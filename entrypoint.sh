#!/bin/sh
set -e

# /data and /downloads are usually bind-mounted from the host. Docker
# creates them as root on first run, and the ownership baked into the
# image at build time has no effect on a bind mount - so the
# unprivileged "musicload" user can end up unable to write to them
# (e.g. PermissionError on /data/logs). Fix that here, once per start,
# then drop from root down to the musicload user before running the app.

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data /downloads
    chown musicload:musicload /downloads
    chown -R musicload:musicload /data
    exec gosu musicload "$@"
fi

exec "$@"
