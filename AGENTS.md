# AGENTS.md

Fork of [jtauschl/openproject-ce-mcp](https://github.com/jtauschl/openproject-ce-mcp).
It adds one feature upstream does not have: `openproject-ce-mcp serve`, the MCP
server over authenticated Streamable HTTP. This file tells an agent how to keep
that working. Upstream's own rules are in `CONTRIBUTING.md`.

This repository is public. Never commit secrets, `stack.env`, compose files from
the server, hostnames, usernames, stack IDs or public endpoint URLs.

## Branches

| Ref | What it is |
| --- | --- |
| `http-hosting` | Our work, rebased on upstream `release/0.5.0`. Work here. |
| `main` | Plain copy of upstream `main`. Never commit to it. |
| tag `deployed-2026-09-22` | Source of the image that ran until 2026-10-30, based on `5ea2452`. History only. |

Remotes: `origin` is this fork, `upstream` is jtauschl. A fresh clone needs
`git remote add upstream https://github.com/jtauschl/openproject-ce-mcp.git`.

What this fork adds, so conflicts are easy to recognise:

- `src/openproject_ce_mcp/http_server.py`
- the `serve` subcommand and `http` argument in `src/openproject_ce_mcp/server.py`
- `starlette` and `uvicorn` in `pyproject.toml`, and the matching `uv.lock` change
- `docker/http/Dockerfile`
- wording about HTTP in `README.md` and `docs/installation.md`
- this file

## Pull in upstream changes

```bash
git fetch upstream
git switch http-hosting
git rebase upstream/release/0.5.0
uv lock
uv sync --all-extras --all-groups
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q   # about 9 minutes
git push --force-with-lease origin http-hosting
```

To update `main`: `git push origin upstream/main:main`.

If `release/0.5.0` no longer exists, `CONTRIBUTING.md` names the current
development branch. Rebase onto that one.

There are no automated tests for `http_server.py`. After changing it, start
`serve` locally and check: no or wrong token gives 401, wrong `Host` gives 421,
and `initialize` plus `tools/list` work with the right token.

## Where it runs

A Portainer stack on a private host. Host, user, stack ID, compose project and
data directory are in the private repository
`frihetsbauta-drift/openproject-ops`, as values for the placeholders used below.

| Service | Image |
| --- | --- |
| `openproject-web-v17`, `-worker-v17`, `-cron-v17`, `-seeder-v17` | `openproject/openproject:17.9.5-slim-bim` |
| `openproject-mcp-v17` | `openproject-ce-mcp:http-hosting`, built locally, `pull_policy: never` |
| `openproject-db-v17` | `postgres:17` |
| `openproject-cache-v17` | `memcached:1.6-alpine` |

Data is in `<data-dir>/{assets,pgdata}`. `assets` must be owned by
`1000:1000`, the app user in the slim images. Attachments and IFC uploads fail
otherwise.

The stack files belong to Portainer and are root-only on the host. Read and write
them through the container, and keep them in step with what runs. A Portainer
redeploy uses that copy, so a stale file downgrades the stack:

```bash
docker cp portainer:/data/compose/<stack-id>/docker-compose.yml - | tar -xO > docker-compose.yml
docker cp portainer:/data/compose/<stack-id>/stack.env - | tar -xO > stack.env
chmod 600 docker-compose.yml stack.env
```

Keep those copies in a private directory on the server. Delete them when done or
keep them with the backups, never in this repository.

`stack.env` must be passed to compose. `OPENPROJECT_SECRET_KEY_BASE` contains a
`$`, so compose warns about an unset variable. That is expected, do not "fix" it:
changing the rendered value invalidates sessions and encrypted data.

```bash
C="docker compose -p <compose-project> --project-directory $PWD --env-file $PWD/stack.env -f $PWD/docker-compose.yml"
```

## Rules for the stack

- Do not change infrastructure: no new services, networks, proxy hosts or
  hostnames. Change `image:` lines only unless the owner asks for more.
- The stack has no hocuspocus container. That is deliberate.
- Run `$C up -d --dry-run` first. `db` and `cache` must not be recreated.
- Keep OpenProject pinned to an exact `X.Y.Z-slim-bim` tag. Plain `-slim` has no
  IFC pipeline, and moving tags change on any restart.

## Deploy a new MCP image

```bash
docker build -f docker/http/Dockerfile -t openproject-ce-mcp:http-hosting \
  'https://github.com/frihetsbauta-drift/openproject-ce-mcp.git#http-hosting'
$C up -d --no-deps --force-recreate openproject-mcp-v17
```

Keep the tag, so the stack file does not change.

Check it from inside the container, which keeps the bearer token out of logs:
POST `initialize`, `tools/list` and a read tool such as `list_projects` to
`http://127.0.0.1:8000/mcp` with `OPENPROJECT_MCP_HTTP_TOKEN` from its environment.
The Python standard library is enough. `curl` is not in the image.

## Upgrade OpenProject

Within major 17 no intermediate version is needed. For a new major, read the
upgrade notes first: going from X to X+2 directly is not supported, and PostgreSQL
may need its own upgrade.

1. Check the tag exists for amd64, then `docker pull` it.
2. Change the four `image:` lines in a private copy of `docker-compose.yml`.
3. `docker stop openproject-web-v17 openproject-worker-v17 openproject-cron-v17`
4. Back up to `~/backups/openproject-<date>/`: `pg_dump -Fc` from the db container,
   plus a tar of the assets. Confirm the dump lists with `pg_restore -l`.
5. `$C up -d openproject-seeder-v17`, then `docker wait openproject-seeder-v17`.
   The seeder runs migrations and fills in missing default data. It is safe to
   rerun and must exit 0.
6. `docker cp docker-compose.yml portainer:/data/compose/<stack-id>/docker-compose.yml`
7. `$C up -d --no-deps openproject-web-v17 openproject-worker-v17 openproject-cron-v17 openproject-mcp-v17`
8. Wait for `healthy`. Check the version and that the row counts match the backup.

Rollback: migrations cannot be undone. Restore the dump into an empty database and
go back to the previous image.

## Known gaps

- `docs/http-hosting.md` does not exist. The original was never recovered.
- `http_server.py` has no automated tests.
- `serve` uses one OpenProject identity for all clients. It is not OAuth.
