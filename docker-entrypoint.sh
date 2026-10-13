#!/bin/sh
# Ajusta el dueño del volumen de datos y baja privilegios antes de ejecutar la API.
set -e

if [ "$(id -u)" = "0" ]; then
  chown -R app:app /data 2>/dev/null || true
  exec gosu app "$@"
fi

exec "$@"
