FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# yt-dlp uses a JavaScript runtime to solve YouTube's current EJS challenges.
FROM denoland/deno:bin-2.9.7@sha256:bc5aa4466e21b6d3021226a85ba2e1911f7c386254d97b9d797903ab74edace2 AS deno

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS builder

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
COPY README.md pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY musicload/ ./musicload/

# Build an isolated runtime environment. uv and build caches stay in this stage.
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime

COPY --from=deno /deno /usr/local/bin/deno

# ffmpeg handles audio; tzdata supports per-user automatic download times;
# gosu lets the entrypoint drop from root to the musicload user at runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg tzdata gosu && \
    rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} musicload && \
    useradd -u ${UID} -g ${GID} -m -s /usr/sbin/nologin musicload && \
    mkdir -p /app /data /downloads && \
    chown -R musicload:musicload /app /data /downloads

WORKDIR /app
COPY --from=builder --chown=musicload:musicload /app/.venv /app/.venv
COPY --chmod=755 entrypoint.sh /entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENV MUSICLOAD_DOWNLOAD_DIR=/downloads
ENV MUSICLOAD_DATA_DIR=/data
ENV MUSICLOAD_WEB_PORT=8000

# Container starts as root so the entrypoint can fix ownership of
# bind-mounted /data and /downloads, then it drops to musicload itself.
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

# Run the web server without shipping the package manager in the final image.
CMD ["musicload", "web", "--host", "0.0.0.0"]
