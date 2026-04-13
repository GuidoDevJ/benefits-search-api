"""
Tests de arquitectura — Validación de las 4 fases de mejora.

Cubre:
  1. Supervisor determinístico — nunca llama al LLM
  2. Prompt assembly — orden fijo, validación de datos, casos edge
  3. Cache de resultados — clave, hit/miss, paginación desde caché
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Silenciar warnings de AWS en tests ───────────────────────────────────
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("MOCK_USER_PROFILE", "true")
os.environ.setdefault("MOCK_BENEFITS", "true")


# ════════════════════════════════════════════════════════════════════════
# 1. Supervisor determinístico
# ════════════════════════════════════════════════════════════════════════

class TestSupervisorDeterministic:
    """
    El supervisor debe rutear sin llamar al LLM en todos los casos.
    El orchestrator siempre resuelve la intención antes del grafo.
    """

    def _make_state(self, intent=None, has_benefits=None):
        ctx = {}
        if intent is not None:
            ctx["classification"] = {"intent": intent}
        if has_benefits is not None:
            ctx["has_benefits"] = has_benefits
        return {
            "messages": [],
            "context": ctx,
            "session_id": "test-session",
        }

    @pytest.mark.asyncio
    async def test_routes_to_benefits_when_intent_benefits(self):
        from src.agents.supervisor_agent import create_supervisor_agent

        llm_mock = MagicMock()  # LLM NO debe ser llamado
        node = create_supervisor_agent(llm_mock, agents=["benefits"])

        state = self._make_state(intent="benefits")
        result = await node(state)

        assert result["next"] == "benefits"
        llm_mock.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_finishes_when_has_benefits_already_set(self):
        from src.agents.supervisor_agent import create_supervisor_agent

        llm_mock = MagicMock()
        node = create_supervisor_agent(llm_mock, agents=["benefits"])

        # benefits ya ejecutó en una vuelta anterior
        state = self._make_state(intent="benefits", has_benefits=True)
        result = await node(state)

        assert result["next"] == "finish"
        llm_mock.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_finishes_when_no_classification(self):
        from src.agents.supervisor_agent import create_supervisor_agent

        llm_mock = MagicMock()
        node = create_supervisor_agent(llm_mock, agents=["benefits"])

        # Sin clasificación → finish sin llamar LLM
        state = self._make_state()
        result = await node(state)

        assert result["next"] == "finish"
        llm_mock.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_finishes_when_intent_not_in_agents(self):
        from src.agents.supervisor_agent import create_supervisor_agent

        llm_mock = MagicMock()
        node = create_supervisor_agent(llm_mock, agents=["benefits"])

        state = self._make_state(intent="unknown_agent")
        result = await node(state)

        assert result["next"] == "finish"
        llm_mock.ainvoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_has_benefits_false_still_finishes(self):
        """has_benefits=False (falsy pero not None) → terminar igual."""
        from src.agents.supervisor_agent import create_supervisor_agent

        llm_mock = MagicMock()
        node = create_supervisor_agent(llm_mock, agents=["benefits"])

        # has_benefits=False significa que ejecutó pero sin resultados
        state = self._make_state(intent="benefits", has_benefits=False)
        result = await node(state)

        assert result["next"] == "finish"


# ════════════════════════════════════════════════════════════════════════
# 2. Prompt assembly — orden, validación y casos edge
# ════════════════════════════════════════════════════════════════════════

class TestPromptAssembly:
    """
    El system prompt debe tener orden fijo y manejar correctamente
    los 3 casos: con datos, sin datos, y con error.
    """

    def _serializer(self):
        """Serializer mínimo para tests."""
        s = MagicMock()
        s.serialize = lambda data: str(data)
        s.get_format_instruction = lambda: ""
        return s

    def test_prompt_order_base_then_ctx_then_results(self):
        """El orden siempre debe ser: base → user_ctx → results."""
        from src.agents.benefits_agent import _build_system_prompt

        base = "INSTRUCCIONES_BASE"
        ctx = "Contexto del cliente:\n- Segmento: MASIVO"
        tool_result = {
            "data": [
                {"nom": "Carrefour", "ben": "20% off",
                 "pago": "MODO", "dias": "Todos los días"}
            ],
            "total": 1, "mostrando": 1, "restantes": 0, "hay_mas": False,
        }

        prompt = _build_system_prompt(
            base_system=base,
            user_ctx=ctx,
            tool_result=tool_result,
            has_benefits=True,
            serializer=self._serializer(),
        )

        pos_base = prompt.index("INSTRUCCIONES_BASE")
        pos_ctx = prompt.index("CONTEXTO DEL CLIENTE")
        pos_results = prompt.index("RESULTADOS DE BÚSQUEDA")

        assert pos_base < pos_ctx < pos_results, (
            "Orden incorrecto: debe ser base → user_ctx → results"
        )

    def test_empty_results_contains_explicit_instruction(self):
        """Sin datos, el prompt debe tener instrucción explícita."""
        from src.agents.benefits_agent import _build_system_prompt

        tool_result = {
            "data": [], "total": 0, "mostrando": 0,
            "restantes": 0, "hay_mas": False,
        }

        prompt = _build_system_prompt(
            base_system="BASE",
            user_ctx="Contexto del cliente:\n- Fecha actual: lunes",
            tool_result=tool_result,
            has_benefits=False,
            serializer=self._serializer(),
        )

        # Debe contener instrucción explícita para el caso sin resultados
        assert "INSTRUCCIÓN" in prompt
        assert "alternativas" in prompt.lower()

    def test_error_result_contains_error_instruction(self):
        """Con error, el prompt debe indicar al LLM informar amablemente."""
        from src.agents.benefits_agent import _build_system_prompt

        tool_result = {"error": "Timeout", "data": []}

        prompt = _build_system_prompt(
            base_system="BASE",
            user_ctx="Contexto del cliente:\n- Fecha: lunes",
            tool_result=tool_result,
            has_benefits=False,
            serializer=self._serializer(),
        )

        assert "error" in prompt.lower() or "INSTRUCCIÓN" in prompt

    def test_segment_black_hint_in_empty_results(self):
        """Sin resultados + segmento Black: hint debe ser sofisticado."""
        from src.agents.benefits_agent import _build_system_prompt

        tool_result = {
            "data": [], "total": 0, "mostrando": 0,
            "restantes": 0, "hay_mas": False,
        }
        user_ctx = (
            "Contexto del cliente:\n"
            "- Segmento: COMAFI UNICO BLACK\n"
            "- Tono: sofisticado y exclusivo."
        )

        prompt = _build_system_prompt(
            base_system="BASE",
            user_ctx=user_ctx,
            tool_result=tool_result,
            has_benefits=False,
            serializer=self._serializer(),
        )

        # Debe mencionar algo sobre el tono sofisticado o alternativas
        # con coherencia de segmento
        assert "sofisticado" in prompt or "exclusiv" in prompt

    def test_no_user_ctx_still_builds_prompt(self):
        """Sin contexto de usuario (anónimo) el prompt igual funciona."""
        from src.agents.benefits_agent import _build_system_prompt

        tool_result = {
            "data": [
                {"nom": "Shell", "ben": "5% off",
                 "pago": "Visa", "dias": "Todos los días"}
            ],
            "total": 1, "mostrando": 1, "restantes": 0, "hay_mas": False,
        }

        prompt = _build_system_prompt(
            base_system="BASE",
            user_ctx="",  # sin contexto
            tool_result=tool_result,
            has_benefits=True,
            serializer=self._serializer(),
        )

        assert "RESULTADOS DE BÚSQUEDA" in prompt
        assert "CONTEXTO DEL CLIENTE" not in prompt


class TestValidateToolResult:
    """_validate_tool_result descarta items con estructura inválida."""

    def test_valid_items_pass_through(self):
        from src.agents.benefits_agent import _validate_tool_result

        tool_result = {
            "data": [
                {"nom": "A", "ben": "10%", "pago": "MODO", "dias": "Lunes"},
                {"nom": "B", "ben": "20%", "pago": "Visa", "dias": "Martes"},
            ],
            "total": 2, "mostrando": 2,
        }
        result = _validate_tool_result(tool_result)
        assert len(result["data"]) == 2

    def test_invalid_item_discarded(self):
        from src.agents.benefits_agent import _validate_tool_result

        tool_result = {
            "data": [
                {"nom": "A", "ben": "10%", "pago": "MODO", "dias": "Lunes"},
                {"nom": "B"},  # faltan campos
                "string_invalido",  # tipo incorrecto
            ],
            "total": 3, "mostrando": 3,
        }
        result = _validate_tool_result(tool_result)
        assert len(result["data"]) == 1
        assert result["data"][0]["nom"] == "A"

    def test_non_list_data_returns_empty(self):
        from src.agents.benefits_agent import _validate_tool_result

        tool_result = {"data": "no es lista", "total": 0}
        result = _validate_tool_result(tool_result)
        assert result["data"] == []


# ════════════════════════════════════════════════════════════════════════
# 3. Cache de resultados filtrados
# ════════════════════════════════════════════════════════════════════════

class TestSearchCacheKey:
    """La clave de caché debe ser determinística y cubrir todos los params."""

    def _entities(self, **kwargs):
        from src.models.typed_entities import Entities
        return Entities(**kwargs)

    def test_same_params_same_key(self):
        from src.tools.benefits_api import _build_search_cache_key

        e = self._entities(categoria="gastronomia", dias=["sabado"])
        profile = {"provincia": "CÓRDOBA", "productos": ["VISA"]}

        key1 = _build_search_cache_key(e, profile)
        key2 = _build_search_cache_key(e, profile)

        assert key1 == key2

    def test_different_categoria_different_key(self):
        from src.tools.benefits_api import _build_search_cache_key

        e1 = self._entities(categoria="gastronomia")
        e2 = self._entities(categoria="supermercados")

        assert _build_search_cache_key(e1, None) != \
               _build_search_cache_key(e2, None)

    def test_different_days_different_key(self):
        from src.tools.benefits_api import _build_search_cache_key

        e1 = self._entities(categoria="moda", dias=["lunes"])
        e2 = self._entities(categoria="moda", dias=["sabado"])

        assert _build_search_cache_key(e1, None) != \
               _build_search_cache_key(e2, None)

    def test_different_province_different_key(self):
        from src.tools.benefits_api import _build_search_cache_key

        e = self._entities(categoria="gastronomia")
        p1 = {"provincia": "CÓRDOBA"}
        p2 = {"provincia": "MENDOZA"}

        assert _build_search_cache_key(e, p1) != \
               _build_search_cache_key(e, p2)

    def test_days_order_irrelevant(self):
        """El orden de los días no debe afectar la clave."""
        from src.tools.benefits_api import _build_search_cache_key

        e1 = self._entities(dias=["lunes", "sabado"])
        e2 = self._entities(dias=["sabado", "lunes"])

        assert _build_search_cache_key(e1, None) == \
               _build_search_cache_key(e2, None)

    def test_key_has_correct_prefix(self):
        from src.tools.benefits_api import _build_search_cache_key

        e = self._entities(categoria="gastronomia")
        key = _build_search_cache_key(e, None)

        assert key.startswith("benefits:search:")

    def test_none_profile_same_as_empty(self):
        """None y dict vacío deben generar la misma clave."""
        from src.tools.benefits_api import _build_search_cache_key

        e = self._entities(categoria="gastronomia")
        assert _build_search_cache_key(e, None) == \
               _build_search_cache_key(e, {})


# ════════════════════════════════════════════════════════════════════════
# 4. Fast-classify antes de is_valid_query (fix "Si" → ver_más)
# ════════════════════════════════════════════════════════════════════════

class TestFastClassifyBeforeNLP:
    """
    fast_classify debe correr ANTES de is_valid_query.

    Afirmativos cortos ("si", "dale", "ok") tienen len < 3 y/o están en
    _STOP_ONLY de is_valid_query — quedarían bloqueados si la validación NLP
    corriera primero.  Verificamos que fast_classify los reconoce como
    ver_mas y que el orquestador no los rechaza.
    """

    # ── 4a. fast_classify reconoce cada afirmativo como ver_mas ──────────

    @pytest.mark.parametrize("affirmative", [
        "si", "sí", "dale", "ok", "claro", "bueno", "va",
        "genial", "perfecto", "listo", "vamos",
        # Con exclamación (se quita puntuación antes del match)
        "dale!", "ok!", "si!", "sí!", "claro!", "bueno!", "va!",
        # Caracteres repetidos — colapsados internamente
        "sii", "daleee", "okk",
        # Combinaciones afirmativas puras (≤ 3 tokens)
        "dale si", "ok dale",
    ])
    def test_affirmative_returns_ver_mas(self, affirmative):
        from src.tools.fast_classifier import fast_classify

        result = fast_classify(affirmative)
        assert result is not None, (
            f"fast_classify({affirmative!r}) retornó None "
            f"— debería reconocerlo"
        )
        assert result.intent == "ver_mas", (
            f"fast_classify({affirmative!r}).intent={result.intent!r}, "
            f"esperado 'ver_mas'"
        )

    # ── 4b. Afirmativo + contexto extra NO es ver_mas automático ─────────

    @pytest.mark.parametrize("query", [
        "dale vuelta",          # "dale" + otra palabra no afirmativa
        "si quiero comida",     # "si" + consulta de beneficio → benefits
        "ok pero busco sushi",  # combinado con intención
    ])
    def test_affirmative_with_extra_context_not_forced_ver_mas(self, query):
        """
        Cuando el afirmativo va acompañado de tokens de intención real,
        fast_classify NO debe forzar ver_mas — puede devolver benefits o None.
        """
        from src.tools.fast_classifier import fast_classify

        result = fast_classify(query)
        # No debe ser ver_mas (puede ser benefits o None, pero no ver_mas)
        if result is not None:
            assert result.intent != "ver_mas", (
                f"fast_classify({query!r}) devolvió ver_mas incorrectamente"
            )

    # ── 4c. is_valid_query rechaza "si" — confirma que el orden importa ──

    def test_is_valid_query_rejects_si(self):
        """
        is_valid_query("si") debe retornar False.
        Esto confirma que el bug original existía: si corría antes de
        fast_classify, el afirmativo era bloqueado.
        """
        from src.tools.nlp_processor import is_valid_query

        assert is_valid_query("si") is False, (
            "is_valid_query('si') debe ser False — confirma que el orden "
            "fast_classify-first es necesario"
        )

    def test_is_valid_query_rejects_ok(self):
        from src.tools.nlp_processor import is_valid_query

        assert is_valid_query("ok") is False

    # ── 4d. Prueba lógica: el ordering fast_classify-first es necesario ───

    def test_ordering_requirement_documented(self):
        """
        Documenta por qué fast_classify debe correr ANTES de is_valid_query.

        'si' falla is_valid_query (muy corto / stop-word) pero fast_classify
        lo reconoce como ver_mas.  Si el orden fuera invertido, 'si' quedaría
        bloqueado con "No pude entender tu consulta".
        """
        from src.tools.fast_classifier import fast_classify
        from src.tools.nlp_processor import is_valid_query

        query = "si"

        # fast_classify lo reconoce → válido sin necesitar NLP
        fc_result = fast_classify(query)
        assert fc_result is not None, (
            "fast_classify('si') no debe retornar None"
        )
        assert fc_result.intent == "ver_mas"

        # NLP sola lo rechazaría
        assert is_valid_query(query) is False, (
            "is_valid_query('si') debe ser False — confirma que el orden "
            "fast_classify-first es necesario para no bloquear afirmativos"
        )


# ════════════════════════════════════════════════════════════════════════
# 5. Recuperación de clasificación desde historial (fallback ver_mas)
# ════════════════════════════════════════════════════════════════════════

class TestRecoverClassificationFromHistory:
    """
    _recover_classification_from_history escanea el historial en orden
    inverso y devuelve la última clasificación benefits válida.

    Cubre el caso donde search_context expiró en Redis y el usuario
    dice "sí" — el orquestador recupera los parámetros del historial
    en lugar de responder "No tengo búsqueda anterior".
    """

    def _human(self, text: str):
        from langchain_core.messages import HumanMessage
        return HumanMessage(content=text)

    def _ai(self, text: str):
        from langchain_core.messages import AIMessage
        return AIMessage(content=text)

    def test_recovers_benefits_query_from_history(self):
        """La última query de benefits en el historial se recupera."""
        from src.services.query_orchestrator import (
            _recover_classification_from_history,
        )

        history = [
            self._human("busco descuentos en gastronomía"),
            self._ai("Encontré 12 beneficios..."),
            self._human("sí"),
        ]

        result = _recover_classification_from_history(history)

        assert result is not None
        assert result["intent"] == "benefits"
        assert result["categoria_benefits"] == "gastronomia"

    def test_skips_affirmatives_in_history(self):
        """Los afirmativos intermedios se saltan; se usa la query real."""
        from src.services.query_orchestrator import (
            _recover_classification_from_history,
        )

        history = [
            self._human("quiero ver cine los viernes"),
            self._ai("Encontré 8 beneficios..."),
            self._human("dale"),
            self._ai("Acá van los siguientes 5..."),
            self._human("sí"),
        ]

        result = _recover_classification_from_history(history)

        assert result is not None
        assert result["categoria_benefits"] == "cine"

    def test_returns_none_on_empty_history(self):
        """Sin historial retorna None sin explotar."""
        from src.services.query_orchestrator import (
            _recover_classification_from_history,
        )

        assert _recover_classification_from_history([]) is None

    def test_returns_none_when_only_affirmatives(self):
        """Si el historial solo tiene afirmativos, retorna None."""
        from src.services.query_orchestrator import (
            _recover_classification_from_history,
        )

        history = [
            self._human("sí"),
            self._human("dale"),
            self._human("ok"),
        ]

        assert _recover_classification_from_history(history) is None

    def test_uses_most_recent_query(self):
        """
        Si hay dos búsquedas distintas en el historial,
        se recupera la más reciente.
        """
        from src.services.query_orchestrator import (
            _recover_classification_from_history,
        )

        history = [
            self._human("descuentos en supermercados"),
            self._ai("Encontré 5 beneficios en supermercados..."),
            self._human("ahora busco combustible"),
            self._ai("Encontré 3 beneficios de combustible..."),
            self._human("sí"),
        ]

        result = _recover_classification_from_history(history)

        assert result is not None
        assert result["categoria_benefits"] == "combustible"

    def test_ignores_ai_messages(self):
        """Los AIMessages se ignoran — solo se escanean HumanMessages."""
        from src.services.query_orchestrator import (
            _recover_classification_from_history,
        )

        history = [
            self._human("gastronomía los sábados"),
            self._ai("dale viernes → gastronomia sabado descuento"),
        ]

        result = _recover_classification_from_history(history)

        assert result is not None
        assert result["categoria_benefits"] == "gastronomia"


class TestSearchResultsCache:
    """Cache hit/miss y paginación correcta desde lista completa."""

    def _entities(self, **kwargs):
        from src.models.typed_entities import Entities
        return Entities(**kwargs)

    def _mock_normalized(self, n: int) -> list:
        """Genera n items normalizados de prueba."""
        return [
            {"nom": f"Comercio {i}", "ben": f"{10+i}% off",
             "pago": "MODO", "dias": "Todos los días"}
            for i in range(n)
        ]

    @pytest.mark.asyncio
    async def test_cache_hit_skips_fetch_benefits(self):
        """Con cache hit, fetch_benefits NO debe llamarse."""
        from src.tools.benefits_api import search_benefits_with_profile

        entities = self._entities(categoria="gastronomia")
        cached_data = self._mock_normalized(10)

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=cached_data)
        mock_cache.set = AsyncMock()

        with patch(
            "src.tools.benefits_api.CACHE_ENABLED", True
        ), patch(
            "src.tools.benefits_api.get_cache_service",
            AsyncMock(return_value=mock_cache),
        ), patch(
            "src.tools.benefits_api.fetch_benefits",
            AsyncMock(side_effect=AssertionError(
                "fetch_benefits NO debe llamarse en cache HIT"
            )),
        ):
            result = await search_benefits_with_profile(
                query="test", entities=entities, user_profile=None
            )

        assert result["total"] == 10
        assert result["mostrando"] == 5
        assert result["hay_mas"] is True

    @pytest.mark.asyncio
    async def test_cache_miss_stores_full_list(self):
        """En cache miss, se guarda la lista completa (no solo top-5)."""
        from src.tools.benefits_api import search_benefits_with_profile
        from src.tools.benefits_api import BenefitsResponse, BenefitItem

        entities = self._entities(categoria="gastronomia")

        # Construir BenefitItems reales (Pydantic los valida)
        def _item(idx: int) -> BenefitItem:
            return BenefitItem(
                i=idx, t=407, c=[], d=f"{10 + idx}",
                q=None, a="1234567", b=f"Comercio {idx}",
                ct="MODO", cti=[], m=None, r=[1], o=[],
                f=230101, e=231231, pr=[151],
            )

        mock_response = BenefitsResponse(
            success=True,
            data=[_item(i) for i in range(8)],
            url="mock",
            status_code=200,
        )

        stored_data = {}
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=None)  # MISS

        async def fake_set(key, value, ttl=None):
            stored_data["key"] = key
            stored_data["value"] = value
            stored_data["ttl"] = ttl

        mock_cache.set = fake_set

        with patch(
            "src.tools.benefits_api.CACHE_ENABLED", True
        ), patch(
            "src.tools.benefits_api.get_cache_service",
            AsyncMock(return_value=mock_cache),
        ), patch(
            "src.tools.benefits_api.fetch_benefits",
            AsyncMock(return_value=mock_response),
        ):
            result = await search_benefits_with_profile(
                query="test", entities=entities, user_profile=None
            )

        # Se guardó la lista completa (8 items normalizados)
        assert len(stored_data.get("value", [])) == 8
        # TTL debe ser 1 hora
        assert stored_data.get("ttl") == 3600
        # Resultado paginado: top 5 de 8
        assert result["total"] == 8
        assert result["mostrando"] == 5

    @pytest.mark.asyncio
    async def test_pagination_from_cached_list(self):
        """El offset aplica correctamente sobre la lista cacheada."""
        from src.tools.benefits_api import search_benefits_with_profile

        entities = self._entities(categoria="gastronomia")
        cached_data = self._mock_normalized(12)

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=cached_data)

        with patch(
            "src.tools.benefits_api.CACHE_ENABLED", True
        ), patch(
            "src.tools.benefits_api.get_cache_service",
            AsyncMock(return_value=mock_cache),
        ), patch(
            "src.tools.benefits_api.fetch_benefits",
            AsyncMock(side_effect=AssertionError("no debe llamarse")),
        ):
            # Página 1 (offset=0)
            r1 = await search_benefits_with_profile(
                query="test", entities=entities,
                user_profile=None, offset=0,
            )
            # Página 2 (offset=5)
            mock_cache.get = AsyncMock(return_value=cached_data)
            r2 = await search_benefits_with_profile(
                query="test", entities=entities,
                user_profile=None, offset=5,
            )

        assert r1["data"][0]["nom"] == "Comercio 0"
        assert r2["data"][0]["nom"] == "Comercio 5"
        assert r1["hay_mas"] is True
        assert r2["hay_mas"] is True
        assert r2["restantes"] == 2  # 12 - 5 - 5

    @pytest.mark.asyncio
    async def test_cache_disabled_always_fetches(self):
        """Con CACHE_ENABLED=False, siempre llama a fetch_benefits."""
        from src.tools.benefits_api import search_benefits_with_profile
        from src.tools.benefits_api import BenefitsResponse

        entities = self._entities(categoria="gastronomia")

        fetch_call_count = {"n": 0}

        async def fake_fetch(entities, user_profile=None, **kwargs):
            fetch_call_count["n"] += 1
            return BenefitsResponse(
                success=True, data=[], url="mock", status_code=200
            )

        with patch("src.tools.benefits_api.CACHE_ENABLED", False), \
             patch("src.tools.benefits_api.fetch_benefits", fake_fetch):
            await search_benefits_with_profile(
                query="test", entities=entities, user_profile=None
            )
            await search_benefits_with_profile(
                query="test", entities=entities, user_profile=None
            )

        assert fetch_call_count["n"] == 2
