FROM python:3.14-slim@sha256:6a27522252aef8432841f224d9baaa6e9fce07b07584154fa0b9a96603af7456

# Install ffmpeg for audio processing and rsgain for ReplayGain tagging
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg jq curl rsgain && \
    rm -rf /var/lib/apt/lists/*

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /usr/local/bin/uv

# Create non-root user for security
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} musicload && \
    useradd -u ${UID} -g ${GID} -m -s /bin/bash musicload

WORKDIR /app

# Copy project files
COPY README.md pyproject.toml uv.lock ./
COPY musicload/ ./musicload/

# Install dependencies
RUN uv sync --frozen

# Create downloads directory and set permissions
RUN mkdir -p /downloads /app/data && \
    chown -R musicload:musicload /app /downloads

ENV MUSICLOAD_DOWNLOAD_DIR=/downloads
ENV MUSICLOAD_WEB_PORT=8000
ENV MUSICLOAD_WEB_PLAYLIST=web-downloads
ENV MUSICLOAD_REPLAYGAIN=false

# Switch to non-root user
USER musicload

EXPOSE 8000

# Run the web server
CMD ["uv", "run", "musicload", "web", "--host", "0.0.0.0"]
