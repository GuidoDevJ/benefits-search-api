# syntax=docker/dockerfile:1

# ============================================
# Stage 1: Builder - Instalar dependencias
# ============================================
FROM python:3.11-slim as builder

WORKDIR /app

# Instalar dependencias de build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements primero para cache de layers
COPY requirements.txt .

# Crear virtualenv e instalar dependencias
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Descargar modelo de spaCy en español
RUN python -m spacy download es_core_news_sm

# ============================================
# Stage 2: Runtime - Imagen final optimizada
# ============================================
FROM python:3.11-slim as runtime

WORKDIR /app

# Copiar virtualenv desde builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Variables de entorno para Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Crear usuario no-root por seguridad
RUN useradd --create-home --shell /bin/bash appuser

# Copiar código fuente
COPY --chown=appuser:appuser src/ ./src/

# Cambiar a usuario no-root
USER appuser

# Exponer puertos (FastAPI: 8000, Gradio: 7860)
EXPOSE 8000 7860

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# Comando por defecto: FastAPI con uvicorn
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
