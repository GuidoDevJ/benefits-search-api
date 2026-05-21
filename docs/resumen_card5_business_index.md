# Card 5 — Business Name Index (Mini-RAG)

## Problema que resuelve

Cuando un usuario pregunta "descuentos en Mostaza" o "promos en La Cantina del Puerto", el sistema anterior fallaba en dos puntos:

1. **fast_classifier no detectaba el negocio** porque `_KNOWN_NEGOCIOS` tenía solo 20 negocios hardcodeados. La query caía al LLM, que a veces extraía el negocio y a veces no.
2. **Búsqueda sin filtro de negocio**: si el LLM no extraía el negocio, se mostraban los TOP 5 de gastronomía en general, ignorando que el usuario quería un comercio específico.
3. **0 resultados por variación**: si el LLM devolvía "mostaza belgrano" pero el campo `b` era "MOSTAZA SRL", el substring exacto fallaba.

---

## Arquitectura de la solución

La solución tiene **tres capas**:

```
Query del usuario
      │
      ▼
[1] fast_classifier.py
    _extract_negocio_candidate()
    → extrae candidato heurístico ("mostaza", "la cantina del puerto")
      si no está en _KNOWN_NEGOCIOS
      │
      ▼
[2] query_orchestrator.py — Step 3.5
    resolve_negocio(candidato) → business_index.py
    → valida el candidato contra el índice Redis
    → retorna el token representativo o descarta el candidato
      │
      ▼
[3] benefits_api.py — _apply_filters
    Filtro substring + fallback token-overlap
    → encuentra el negocio aunque el nombre en TeVaBien sea diferente
```

---

## Capa 1: Extracción heurística en fast_classifier.py

### Patrón Regex

```python
_NEGOCIO_CANDIDATE_RE = re.compile(
    r"(?:^|\s)(?:en|para)\s+"
    r"([a-zà-ɏ][a-zà-ɏ0-9]{1,25}"
    r"(?:\s+[a-zà-ɏ0-9]{2,20}){0,3}?)"
    r"(?=\s+(?:hoy|el|la|los|las|un|una|con|por|\d)|[,.]|\s*$)"
)
```

**Por qué este patrón:**

- `(?:en|para)\s+` — captura el patrón lingüístico más común en español argentino para mencionar un comercio: "descuentos **en** Carrefour", "promos **para** YPF". Son las dos preposiciones que preceden nombres de comercios en consultas de beneficios.
- `[a-zà-ɏ][a-zà-ɏ0-9]{1,25}` — el primer token debe empezar con letra, tener entre 2 y 26 caracteres. Evita capturar números solos.
- `(?:\s+[a-zà-ɏ0-9]{2,20}){0,3}?` — hasta 3 tokens adicionales, con cuantificador lazy `?`. "Lazy" es clave: captura lo mínimo posible para no tragarse la oración entera.
- `(?=\s+(?:hoy|el|la|...|\d)|[,.]|\s*$)` — lookahead para cortar en palabras de corte naturales (artículos, números, comas, fin de texto). Esto evita que "descuentos en Mostaza hoy" capture "mostaza hoy".

### Filtro de falsos positivos

```python
known_tokens = _ALL_CATEGORY_TOKENS | _BENEFIT_KEYWORDS | _NEGOCIO_STOP_TOKENS
useful_tokens = candidate_tokens - known_tokens
if not useful_tokens:
    return None
```

`_ALL_CATEGORY_TOKENS` se genera dinámicamente de `_CATEGORY_KEYWORDS`:

```python
_ALL_CATEGORY_TOKENS: frozenset[str] = frozenset(
    token
    for keywords in _CATEGORY_KEYWORDS.values()
    for token in keywords
    if " " not in token
)
```

**Por qué:** evita que "descuentos en gastronomía" extraiga "gastronomía" como negocio. Si todos los tokens del candidato son categorías o keywords conocidas, es una categoría — no un negocio.

### Ejemplo de ejecución

```
query: "descuentos en mostaza"
text normalizado: "descuentos en mostaza"
_extract_negocio_candidate("descuentos en mostaza", {"descuentos","en","mostaza"})
  → regex captura: "mostaza"
  → candidate_tokens = {"mostaza"}
  → known_tokens contiene "descuentos" pero NO "mostaza"
  → useful_tokens = {"mostaza"} → no vacío
  → retorna "mostaza"

query: "descuentos en gastronomia"
  → regex captura: "gastronomia"
  → candidate_tokens = {"gastronomia"}
  → known_tokens contiene "gastronomia" (_CATEGORY_KEYWORDS)
  → useful_tokens = {} → vacío
  → retorna None ✓ (categoría, no negocio)
```

---

## Capa 2: Business Index en business_index.py

### Estructura del índice

```json
{
  "token_map": {
    "mostaza":   ["MOSTAZA PALERMO SRL", "MOSTAZA BELGRANO"],
    "ypf":       ["YPF LITORAL SRL", "YPF FLORES"],
    "carrefour": ["CARREFOUR EXPRESS", "CARREFOUR HIPERMERCADO"]
  },
  "all_names": ["MOSTAZA PALERMO SRL", "YPF LITORAL SRL", ...]
}
```

**Por qué este diseño y no otro:**

| Alternativa | Problema |
|---|---|
| Lista plana con búsqueda lineal | O(n) por query, ~500 negocios = lento |
| Embedding + vector store | Overkill: requiere modelo, infra, latencia extra |
| Trie (árbol de prefijos) | Complejo de serializar en Redis |
| **Índice invertido** ✓ | O(1) lookup por token, JSON serializable, < 100KB |

El índice invertido es la estructura usada en motores de búsqueda (Lucene, Elasticsearch). Para este dominio (500 negocios, tokens cortos) es la solución óptima: lookup en O(1) por token, serialización trivial a JSON en Redis.

### Algoritmo build_index

```
Para cada beneficio en all_benefits:
  1. Extraer campo `b` (nombre del comercio)
  2. Deduplicar por nombre exacto
  3. Normalizar: minúsculas, sin acentos, solo alfanuméricos
  4. Tokenizar y descartar stop-tokens (artículos, formas jurídicas)
  5. Para cada token útil: token_map[token].append(b_raw)
```

**Stop-tokens descartados:**
- Artículos: el, la, los, las, un, una
- Preposiciones: de, del, en, a, al
- Formas jurídicas: srl, sa, sas, ltda (no aportan al nombre del comercio)
- Genéricos: express, center, store, local, sucursal

**Por qué descartar formas jurídicas:** si un usuario busca "mostaza", no busca "mostaza srl". Incluir "srl" en el índice generaría ruido sin valor.

### Algoritmo resolve_negocio

```python
async def resolve_negocio(candidate: str) -> Optional[str]:
    tokens = _tokenize(candidate)
    token_scores = {}
    
    for token in tokens:
        matches = token_map.get(token, [])
        if matches:
            token_scores[token] = len(matches)  # = cobertura en el catálogo
    
    if not token_scores:
        return None
    
    best_token = max(token_scores, key=lambda t: (token_scores[t], -len(t)))
    return best_token
```

**Decisión de retornar el TOKEN y no el b_name completo:**

El filtro en `_apply_filters` hace `substring match`: `negocio.lower() in b.lower()`. Si retornamos `"mostaza"`, el filtro encontrará tanto "MOSTAZA PALERMO SRL" como "MOSTAZA BELGRANO" — que es el comportamiento correcto. Si retornáramos "MOSTAZA PALERMO SRL", solo encontraría ese local específico.

**Criterio de scoring:** el token con más b_names en el índice es el más representativo (más presencia en el catálogo). En empate, se prefiere el más corto porque un token corto como "mostaza" tiene más poder de discriminación como substring que "mostaza palermo".

### Gestión del TTL en Redis

```
all_benefits:global    → TTL 86400s (24h)   [benefits_api.py]
business_index         → TTL 86400s (24h)   [business_index.py]
```

Los dos TTLs están alineados intencionalmente. Cuando `all_benefits` expira y se renueva desde la API, el endpoint `/sync/business-index` debe invocarse para reconstruir el índice con los datos nuevos. Esto se hace vía cron diario.

---

## Capa 3: Fallback token-overlap en benefits_api.py

```python
# Intento 1: substring exacto (comportamiento previo)
substring_results = [item for item in filtered
                     if negocio_lower in item.get("b", "").lower()]

if substring_results:
    filtered = substring_results
else:
    # Intento 2: token overlap
    negocio_tokens = {t for t in negocio_lower.split()
                      if t not in _NEGOCIO_STOP and len(t) >= 3}
    token_results = [item for item in filtered
                     if negocio_tokens & set(_normalize_b(item.get("b","")).split())]
    filtered = token_results
```

**Por qué dos intentos en lugar de solo token-overlap:**

El substring exacto es más preciso. "carrefour express" como substring requiere que el b_name contenga exactamente esa secuencia. El token overlap es más permisivo: "carrefour" AND "express" como tokens separados encontraría también "EXPRESS CARREFOUR" o "CARREFOUR SRL + EXPRESS". Ir directo a token-overlap aumenta falsos positivos. El orden correcto es: substring exacto primero, fallback a token-overlap solo si da 0 resultados.

**`_normalize_b`:** función auxiliar para normalizar el campo `b` antes del split. Sin esta normalización, "MOSTAZA" (mayúsculas) no matchearía con el token "mostaza" en el overlap set.

---

## Step 3.5 en query_orchestrator.py

```python
if classification.intent == "benefits" and classification.negocio:
    _is_heuristic_candidate = (
        classification.negocio not in _KNOWN_NEGOCIOS.values()
    )
    if _is_heuristic_candidate:
        resolved = await resolve_negocio(classification.negocio)
        if resolved:
            classification = Classification(**{**classification_dict, "negocio": resolved})
        else:
            _candidate = classification.negocio
            if " " in _candidate or len(_candidate) > 15:
                # Descartar candidatos multi-word o largos no confirmados
                classification = Classification(**{**classification_dict, "negocio": None})
```

**Por qué el check `not in _KNOWN_NEGOCIOS.values()`:**

Distingue si el `negocio` viene de `_KNOWN_NEGOCIOS` (hardcodeado, confiable) o de `_extract_negocio_candidate` (heurístico, necesita validación). Los negocios conocidos como "carrefour", "ypf" nunca pasan por el índice — ya son confiables.

**Por qué descartar multi-word no confirmados:**

Un candidato de una sola palabra corta como "mostaza" que el índice no reconoce puede ser un negocio local o nuevo no indexado todavía. El substring fallará en 0 resultados pero no generará confusión. En cambio, "la cantina del puerto norte" pasado como substring a `_apply_filters` casi con certeza dará 0 resultados y el LLM generará una respuesta confusa. Más vale descartarlo y que el agente responda "no encontré beneficios para ese comercio" a que busque con un filtro que nunca va a matchear.

---

## Endpoint POST /sync/business-index

```http
POST /sync/business-index
→ 200 {"ok": true, "indexed": 487, "message": "487 negocios indexados correctamente"}
→ 500 {"detail": "No se pudieron obtener los beneficios..."}
```

**Estrategia de obtención de datos:**
1. Intenta leer `all_benefits:global` de Redis (ya cacheado por consultas previas)
2. Si no está en cache → llama a TeVaBien API directamente
3. Construye el índice
4. Persiste en Redis con TTL 24h

**Por qué reusar el cache de all_benefits:** evita una llamada HTTP extra a TeVaBien en el sync nocturno. Si el cache diario ya está caliente (es el caso normal), el sync es instantáneo — solo procesa el JSON que ya está en Redis.

**Import lazy en build_and_sync:**
```python
async def build_and_sync() -> int:
    # Import lazy para evitar circular entre business_index ↔ benefits_api
    from ..tools.benefits_api import _fetch_all_benefits_from_api, BenefitsAPIConfig
```
`business_index` importa de `benefits_api`, pero `benefits_api` NO importa de `business_index`. El import lazy dentro de la función evita que Python levante `ImportError` por circular imports si en el futuro alguien conecta los dos módulos.

---

## Flujo completo end-to-end

### Caso 1: negocio conocido (comportamiento sin cambios)

```
"descuentos en ypf"
  → fast_classify: _KNOWN_NEGOCIOS["ypf"] = "ypf" → negocio="ypf" ✓
  → Step 3.5: "ypf" IN _KNOWN_NEGOCIOS.values() → skip validación
  → _apply_filters: substring "ypf" in "YPF LITORAL".lower() → True ✓
```

### Caso 2: negocio desconocido → en índice

```
"quiero promos en Mostaza"
  → fast_classify: no en _KNOWN_NEGOCIOS
    → _extract_negocio_candidate("quiero promos en mostaza", tokens)
    → regex captura "mostaza" → useful_tokens={"mostaza"} → negocio="mostaza"
  → Step 3.5: "mostaza" NOT IN _KNOWN_NEGOCIOS.values() → resolve_negocio("mostaza")
    → load_index() → token_map["mostaza"] = ["MOSTAZA PALERMO SRL", "MOSTAZA BELGRANO"]
    → best_token = "mostaza" (token con mayor cobertura) → retorna "mostaza"
  → classification.negocio = "mostaza" (confirmado)
  → _apply_filters: "mostaza" in "MOSTAZA PALERMO SRL".lower() → True ✓
```

### Caso 3: negocio desconocido → no en índice → una sola palabra

```
"promos en Frigor"   (nombre local, no indexado)
  → fast_classify: → negocio="frigor" (heurístico)
  → Step 3.5: resolve_negocio("frigor") → None (sin match)
  → "frigor" len=6, no tiene espacio → NO se descarta
  → _apply_filters substring: "frigor" in "FRIGORÍFICO PALERMO".lower() → False (typo/acento)
  → fallback token-overlap: {"frigor"} & set("frigorifico palermo".split()) → False
  → filtered = [] → agente responde "no encontré beneficios para ese comercio"
```

### Caso 4: candidato multi-word no confirmado → descartado

```
"descuentos en la panaderia de juan"
  → fast_classify: regex captura "la panaderia de juan"
    → useful_tokens = {"panaderia", "juan"} (se filtran "la","de")
    → negocio="la panaderia de juan"
  → Step 3.5: resolve_negocio("la panaderia de juan")
    → tokens útiles: {"panaderia", "juan"}
    → token_map["panaderia"] puede existir, "juan" probablemente no
    → si ningún token matchea: resolve devuelve None
  → " " in "la panaderia de juan" → True → DESCARTADO
  → classification.negocio = None
  → búsqueda sin filtro de negocio (resultado: gastronomía general)
```

---

## Resumen de cambios por archivo

| Archivo | Tipo | Descripción |
|---|---|---|
| `src/tools/business_index.py` | NUEVO | Índice invertido completo: build, store, load, resolve, sync |
| `src/tools/fast_classifier.py` | MODIFICADO | `_extract_negocio_candidate` + constantes de soporte |
| `src/services/query_orchestrator.py` | MODIFICADO | Step 3.5: validación del candidato vía Business Index |
| `src/api/main.py` | MODIFICADO | Endpoint `POST /sync/business-index` |
| `src/tools/benefits_api.py` | MODIFICADO | `_normalize_b` + fallback token-overlap en `_apply_filters` |

---

## Operaciones de mantenimiento

### Sincronización diaria

```bash
# Invocar después del prime time o madrugada cuando all_benefits fue renovado
curl -X POST https://{host}/sync/business-index
# Respuesta: {"ok": true, "indexed": 487, "message": "487 negocios indexados"}
```

### Forzar re-fetch desde API (si Redis está vacío)

El endpoint llama automáticamente a la API de TeVaBien si `all_benefits:global` no está en Redis. No requiere parámetros adicionales.

### Monitoreo

Logs a observar:
```
[BusinessIndex] Índice construido: 487 negocios únicos, 1243 tokens
[BusinessIndex] Cache HIT (487 negocios)
[BusinessIndex] 'mostaza' → 'mostaza' (2 negocios indexados)
[BusinessIndex] candidato 'la panaderia de juan' no confirmado → descartado
[Filter] negocio='mostaza' -> 2 beneficios
[Filter] negocio='mostaza belgrano' token-overlap (tokens={'mostaza','belgrano'}) -> 1 beneficios
```
