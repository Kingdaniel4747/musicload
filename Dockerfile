FROM ghcr.io/astral-sh/uv:latest@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 AS uv

FROM python:3.14-slim@sha256:6a27522252aef8432841f224d9baaa6e9fce07b07584154fa0b9a96603af7456 AS builder

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
COPY README.md pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY musicload/ ./musicload/

# Build an isolated runtime environment. uv and build caches stay in this stage.
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:6a27522252aef8432841f224d9baaa6e9fce07b07584154fa0b9a96603af7456 AS runtime

# ffmpeg handles audio; tzdata supports per-user automatic download times;
# gosu drops root after bind-mount permissions have been repaired.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg gosu tzdata && \
    rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} musicload && \
    useradd -u ${UID} -g ${GID} -m -s /usr/sbin/nologin musicload && \
    mkdir -p /app /data /downloads && \
    chown -R musicload:musicload /app /data /downloads

WORKDIR /app
COPY --from=builder --chown=musicload:musicload /app/.venv /app/.venv
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENV MUSICLOAD_DOWNLOAD_DIR=/downloads
ENV MUSICLOAD_DATA_DIR=/data
ENV MUSICLOAD_WEB_PORT=8000

EXPOSE 8000

# Repair bind-mount ownership as root, then run the server as musicload.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["musicload", "web", "--host", "0.0.0.0"]
