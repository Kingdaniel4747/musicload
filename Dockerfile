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

# ffmpeg is the only system package Musicload needs at runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} musicload && \
    useradd -u ${UID} -g ${GID} -m -s /usr/sbin/nologin musicload && \
    mkdir -p /app /downloads && \
    chown -R musicload:musicload /app /downloads

WORKDIR /app
COPY --from=builder --chown=musicload:musicload /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENV MUSICLOAD_DOWNLOAD_DIR=/downloads
ENV MUSICLOAD_WEB_PORT=8000
ENV MUSICLOAD_WEB_PLAYLIST=web-downloads

# Switch to non-root user
USER musicload

EXPOSE 8000

# Run the web server without shipping the package manager in the final image.
CMD ["musicload", "web", "--host", "0.0.0.0"]
