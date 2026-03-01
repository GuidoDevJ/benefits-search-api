#!/bin/sh
# docker-entrypoint.sh — arranca uvicorn con parámetros production-ready.
# Ejecutado como appuser (UID 1001), PID 1 mantenido por tini.
#
# IMPORTANTE — WORKERS debe ser 1 mientras Gradio esté montado.
# Gradio usa WebSockets con estado en memoria; múltiples workers rompen
# las conexiones al no compartir ese estado.
# Para escalar horizontalmente usá múltiples tasks ECS + sticky sessions en ALB.
set -e

exec uvicorn src.api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers "${WORKERS:-1}" \
    --proxy-headers \
    --forwarded-allow-ips="*" \
    --timeout-graceful-shutdown 30 \
    --log-level info
