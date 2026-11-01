FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv

# yt-dlp uses a JavaScript runtime to solve YouTube's current EJS challenges.
FROM denoland/deno:bin-2.9.7@sha256:bc5aa4466e21b6d3021226a85ba2e1911f7c386254d97b9d797903ab74edace2 AS deno

FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913 AS builder

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
COPY README.md pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY musicload/ ./musicload/

# Build an isolated runtime environment. uv and build caches stay in this stage.
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913 AS runtime

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
