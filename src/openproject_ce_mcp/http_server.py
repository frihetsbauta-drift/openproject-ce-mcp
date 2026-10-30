"""Opt-in, single-identity Streamable HTTP hosting.

MCP framing, routing and lifespan remain owned by the SDK. The deployment token
protects this endpoint only; it is never forwarded to OpenProject and does not
select an OpenProject user. This is static bearer authentication, not OAuth.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from hmac import compare_digest
from urllib.parse import urlsplit

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import ConfigError
from .strict_mcpserver import StrictMCPServer

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_TOKEN_ENV = "OPENPROJECT_MCP_HTTP_TOKEN"


@dataclass(frozen=True, slots=True)
class HttpSettings:
    token: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 8000
    public_url: str | None = None

    def __post_init__(self) -> None:
        # RFC 6750 bearer-token alphabet. Length alone does not ensure entropy;
        # the deployment guide generates a random token rather than a password.
        if len(self.token) < 32 or re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", self.token) is None:
            raise ConfigError(f"{_TOKEN_ENV} must contain at least 32 characters from the bearer-token alphabet")
        if not self.host or any(char.isspace() for char in self.host):
            raise ConfigError("HTTP host must be non-empty and contain no whitespace")
        if not 1 <= self.port <= 65535:
            raise ConfigError("HTTP port must be between 1 and 65535")
        if self.public_url is None:
            if self.host not in _LOOPBACK_HOSTS:
                raise ConfigError("--public-url is required when binding HTTP to a non-loopback host")
            return
        if any(char.isspace() or char in "\\*" for char in self.public_url):
            raise ConfigError("--public-url must be an HTTP(S) origin without whitespace or wildcards")
        try:
            url = urlsplit(self.public_url)
            port = url.port
        except ValueError as exc:
            raise ConfigError("--public-url must be a valid HTTP(S) origin") from exc
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.netloc.endswith(":")
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ConfigError("--public-url must be an HTTP(S) origin without credentials, path, query or fragment")
        if url.scheme != "https" and url.hostname not in _LOOPBACK_HOSTS:
            raise ConfigError("--public-url must use HTTPS unless its host is loopback")

    @classmethod
    def from_env(cls, *, host: str, port: int, public_url: str | None) -> HttpSettings:
        token = os.environ.get(_TOKEN_ENV, "")
        if not token:
            raise ConfigError(f"{_TOKEN_ENV} is required for HTTP hosting")
        return cls(token=token, host=host, port=port, public_url=public_url)

    def transport_security(self) -> TransportSecuritySettings:
        # Keep DNS-rebinding checks enabled even for 0.0.0.0/container binds.
        loopback = ["127.0.0.1", "localhost", "[::1]"]
        hosts = [f"{host}:{self.port}" for host in loopback]
        if self.port == 80:
            hosts.extend(loopback)
        origins = [f"http://{host}" for host in hosts]
        if self.public_url:
            url = urlsplit(self.public_url)
            authorities = {url.netloc.lower()}
            default_port = 443 if url.scheme == "https" else 80
            if url.port in {None, default_port}:
                hostname = url.hostname or ""
                bare_host = f"[{hostname}]" if ":" in hostname else hostname
                authorities.update({bare_host, f"{bare_host}:{default_port}"})
            hosts.extend(sorted(authorities))
            origins.extend(f"{url.scheme}://{authority}" for authority in sorted(authorities))
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        )


class BearerTokenMiddleware:
    """Require the deployment token on every HTTP method, before MCP dispatch."""

    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self.app = app
        self._token = token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # In particular, pass lifespan through so the SDK initializes and
            # closes the OpenProject client once for the server's lifetime.
            await self.app(scope, receive, send)
            return
        headers = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
        if len(headers) == 1:
            scheme, separator, token = headers[0].partition(b" ")
            if separator and scheme.lower() == b"bearer" and compare_digest(token, self._token):
                await self.app(scope, receive, send)
                return
        await JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="openproject-ce-mcp"', "Cache-Control": "no-store"},
        )(scope, receive, send)


def create_http_app(mcp: StrictMCPServer, settings: HttpSettings) -> Starlette:
    app = mcp.streamable_http_app(
        host=settings.host,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=settings.transport_security(),
    )
    app.add_middleware(BearerTokenMiddleware, token=settings.token)
    return app


def run_http_server(mcp: StrictMCPServer, settings: HttpSettings) -> None:
    uvicorn.run(
        create_http_app(mcp, settings),
        host=settings.host,
        port=settings.port,
        log_level=mcp.settings.log_level.lower(),
        # The reverse proxy must preserve Host. Do not implicitly trust
        # forwarded client addresses/schemes or log user-supplied query strings.
        proxy_headers=False,
        access_log=False,
    )
