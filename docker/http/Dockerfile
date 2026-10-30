# Image for `openproject-ce-mcp serve`. Build from the repository root:
#   docker build -f docker/http/Dockerfile -t openproject-ce-mcp:http-hosting .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --uid 10001 --create-home mcp

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["openproject-ce-mcp"]
CMD ["serve"]
