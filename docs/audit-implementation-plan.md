# Plan de Implementacion - LLM Audit + Observability v3

Basado en el blueprint de `audit-logging-plan.md`. Este documento traduce la arquitectura target en fases concretas de codigo.

---

## Estructura Final de Modulos

```
src/
├── audit/
│   ├── __init__.py                # Re-exports: emit, audit_span, TraceContext
│   ├── models.py                  # AuditEvent, ErrorDetail, AuditEventType (Pydantic)
│   ├── context.py                 # TraceContext (contextvars, W3C traceparent)
│   ├── logger.py                  # AuditLogger (singleton, sampling, routing a capas)
│   ├── sampler.py                 # SamplingPolicy (decide si loguear o no)
│   ├── sanitizer.py               # Sanitize PII antes de persistir
│   ├── decorators.py              # @audit_span (sync + async)
│   ├── middleware.py              # FastAPI middleware (trace_id, request/response)
│   ├── replay.py                  # ReplayFixture (extrae snapshots para tests)
│   ├── cost.py                    # Calculadora de costo por modelo/tokens
│   │
│   └── exporters/
│       ├── __init__.py
│       ├── base.py                # AuditExporter Protocol
│       ├── stdout.py              # Dev: print estructurado al terminal
│       ├── jsonfile.py            # Default: escribe JSONL rotado por dia
│       ├── cloudwatch.py          # Prod: envia a CloudWatch Logs
│       └── async_pipeline.py      # Cola async + worker + flush + resilience
│
├── metrics/
│   ├── __init__.py
│   ├── collector.py               # MetricsCollector (counters, histograms, gauges)
│   └── otel.py                    # OpenTelemetry meter provider (exporta a OTLP)
│
├── config.py                      # + AUDIT_*, METRICS_*, SAMPLING_* env vars
└── api/
    └── main.py                    # + AuditMiddleware + /health con metricas
```

---

## Fases de Implementacion

### Fase 1: Core - Modelos + Contexto + Logger

**Objetivo**: Tener la base para emitir eventos sin romper nada existente.

**Archivos a crear**:

#### 1.1 `src/audit/models.py`

```python
class AuditEventType(str, Enum):
    REQUEST_START = "request.start"
    REQUEST_END = "request.end"
    AGENT_ROUTE = "agent.route"
    AGENT_DECISION = "agent.decision"
    AGENT_RETRY = "agent.retry"
    AGENT_FALLBACK = "agent.fallback"
    LLM_INVOKE = "llm.invoke"
    LLM_ERROR = "llm.error"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    NLP_EXTRACT = "nlp.extract"
    CACHE_HIT = "cache.hit"
    CACHE_MISS = "cache.miss"
    API_CALL = "api.call"
    S3_WRITE = "s3.write"
    PUSH_SEND = "push.send"
    SERIALIZE = "serialize"
    PROMPT_EFFICIENCY = "prompt.efficiency"

class ErrorDetail(BaseModel):
    type: str
    message: str
    traceback: str | None = None
    recoverable: bool = True
    input_snapshot: dict | None = None
    environment: dict | None = None    # model_version, tool_versions, python_version

class AuditEvent(BaseModel):
    event_version: str = "3.0"
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    timestamp: datetime
    event_type: AuditEventType
    agent: str
    action: str
    status: Literal["ok", "error", "timeout", "retry"]
    latency_ms: float | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    data: dict = {}
    error: ErrorDetail | None = None
```

#### 1.2 `src/audit/context.py`

Propagacion async-safe con soporte W3C traceparent:

```python
_trace_id: ContextVar[str]
_span_id: ContextVar[str]
_parent_span_id: ContextVar[str | None]

class TraceContext:
    @staticmethod
    def new_trace() -> str                    # uuid hex[:12], set en contextvar

    @staticmethod
    def new_span() -> str                     # uuid hex[:8], guarda parent

    @staticmethod
    def get_trace_id() -> str

    @staticmethod
    def get_span_id() -> str

    @staticmethod
    def get_parent_span_id() -> str | None

    @staticmethod
    def to_traceparent() -> str               # "00-{trace}-{span}-01" (W3C)

    @staticmethod
    def from_traceparent(header: str) -> None # Parsea W3C traceparent entrante
```

#### 1.3 `src/audit/logger.py`

Singleton que recibe eventos, aplica sampling + sanitizacion, y los despacha:

```python
class AuditLogger:
    _instance: ClassVar[AuditLogger | None] = None
    _pipeline: AsyncPipeline
    _sampler: SamplingPolicy
    _sanitizer: Sanitizer

    @classmethod
    def get(cls) -> AuditLogger

    def emit(self, event: AuditEvent) -> None      # Non-blocking, enqueue

    async def flush(self) -> None                   # Drain queue

    async def shutdown(self) -> None                # Flush + close exporters
```

**Verificacion Fase 1**: Crear test que emita un AuditEvent, verifique que se serializó como JSONL, y que el trace_id se propaga entre spans.

---

### Fase 2: Sanitizacion + Sampling

**Objetivo**: Nunca almacenar PII. No loguear todo en produccion.

#### 2.1 `src/audit/sanitizer.py`

```python
# Patrones a redactar
PATTERNS = {
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    "jwt": r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+",
    "aws_key": r"AKIA[0-9A-Z]{16}",
}

SENSITIVE_KEYS = {"password", "secret", "token", "api_key", "authorization", "cookie"}

def sanitize(data: dict) -> dict:
    """Deep-clone + redact patterns + mask sensitive keys."""
```

Reglas:
- Valores de keys en `SENSITIVE_KEYS` -> `"[REDACTED]"`
- Strings que matchean `PATTERNS` -> reemplazados con `"[REDACTED_{tipo}]"`
- Recursivo para dicts y listas anidadas
- Nunca muta el input original

#### 2.2 `src/audit/sampler.py`

```python
class SamplingPolicy:
    def __init__(self,
        trace_rate: float = 0.20,       # TRACE_SAMPLE_RATE
        success_rate: float = 0.10,     # SUCCESS_SAMPLE_RATE
        error_rate: float = 1.00,       # ERROR_SAMPLE_RATE (siempre)
        slow_threshold_ms: float = 1500 # SLOW_REQUEST_THRESHOLD_MS
    ): ...

    def should_record(self, event: AuditEvent) -> bool:
        if event.status in ("error", "timeout"):
            return True                          # Siempre
        if event.latency_ms and event.latency_ms > self.slow_threshold_ms:
            return True                          # Siempre si lento
        if os.getenv("AUDIT_DEBUG") == "true":
            return True                          # Full logging en debug
        return random.random() < self.success_rate
```

**Verificacion Fase 2**: Test que sanitize redacta emails/cards/keys. Test que sampler deja pasar 100% de errores y ~10% de success.

---

### Fase 3: Pipeline Async + Exporters

**Objetivo**: Nunca bloquear un request por logging.

#### 3.1 `src/audit/exporters/async_pipeline.py`

```python
class AsyncPipeline:
    """Cola async con worker de fondo, buffer y resilience."""

    def __init__(self, exporters: list[AuditExporter], max_queue: int = 10_000):
        self._queue: asyncio.Queue[AuditEvent]
        self._exporters: list[AuditExporter]
        self._worker_task: asyncio.Task | None

    def enqueue(self, event: AuditEvent) -> None:
        """Non-blocking. Si la cola esta llena, drop oldest."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._queue.get_nowait()   # Drop oldest
            self._queue.put_nowait(event)

    async def _worker(self) -> None:
        """Loop infinito que consume la cola y exporta."""
        while True:
            event = await self._queue.get()
            for exporter in self._exporters:
                try:
                    await exporter.export(event)
                except Exception:
                    pass  # Exporter fallo, no bloquear

    async def start(self) -> None
    async def flush(self) -> None      # Drain completo
    async def shutdown(self) -> None   # Flush + cancel worker
```

#### 3.2 Exporters

| Exporter | Archivo | Logica |
|----------|---------|--------|
| `StdoutExporter` | `stdout.py` | `print(event.model_dump_json())` coloreado por status |
| `JsonFileExporter` | `jsonfile.py` | Append a `logs/audit-YYYY-MM-DD.jsonl`, rotacion diaria |
| `CloudWatchExporter` | `cloudwatch.py` | `boto3 logs.put_log_events()` con batch y retry exponencial |

#### 3.3 Configuracion en `src/config.py`

```python
# Audit
AUDIT_ENABLED = os.getenv("AUDIT_ENABLED", "true").lower() == "true"
AUDIT_EXPORTER = os.getenv("AUDIT_EXPORTER", "jsonfile")  # stdout | jsonfile | cloudwatch
AUDIT_LOG_DIR = os.getenv("AUDIT_LOG_DIR", "logs")
AUDIT_DEBUG = os.getenv("AUDIT_DEBUG", "false").lower() == "true"
AUDIT_INCLUDE_SNAPSHOTS = os.getenv("AUDIT_INCLUDE_SNAPSHOTS", "true").lower() == "true"

# Sampling
TRACE_SAMPLE_RATE = float(os.getenv("TRACE_SAMPLE_RATE", "0.20"))
SUCCESS_SAMPLE_RATE = float(os.getenv("SUCCESS_SAMPLE_RATE", "0.10"))
ERROR_SAMPLE_RATE = float(os.getenv("ERROR_SAMPLE_RATE", "1.00"))
SLOW_REQUEST_THRESHOLD_MS = float(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "1500"))
```

**Verificacion Fase 3**: Test que emite 100 eventos, verifica que JSONL tiene lineas, que cola no bloquea, que drop funciona cuando queue esta llena.

---

### Fase 4: Decorators + Middleware

**Objetivo**: Instrumentar funciones con una linea. Trazar requests HTTP.

#### 4.1 `src/audit/decorators.py`

```python
def audit_span(agent: str, action: str, capture_result: bool = False):
    """Decorator para sync y async. Emite TOOL_CALL/TOOL_RESULT automaticamente."""
    # - Crea span hijo
    # - Mide latencia con time.perf_counter()
    # - En ok: emite evento con data (opcionalmente el resultado)
    # - En error: emite evento con ErrorDetail + input_snapshot
    # - Soporta @audit_span("benefits", "tool.search") en async def
```

#### 4.2 `src/audit/middleware.py`

```python
class AuditMiddleware:
    """Middleware FastAPI. Crea trace por request, emite start/end."""

    async def __call__(self, request, call_next):
        # 1. Leer traceparent header (W3C) si existe, sino crear trace nuevo
        # 2. Emitir REQUEST_START
        # 3. Ejecutar request
        # 4. Emitir REQUEST_END con status_code + latency
        # 5. Agregar X-Trace-ID y traceparent al response
```

**Verificacion Fase 4**: Test con TestClient de FastAPI que verifica X-Trace-ID en response, y que REQUEST_START + REQUEST_END se emiten.

---

### Fase 5: Cost Calculator + AI Telemetry

**Objetivo**: Saber cuanto cuesta cada request y cada decision del agente.

#### 5.1 `src/audit/cost.py`

```python
# Precios por 1K tokens (actualizar periodicamente)
MODEL_PRICING = {
    "anthropic.claude-3-haiku-20240307-v1:0": {
        "input": 0.00025,    # $0.25 per 1M input
        "output": 0.00125,   # $1.25 per 1M output
    },
    # Agregar mas modelos...
}

def calculate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Retorna costo en USD."""
```

#### 5.2 Integracion en benefits_agent.py

Despues de cada `llm.ainvoke()`, extraer token usage del response metadata:

```python
# LangChain ChatBedrock devuelve usage en response.response_metadata
usage = response.response_metadata.get("usage", {})
input_tokens = usage.get("input_tokens", 0)
output_tokens = usage.get("output_tokens", 0)
cost = calculate_cost(BEDROCK_MODEL_ID, input_tokens, output_tokens)

AuditLogger.get().emit(AuditEvent(
    event_type=AuditEventType.LLM_INVOKE,
    tokens_input=input_tokens,
    tokens_output=output_tokens,
    cost_usd=cost,
    ...
))
```

**Verificacion Fase 5**: Test que verifica calculo de costo con tokens conocidos.

---

### Fase 6: Metricas (Counters, Histograms, Gauges)

**Objetivo**: Monitoreo cuantitativo para alertas y dashboards.

#### 6.1 `src/metrics/collector.py`

```python
class MetricsCollector:
    """Colector in-memory que puede exportarse a OpenTelemetry o CloudWatch."""

    # Counters
    requests_total: Counter
    llm_calls_total: Counter
    tool_calls_total: Counter
    errors_total: Counter
    retries_total: Counter

    # Histograms
    request_latency: Histogram
    llm_latency: Histogram
    tool_latency: Histogram

    # Gauges
    active_traces: Gauge
    queue_size: Gauge

    def record_request(self, latency_ms: float, status: str) -> None
    def record_llm_call(self, latency_ms: float, model: str, tokens: int) -> None
    def record_tool_call(self, latency_ms: float, tool: str, status: str) -> None
```

#### 6.2 `src/metrics/otel.py` (opcional)

Conecta el collector con OpenTelemetry SDK para exportar via OTLP:

```python
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
```

Esto es opcional — se activa si `OTEL_EXPORTER_OTLP_ENDPOINT` esta configurado.

**Verificacion Fase 6**: Test que registra metricas y verifica valores. Endpoint `/metrics` o `/health` que expone contadores basicos.

---

### Fase 7: Replay Deterministico

**Objetivo**: Reproducir cualquier error en testing.

#### 7.1 `src/audit/replay.py`

```python
class ReplayFixture:
    @staticmethod
    def errors_from_jsonl(path: str, action: str | None = None) -> list[dict]:
        """Extrae todos los error snapshots de un JSONL."""

    @staticmethod
    def successful_result(path: str, action: str) -> dict | None:
        """Extrae un resultado exitoso para usar como mock."""

    @staticmethod
    def full_trace(path: str, trace_id: str) -> list[AuditEvent]:
        """Reconstruye un trace completo."""
```

Cada error snapshot incluye:
- `input_snapshot`: args exactos de la funcion que fallo
- `environment`: `{"model": "claude-3-haiku", "python": "3.11", "toon": "0.5.3"}`
- `traceback`: stack trace completo

**Verificacion Fase 7**: Test que carga un JSONL fixture, extrae un snapshot, y lo ejecuta con mocks.

---

### Fase 8: Instrumentar el Codigo Existente

**Objetivo**: Conectar el sistema de audit a los agentes, tools y servicios.

| Archivo | Cambio | Evento emitido |
|---------|--------|---------------|
| `src/api/main.py` | Agregar `AuditMiddleware` al app | `request.start`, `request.end` |
| `src/agents/supervisor_agent.py` | Emitir despues de `ainvoke` | `agent.route` con decision |
| `src/agents/benefits_agent.py` | Emitir en loop de tools y LLM | `tool.call`, `tool.result`, `llm.invoke` |
| `src/tools/benefits_api.py` | `@audit_span` en `search_benefits_async` | `tool.call` |
| `src/tools/benefits_api.py` | `@audit_span` en `fetch_benefits` | `api.call` |
| `src/tools/nlp_processor.py` | `@audit_span` en `nlp_pipeline` | `nlp.extract` |
| `src/cache/cache_service.py` | Emitir en `get`/`set` | `cache.hit`, `cache.miss` |
| `src/tools/s3_unhandled_queries.py` | `@audit_span` en `save_unhandled_query` | `s3.write` |
| `src/tools/push_notifications.py` | `@audit_span` en `send_push_notification` | `push.send` |

**Los print() existentes se eliminan gradualmente** a medida que cada punto se instrumenta.

---

### Fase 9: Alertas + SLOs

**Objetivo**: Observabilidad sin alertas = logging pasivo.

Implementar como checks periodicos en el MetricsCollector:

```python
class AlertEngine:
    rules = [
        AlertRule("error_rate > 5%",    metric="errors_total / requests_total"),
        AlertRule("p95_latency > 1500",  metric="request_latency.p95"),
        AlertRule("token_cost_spike",    metric="cost_usd.sum.1h > threshold"),
        AlertRule("tool_failure > 3%",   metric="tool_errors / tool_calls_total"),
        AlertRule("retries > 2/req",     metric="retries_total / requests_total"),
    ]

    async def check(self) -> list[Alert]   # Evaluado cada 60s
    async def notify(self, alert: Alert)   # Envia via push_notifications o SNS
```

SLOs iniciales:

| Metrica | Target |
|---------|--------|
| p95 latency | < 1500 ms |
| Error rate | < 2% |
| Timeout rate | < 1% |
| Tool failure | < 3% |
| Retry ratio | < 5% |

---

## Orden de PRs

| PR | Fase | Archivos | Dependencias |
|----|------|----------|-------------|
| **PR 1** | Fase 1 + 2 | models, context, logger, sampler, sanitizer | Ninguna |
| **PR 2** | Fase 3 | async_pipeline, exporters (stdout, jsonfile) | PR 1 |
| **PR 3** | Fase 4 | decorators, middleware | PR 2 |
| **PR 4** | Fase 5 | cost.py, integracion en benefits_agent | PR 3 |
| **PR 5** | Fase 6 | metrics/collector, metrics/otel | PR 3 |
| **PR 6** | Fase 7 | replay.py + test fixtures | PR 2 |
| **PR 7** | Fase 8 | Instrumentar todos los archivos existentes | PR 3 + 4 |
| **PR 8** | Fase 9 | AlertEngine + SLO checks | PR 5 + 7 |
| **PR 9** | Cleanup | Eliminar todos los print() restantes | PR 7 |

Cada PR es deployable independientemente. Ningun PR rompe funcionalidad existente.

---

## Dependencias Nuevas

```
# requirements.txt (agregar)
opentelemetry-api>=1.20.0           # Solo si se usa OTLP export
opentelemetry-sdk>=1.20.0           # Solo si se usa OTLP export
opentelemetry-exporter-otlp>=1.20.0 # Solo si se usa OTLP export
```

Las fases 1-8 no requieren dependencias nuevas — usan solo stdlib + pydantic (ya instalado).

OpenTelemetry es opcional y solo se instala si se activa el exporter OTLP.

---

## Variables de Entorno Nuevas

```bash
# Audit core
AUDIT_ENABLED=true                    # Activar/desactivar todo el sistema
AUDIT_EXPORTER=jsonfile               # stdout | jsonfile | cloudwatch
AUDIT_LOG_DIR=logs                    # Directorio para JSONL
AUDIT_DEBUG=false                     # true = full logging (sin sampling)
AUDIT_INCLUDE_SNAPSHOTS=true          # Guardar input_snapshot en errores

# Sampling
TRACE_SAMPLE_RATE=0.20               # % de traces completos logueados
SUCCESS_SAMPLE_RATE=0.10              # % de eventos exitosos logueados
ERROR_SAMPLE_RATE=1.00                # % de errores logueados (siempre 100%)
SLOW_REQUEST_THRESHOLD_MS=1500        # Threshold para loguear siempre

# Metrics (opcional)
OTEL_EXPORTER_OTLP_ENDPOINT=         # Si se configura, activa OpenTelemetry
```

---

## Checklist de Produccion

Antes de deploy de cada fase:

- [ ] Tests pasan (unitarios + integracion)
- [ ] Sanitizacion activa (no hay PII en logs)
- [ ] Exporter no bloquea requests (async pipeline)
- [ ] Sampling configurado (no loguear 100% en prod)
- [ ] Event version presente en todos los eventos
- [ ] JSONL se puede parsear con jq
- [ ] Replay funciona con snapshots reales
