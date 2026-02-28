#!/bin/sh
# docker-entrypoint.sh — arranca uvicorn con parámetros production-ready
# Ejecutado como appuser (UID 1001), PID 1 mantenido por tini.
set -e

exec uvicorn src.api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers "${WORKERS:-2}" \
    --proxy-headers \
    --forwarded-allow-ips="*" \
    --timeout-graceful-shutdown 30 \
    --log-level info
