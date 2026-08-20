#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    mkdir -p /data/logs

    # Docker creates a missing bind-mounted host directory as root. Repair only
    # the small application-data mount; never recursively chown the music tree.
    if ! chown -R musicload:musicload /data; then
        echo "Warning: could not change /data ownership; checking write access." >&2
    fi
    if ! gosu musicload test -w /data; then
        echo "Error: /data is not writable by the musicload user." >&2
        echo "Fix the host directory permissions or mount a writable volume at /data." >&2
        exit 1
    fi

    exec gosu musicload "$@"
fi

exec "$@"
