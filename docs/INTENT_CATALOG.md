# Intent Catalog — Detección de intents out-of-scope

## Contexto

El bot de beneficios Comafi actualmente solo maneja consultas sobre descuentos y beneficios TeVaBien.
Cuando el usuario consulta algo fuera de ese scope (transferencias, saldo, dólares, etc.), el sistema
retorna un mensaje genérico con `intent="unknown"` sin ninguna señal estructurada para el integrador upstream.

Este documento describe la arquitectura para detectar esos intents out-of-scope, identificar qué quiso
hacer el usuario, y devolver el `trigger_text` exacto que debe escribir para activar el flujo correcto.

---

## Estado actual del pipeline

```
query
  │
  ▼
fast_classifier (regex)     → intent: "benefits" | "location" | "ver_mas" | "unknown"
  │
  ▼  (si unknown)
LLM classifier (Bedrock)    → confirma o refina
  │
  ▼  (si sigue unknown)
_UNKNOWN_RESPONSE genérico  → "Solo puedo ayudarte con beneficios..."
```

**Problema:** `intent="unknown"` mezcla dos casos muy distintos:
- Query ambigua que el clasificador no entendió (ej: "hola", "qué?")
- Query bancaria clara fuera de scope (ej: "quiero transferir $5000")

El integrador (Aivo/WhatsApp) no puede distinguirlos ni redirigir al flujo correcto.

---

## Arquitectura objetivo

```
query
  │
  ▼
fast_classifier             → "benefits" | "location" | "ver_mas" | "unknown"
  │
  ▼  (si unknown)
IntentCatalog.detect()      ← NUEVO — in-process, sin LLM, sin red
  ├─ Paso 1: keyword regex  → <1ms  — hit directo si keyword match
  └─ Paso 2: BM25 fallback  → 2-5ms — cubre paráfrasis y sinónimos
       │
       ├─ intent detectado  → exit_intent=true + detected_intent + trigger_text + flow_id
       └─ no detectado      → intent="unknown" genérico (query ambigua)
```

**Total latencia agregada:** <6ms en el peor caso. Sin tokens. Sin red.

---

## Comparativa de enfoques

| Approach         | Latencia | Costo  | Recall    | Complejidad |
|------------------|----------|--------|-----------|-------------|
| Keyword regex    | <1ms     | $0     | Medio     | Baja        |
| BM25 in-process  | 2–5ms    | $0     | Alto      | Media       |
| Embeddings+FAISS | 20–50ms  | $      | Muy alto  | Alta        |
| LLM classifier   | 800–2s   | $$     | Excelente | Baja        |

Para N < 60 intents bancarios, **BM25 domina**: mejor recall que regex puro,
sin infraestructura externa, corre en el mismo proceso FastAPI.

---

## Schema del Excel (fuente de verdad)

El Excel debe tener exactamente estas columnas (nombres exactos, case-sensitive):

| Columna       | Tipo    | Requerido | Descripción                                                        |
|---------------|---------|-----------|--------------------------------------------------------------------|
| `intent_key`  | string  | ✅        | Identificador único snake_case (ej: `transfers`, `dollar_exchange`) |
| `label`       | string  | ✅        | Nombre legible (ej: "Transferencias")                               |
| `keywords`    | string  | ✅        | Palabras clave separadas por coma (ej: "transferir,alias,cvu,cbu") |
| `trigger_text`| string  | ✅        | Frase exacta que activa el flujo (ej: "Quiero hacer una transferencia") |
| `flow_id`     | string  | ✅        | ID del flujo en el sistema destino (ej: "FLOW_TRF_001")            |
| `examples`    | string  | ⬜        | Frases reales del usuario, separadas por `|` — alimentan BM25      |
| `active`      | bool    | ⬜        | `true`/`false` para activar/desactivar sin borrar la fila           |

### Ejemplo de filas

```
intent_key       | label               | keywords                               | trigger_text                     | flow_id      | examples
transfers        | Transferencias      | transferir,enviar dinero,alias,cvu,cbu | Quiero hacer una transferencia   | FLOW_TRF_001 | mandame plata|te paso por alias|haceme una transfe
dollar_exchange  | Compra de dólares   | dólar,comprar dólares,cotización,cepo  | Quiero comprar dólares           | FLOW_USD_001 | a cuánto está el dólar|quiero comprar verdes
account_balance  | Consulta de saldo   | saldo,extracto,movimientos,resumen     | Quiero consultar mi saldo        | FLOW_SAL_001 | cuánto tengo|mis movimientos|ver mi cuenta
card_block       | Bloqueo de tarjeta  | bloquear tarjeta,robo,perdí la tarjeta | Quiero bloquear mi tarjeta       | FLOW_CARD_001| me robaron la tarjeta|perdí el plástico
loans            | Préstamos           | préstamo,crédito,cuota personal        | Quiero solicitar un préstamo     | FLOW_LOAN_001| necesito plata prestada|pedir un crédito
```

> **Nota sobre el PDF:** el PDF de flujos es referencia humana, no fuente de datos del sistema.
> Extraer los intents del PDF manualmente (o con un LLM offline una sola vez) y volcarlos al Excel.
> El runtime solo lee el Excel.

---

## Estructura de archivos

```
src/
  models/
    intent_catalog.py          ← NUEVO: IntentCatalog, Intent, loader Excel, BM25
  services/
    query_orchestrator.py      ← MODIFICAR: integrar detección, enriquecer OrchestratorResult
  api/
    main.py                    ← MODIFICAR: exponer nuevos campos en JSON response
  tools/
    fast_classifier.py         ← MODIFICAR: agregar intent "out_of_scope" como primer filtro
data/
  intents.xlsx                 ← FUENTE DE VERDAD (no commitear datos sensibles)
docs/
  INTENT_CATALOG.md            ← este archivo
```

---

## API response esperada

### Caso: intent out-of-scope detectado

```json
{
  "response": {
    "type": "success",
    "data": {
      "message": "Por ahora solo puedo ayudarte con beneficios y descuentos de tu tarjeta Comafi. Para transferencias, escribí: 'Quiero hacer una transferencia'",
      "session_id": "uuid",
      "exit_intent": true,
      "detected_intent": "transfers",
      "trigger_text": "Quiero hacer una transferencia",
      "flow_id": "FLOW_TRF_001"
    }
  }
}
```

### Caso: query ambigua (sin intent identificado)

```json
{
  "response": {
    "type": "success",
    "data": {
      "message": "Solo puedo ayudarte con descuentos y beneficios de tu tarjeta Comafi...",
      "session_id": "uuid",
      "exit_intent": true,
      "detected_intent": null,
      "trigger_text": null,
      "flow_id": null
    }
  }
}
```

### Caso: consulta de beneficios normal

```json
{
  "response": {
    "type": "success",
    "data": {
      "message": "Encontré 3 beneficios en gastronomía...",
      "session_id": "uuid",
      "exit_intent": false,
      "detected_intent": null,
      "trigger_text": null,
      "flow_id": null
    }
  }
}
```

---

## Cambios en OrchestratorResult

```python
@dataclass
class OrchestratorResult:
    response: str
    session_id: str
    user_profile: Optional[dict] = None
    user_prefs: dict = field(default_factory=dict)
    is_early_exit: bool = False
    total_ms: int = 0
    # Nuevos campos — Task 4
    exit_intent: bool = False
    detected_intent: Optional[str] = None   # intent_key del catálogo
    trigger_text: Optional[str] = None      # texto exacto para activar el flujo
    flow_id: Optional[str] = None           # ID del flujo en sistema destino
```

---

## Implementación: IntentCatalog

### Dependencia nueva

```
rank-bm25==0.2.2   # pure Python, sin deps, ~15KB
```

### Pseudocódigo del módulo

```python
# src/models/intent_catalog.py

@dataclass
class Intent:
    key: str
    label: str
    pattern: re.Pattern       # keywords compilados al cargar
    corpus_tokens: list[str]  # keywords + examples tokenizados para BM25
    trigger_text: str
    flow_id: str

class IntentCatalog:
    BM25_THRESHOLD = 0.4

    def __init__(self, excel_path: str):
        self._intents = _load_from_excel(excel_path)
        self._bm25 = BM25Okapi([i.corpus_tokens for i in self._intents])
        # Cargado una sola vez al inicio del proceso — inmutable en runtime

    def detect(self, query: str) -> Optional[Intent]:
        normalized = _normalize(query)   # lower + strip acentos

        # Paso 1: keyword regex — O(n), <1ms
        for intent in self._intents:
            if intent.pattern.search(normalized):
                return intent

        # Paso 2: BM25 — 2-5ms, cubre paráfrasis
        scores = self._bm25.get_scores(normalized.split())
        best_idx = int(scores.argmax())
        if scores[best_idx] >= self.BM25_THRESHOLD:
            return self._intents[best_idx]

        return None

# Singleton — una instancia por proceso
_catalog: Optional[IntentCatalog] = None

def get_intent_catalog() -> IntentCatalog:
    global _catalog
    if _catalog is None:
        path = os.getenv("INTENT_CATALOG_PATH", "data/intents.xlsx")
        _catalog = IntentCatalog(path)
    return _catalog
```

---

## Puntos de integración en el pipeline

### En `query_orchestrator.py`

```python
# Justo después del bloque intent="unknown" (línea ~596 actual)
if classification.intent == "unknown":
    catalog = get_intent_catalog()
    matched = catalog.detect(query)
    if matched:
        # Out-of-scope identificado
        resp = _build_out_of_scope_message(matched)
        return OrchestratorResult(
            response=resp,
            exit_intent=True,
            detected_intent=matched.key,
            trigger_text=matched.trigger_text,
            flow_id=matched.flow_id,
            ...
        )
    else:
        # Query ambigua genuina
        return OrchestratorResult(
            response=_UNKNOWN_RESPONSE,
            exit_intent=True,
            detected_intent=None,
            ...
        )
```

### Mensaje por intent (no genérico)

```python
def _build_out_of_scope_message(intent: Intent) -> str:
    return (
        f"Por ahora solo puedo ayudarte con beneficios y descuentos de tu tarjeta Comafi. "
        f"Para {intent.label.lower()}, escribí exactamente:\n\n"
        f"👉 \"{intent.trigger_text}\""
    )
```

---

## MVP vs Producción

### MVP (sin BM25)
- Solo keyword regex (Paso 1)
- Dependencia nueva: ninguna
- Tiempo estimado: 2–3 hs
- Suficiente para validar la integración con el sistema upstream

### Producción (con BM25)
- Keyword regex + BM25 fallback
- Dependencia nueva: `rank-bm25==0.2.2`
- Tiempo estimado: +1 hs sobre el MVP
- Cubre paráfrasis, errores ortográficos leves, sinónimos no listados

---

## Variables de entorno necesarias

```env
INTENT_CATALOG_PATH=data/intents.xlsx   # path al Excel (default si no se setea)
INTENT_CATALOG_ENABLED=true             # feature flag para activar/desactivar
BM25_THRESHOLD=0.4                      # ajustar según falsos positivos observados
```

---

## Decisiones de diseño

| Decisión | Alternativa descartada | Razón |
|---|---|---|
| Excel como fuente de verdad | PDF directo | El PDF es prosa, no parseable de forma confiable |
| BM25 in-process | Embeddings externos | Sin latencia de red, sin costo, suficiente para N < 60 intents |
| Singleton cargado al startup | Carga lazy per-request | El Excel no cambia en runtime; carga única amortiza el costo |
| `detected_intent` en la API response | Solo `exit_intent: bool` | El integrador upstream necesita saber a qué flujo redirigir |
| `trigger_text` literal desde Excel | Generar con LLM | Determinístico, el producto define exactamente qué frase activa el flujo |

---

## Pendiente para iniciar implementación

- [ ] Recibir `intents.xlsx` con el schema definido arriba (aunque sea parcial)
- [ ] Confirmar IDs de flujo del sistema destino (Aivo / WhatsApp Business)
- [ ] Confirmar si `trigger_text` es para el usuario final o para el sistema (webhook payload)
- [ ] Decidir MVP (solo keywords) vs Producción (keywords + BM25) para el primer sprint
