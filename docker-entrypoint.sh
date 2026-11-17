#!/bin/sh
# Hosts often mount volumes owned by root. When started as root, make /data writable for the app user, then drop
# privileges; the app itself never runs as root.
set -e
mkdir -p /data/objects /data/hf
if [ "$(id -u)" = "0" ]; then
  [ "$(stat -c %u /data)" = "10001" ] || chown -R notiv:notiv /data
  chown notiv:notiv /data/objects /data/hf
  exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
fi
exec "$@"
