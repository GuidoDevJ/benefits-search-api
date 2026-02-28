# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.11

# ============================================================
# Stage 1: Builder — instala deps y descarga modelos
# ============================================================
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /build

# Herramientas de compilación solo en el builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# requirements primero → cache de layer maximizado
COPY requirements.txt .

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Modelo spaCy en español (queda dentro del venv site-packages)
RUN /opt/venv/bin/python -m spacy download es_core_news_sm

# ============================================================
# Stage 2: Runtime — imagen final mínima
# ============================================================
FROM python:${PYTHON_VERSION}-slim AS runtime

# tini: PID 1 correcto en ECS (reenvío de señales, no zombie procs)
# curl: health check del ALB/ECS
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar virtualenv completo desde builder
COPY --from=builder /opt/venv /opt/venv

# PATH del venv primero
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONPATH=/app \
    # Uvicorn — sobreescribibles vía ECS task definition env vars
    PORT=8000 \
    WORKERS=2

# Usuario no-root con UID/GID fijos para auditoría y compatibilidad con EFS
RUN groupadd -r -g 1001 appgroup && \
    useradd  -r -u 1001 -g appgroup --no-create-home --shell /sbin/nologin appuser

# Código fuente y entrypoint
COPY --chown=appuser:appgroup src/               ./src/
COPY --chown=appuser:appgroup docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

USER appuser

EXPOSE ${PORT}

# Health check: llama al endpoint raíz de FastAPI
# start-period largo porque CloudWatch Logs + spaCy tardan en iniciar
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:${PORT}/ || exit 1

# tini como PID 1 → manejo correcto de SIGTERM desde ECS
ENTRYPOINT ["tini", "--"]
CMD ["/app/docker-entrypoint.sh"]
