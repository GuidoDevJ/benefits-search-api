"""
Tests de integración — Sistema de memoria y personalización.

Cubre:
  1. ConversationMemoryService — fallback en memoria sin Redis
  2. UserPrefsService          — last_categoria, last_searched_at, fallback
  3. _autofill_today           — auto-fill de día según day_counts
  4. _get_top_from_prefs       — extracción de preferencias estables
  5. _build_user_context_block — bloque de contexto del LLM con prefs
  6. Flujo end-to-end          — pipeline clasificación → prefs → contexto
"""

import asyncio
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

# ── Silenciar warnings de AWS en tests ───────────────────────────────────
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("MOCK_USER_PROFILE", "true")
os.environ.setdefault("MOCK_BENEFITS", "true")


# ════════════════════════════════════════════════════════════════════════
# 1. ConversationMemoryService — fallback en memoria
# ════════════════════════════════════════════════════════════════════════

class TestConversationMemoryFallback:
    """Sin Redis disponible, el historial vive en el dict en memoria."""

    def setup_method(self):
        # Limpiar fallback global antes de cada test
        from src.memory.conversation_memory import _memory_fallback
        _memory_fallback.clear()

    @pytest.mark.asyncio
    async def test_save_and_load_without_redis(self):
        from langchain_core.messages import HumanMessage, AIMessage
        from src.memory.conversation_memory import ConversationMemoryService

        svc = ConversationMemoryService()
        phone = "+5491100000003"

        # Simular Redis caído
        with patch.object(svc, "_ensure_connected", return_value=False):
            saved = await svc.save_messages(phone, [
                HumanMessage(content="descuentos en gastronomia"),
                AIMessage(content="Encontré 5 beneficios..."),
            ])
            assert saved is True

            history = await svc.load_history(phone)
            assert len(history) == 2
            assert history[0].content == "descuentos en gastronomia"
            assert history[1].content == "Encontré 5 beneficios..."

    @pytest.mark.asyncio
    async def test_is_new_session_false_after_save(self):
        """Después de guardar historial, is_new_session debe ser False."""
        from langchain_core.messages import HumanMessage, AIMessage
        from src.memory.conversation_memory import ConversationMemoryService

        svc = ConversationMemoryService()
        phone = "+5491100000001"

        with patch.object(svc, "_ensure_connected", return_value=False):
            # Primera vez — historial vacío → nueva sesión
            history = await svc.load_history(phone)
            assert len(history) == 0  # is_new_session = True

            # Guardar un intercambio
            await svc.save_messages(phone, [
                HumanMessage(content="hola"),
                AIMessage(content="Hola! ¿En qué te ayudo?"),
            ])

            # Segunda vez — historial tiene mensajes → sesión existente
            history = await svc.load_history(phone)
            assert len(history) == 2  # is_new_session = False

    @pytest.mark.asyncio
    async def test_max_messages_window(self):
        """El historial no supera la ventana máxima configurada."""
        from langchain_core.messages import HumanMessage, AIMessage
        from src.memory.conversation_memory import ConversationMemoryService

        svc = ConversationMemoryService(max_messages=4)
        phone = "+5491100000002"

        with patch.object(svc, "_ensure_connected", return_value=False):
            for i in range(4):
                await svc.save_messages(phone, [
                    HumanMessage(content=f"pregunta {i}"),
                    AIMessage(content=f"respuesta {i}"),
                ])

            history = await svc.load_history(phone)
            assert len(history) == 4  # ventana de 4, no 8
            # Los más recientes deben estar al final
            assert "pregunta 3" in history[-2].content


# ════════════════════════════════════════════════════════════════════════
# 2. UserPrefsService — last_categoria, last_searched_at, fallback
# ════════════════════════════════════════════════════════════════════════

class TestUserPrefsRecency:

    def setup_method(self):
        from src.memory.user_prefs import _memory_fallback
        _memory_fallback.clear()

    @pytest.mark.asyncio
    async def test_update_search_prefs_saves_last_categoria(self):
        from src.memory.user_prefs import UserPrefsService

        svc = UserPrefsService()
        phone = "+5491100000003"

        with patch.object(svc, "_ensure_connected", return_value=False):
            await svc.update_search_prefs(phone, "gastronomia", ["sabado"])

            prefs = await svc.load(phone)
            assert prefs["last_categoria"] == "gastronomia"
            assert "last_searched_at" in prefs
            assert prefs["cat_counts"]["gastronomia"] == 1
            assert prefs["day_counts"]["sabado"] == 1

    @pytest.mark.asyncio
    async def test_counters_accumulate(self):
        from src.memory.user_prefs import UserPrefsService

        svc = UserPrefsService()
        phone = "+5491100000004"

        with patch.object(svc, "_ensure_connected", return_value=False):
            await svc.update_search_prefs(phone, "gastronomia", ["sabado"])
            await svc.update_search_prefs(phone, "gastronomia", ["sabado"])
            await svc.update_search_prefs(phone, "supermercados", ["lunes"])

            prefs = await svc.load(phone)
            assert prefs["cat_counts"]["gastronomia"] == 2
            assert prefs["cat_counts"]["supermercados"] == 1
            assert prefs["day_counts"]["sabado"] == 2
            assert prefs["last_categoria"] == "supermercados"  # última

    @pytest.mark.asyncio
    async def test_extract_top_prefs_threshold(self):
        """Solo categorías con >= 2 usos llegan a ser 'top'."""
        from src.memory.user_prefs import UserPrefsService

        svc = UserPrefsService()
        phone = "+5491100000005"

        with patch.object(svc, "_ensure_connected", return_value=False):
            # Un solo uso — NO debe ser top
            await svc.update_search_prefs(phone, "moda", None)
            prefs = await svc.load(phone)
            result = UserPrefsService.extract_top_prefs(prefs)
            assert result["top_categoria"] is None

            # Segundo uso — SÍ debe ser top
            await svc.update_search_prefs(phone, "moda", None)
            prefs = await svc.load(phone)
            result = UserPrefsService.extract_top_prefs(prefs)
            assert result["top_categoria"] == "moda"


# ════════════════════════════════════════════════════════════════════════
# 3. _autofill_today
# ════════════════════════════════════════════════════════════════════════

class TestAutofillToday:

    def _prefs_with_day(self, day: str, count: int = 3) -> dict:
        return {"day_counts": {day: count}}

    def test_autofill_when_no_dia_in_clf(self):
        from src.ui.chat_interface import _autofill_today, _WEEKDAY_KEY
        from datetime import datetime

        hoy_idx = datetime.now().weekday()
        hoy_key = _WEEKDAY_KEY[hoy_idx]

        prefs = self._prefs_with_day(hoy_key, count=3)
        merged = {"intent": "benefits", "categoria_benefits": "gastronomia"}

        result = _autofill_today(merged, prefs)
        assert result["dias"] == [hoy_key]
        assert result["dia"] == hoy_key

    def test_no_autofill_when_dia_already_set(self):
        from src.ui.chat_interface import _autofill_today

        prefs = {"day_counts": {"sabado": 5}}
        merged = {
            "intent": "benefits",
            "categoria_benefits": "gastronomia",
            "dias": ["lunes"],
            "dia": "lunes",
        }
        result = _autofill_today(merged, prefs)
        # No debe sobrescribir el día ya especificado
        assert result["dias"] == ["lunes"]

    def test_no_autofill_when_count_below_threshold(self):
        from src.ui.chat_interface import _autofill_today, _WEEKDAY_KEY
        from datetime import datetime

        hoy_key = _WEEKDAY_KEY[datetime.now().weekday()]
        prefs = {"day_counts": {hoy_key: 1}}  # solo 1 uso, umbral es 2
        merged = {"intent": "benefits"}

        result = _autofill_today(merged, prefs)
        assert result.get("dias") is None

    def test_no_autofill_when_today_not_in_habits(self):
        from src.ui.chat_interface import _autofill_today, _WEEKDAY_KEY
        from datetime import datetime

        hoy_key = _WEEKDAY_KEY[datetime.now().weekday()]
        # El usuario busca otro día, no hoy
        otro_dia = "lunes" if hoy_key != "lunes" else "martes"
        prefs = {"day_counts": {otro_dia: 5}}
        merged = {"intent": "benefits"}

        result = _autofill_today(merged, prefs)
        assert result.get("dias") is None


# ════════════════════════════════════════════════════════════════════════
# 4. _get_top_from_prefs
# ════════════════════════════════════════════════════════════════════════

class TestGetTopFromPrefs:

    def test_returns_top_categoria_above_threshold(self):
        from src.ui.chat_interface import _get_top_from_prefs

        prefs = {"cat_counts": {"gastronomia": 4, "moda": 1}}
        top_cat, top_dias = _get_top_from_prefs(prefs)
        assert top_cat == "gastronomia"

    def test_returns_none_when_all_below_threshold(self):
        from src.ui.chat_interface import _get_top_from_prefs

        prefs = {"cat_counts": {"gastronomia": 1, "moda": 1}}
        top_cat, _ = _get_top_from_prefs(prefs)
        assert top_cat is None

    def test_returns_top_dias_above_threshold(self):
        from src.ui.chat_interface import _get_top_from_prefs

        prefs = {"day_counts": {"sabado": 3, "domingo": 2, "lunes": 1}}
        _, top_dias = _get_top_from_prefs(prefs)
        assert set(top_dias) == {"sabado", "domingo"}

    def test_empty_prefs(self):
        from src.ui.chat_interface import _get_top_from_prefs

        top_cat, top_dias = _get_top_from_prefs({})
        assert top_cat is None
        assert top_dias is None


# ════════════════════════════════════════════════════════════════════════
# 5. _build_user_context_block — prefs y recencia en el contexto del LLM
# ════════════════════════════════════════════════════════════════════════

class TestBuildUserContextBlock:

    def _profile(self, segmento="COMAFI PREMIUM PLATINUM"):
        return {
            "identificado": True,
            "nombre": "Martin",
            "nombre_completo": "Martin Ibanez",
            "segmento": segmento,
            "productos": ["VISA VISA SIGNATURE"],
        }

    def test_categoria_favorita_aparece_en_contexto(self):
        from src.agents.benefits_agent import _build_user_context_block

        prefs = {"cat_counts": {"gastronomia": 4}, "day_counts": {}}
        ctx = _build_user_context_block(
            self._profile(), prefs, is_new_session=False
        )
        assert "gastronomia" in ctx
        assert "4" in ctx  # "buscada 4 veces"

    def test_categoria_favorita_no_aparece_bajo_umbral(self):
        from src.agents.benefits_agent import _build_user_context_block

        prefs = {"cat_counts": {"gastronomia": 1}, "day_counts": {}}
        ctx = _build_user_context_block(
            self._profile(), prefs, is_new_session=False
        )
        assert "favorita" not in ctx

    def test_recencia_aparece_si_busqueda_reciente(self):
        from src.agents.benefits_agent import _build_user_context_block

        hace_30min = (
            datetime.now(timezone.utc) - timedelta(minutes=30)
        ).isoformat()
        prefs = {
            "cat_counts": {}, "day_counts": {},
            "last_categoria": "supermercados",
            "last_searched_at": hace_30min,
        }
        ctx = _build_user_context_block(
            self._profile(), prefs, is_new_session=False
        )
        assert "supermercados" in ctx
        assert "min" in ctx

    def test_recencia_no_aparece_si_muy_antigua(self):
        from src.agents.benefits_agent import _build_user_context_block

        hace_3hs = (
            datetime.now(timezone.utc) - timedelta(hours=3)
        ).isoformat()
        prefs = {
            "cat_counts": {}, "day_counts": {},
            "last_categoria": "supermercados",
            "last_searched_at": hace_3hs,
        }
        ctx = _build_user_context_block(
            self._profile(), prefs, is_new_session=False
        )
        # Más de 2hs → no mostrar recencia
        assert "hace" not in ctx.lower() or "supermercados" not in ctx

    def test_nombre_nunca_en_contexto(self):
        """
        El nombre del cliente NUNCA va en el contexto del LLM.
        El saludo con nombre es responsabilidad exclusiva del prepend
        determinístico en Python (benefits_agent_node).
        Si el LLM viera el nombre lo usaría como apertura en cada turno,
        repitiendo el saludo ("¡Excelente, Roberto!").
        """
        from src.agents.benefits_agent import _build_user_context_block

        prefs = {"cat_counts": {}, "day_counts": {}}

        # Ni en nueva sesión...
        ctx_new = _build_user_context_block(
            self._profile(), prefs, is_new_session=True
        )
        assert "Martin" not in ctx_new
        assert "Nombre del cliente" not in ctx_new

        # ...ni en sesión existente.
        ctx_old = _build_user_context_block(
            self._profile(), prefs, is_new_session=False
        )
        assert "Martin" not in ctx_old
        assert "Nombre del cliente" not in ctx_old

    def test_dias_habituales_aparecen_en_contexto(self):
        from src.agents.benefits_agent import _build_user_context_block

        prefs = {
            "cat_counts": {},
            "day_counts": {"sabado": 3, "domingo": 2},
        }
        ctx = _build_user_context_block(
            self._profile(), prefs, is_new_session=False
        )
        assert "sabado" in ctx or "Días habituales" in ctx


# ════════════════════════════════════════════════════════════════════════
# 6. Flujo end-to-end: clasificación → prefs → context block
# ════════════════════════════════════════════════════════════════════════

class TestEndToEndMemoryFlow:

    @pytest.mark.asyncio
    async def test_user_recurrente_gastronomia_sabados(self):
        """
        Usuario que busca gastronomía los sábados 3 veces.
        La 4ta vez que pregunta 'qué hay hoy?':
        - top_cat debe ser gastronomia
        - el contexto del LLM debe mencionar la categoría favorita
        """
        from src.memory.user_prefs import UserPrefsService
        from src.ui.chat_interface import _get_top_from_prefs
        from src.agents.benefits_agent import _build_user_context_block

        svc = UserPrefsService()
        phone = "+5491100000006"

        with patch.object(svc, "_ensure_connected", return_value=False):
            for _ in range(3):
                await svc.update_search_prefs(
                    phone, "gastronomia", ["sabado"]
                )

            prefs = await svc.load(phone)
            top_cat, top_dias = _get_top_from_prefs(prefs)

            assert top_cat == "gastronomia"
            assert "sabado" in (top_dias or [])

            profile = {
                "identificado": True,
                "nombre": "Ana",
                "segmento": "MASIVO",
                "productos": [],
            }
            ctx = _build_user_context_block(
                profile, prefs, is_new_session=False
            )
            assert "gastronomia" in ctx

    @pytest.mark.asyncio
    async def test_nuevo_usuario_sin_preferencias(self):
        """
        Usuario nuevo sin historial.
        No debe haber menciones de categoría favorita ni recencia.
        """
        from src.agents.benefits_agent import _build_user_context_block

        prefs = {}  # sin preferencias
        profile = {
            "identificado": True,
            "nombre": "Carlos",
            "segmento": "COMAFI UNICO BLACK",
            "productos": ["MASTERCARD MASTERCARD BLACK"],
        }
        ctx = _build_user_context_block(
            profile, prefs, is_new_session=True
        )
        assert "favorita" not in ctx
        assert "Hace" not in ctx
        # El nombre no va en el contexto — lo maneja el prepend de Python
        assert "Carlos" not in ctx
        # Pero el segmento y productos sí deben estar
        assert "BLACK" in ctx
        assert "MASTERCARD" in ctx

    def test_autofill_integrado_con_prefs(self):
        """
        _autofill_today usa los day_counts del mismo dict que
        viene de UserPrefsService.load().
        """
        from src.ui.chat_interface import _autofill_today, _WEEKDAY_KEY
        from datetime import datetime

        hoy_key = _WEEKDAY_KEY[datetime.now().weekday()]
        # Simular prefs cargadas de Redis/memoria
        prefs = {
            "cat_counts": {"gastronomia": 3},
            "day_counts": {hoy_key: 4},
            "ciudad": "cordoba",
        }
        merged_clf = {
            "intent": "benefits",
            "categoria_benefits": "gastronomia",
        }
        result = _autofill_today(merged_clf, prefs)
        assert result["dias"] == [hoy_key]

    @pytest.mark.asyncio
    async def test_memory_fallback_preserva_is_new_session(self):
        """
        Sin Redis, is_new_session se comporta correctamente
        gracias al fallback en memoria.
        """
        from langchain_core.messages import HumanMessage, AIMessage
        from src.memory.conversation_memory import (
            ConversationMemoryService,
            _memory_fallback,
        )
        _memory_fallback.clear()

        svc = ConversationMemoryService()
        phone = "+5491100000007"

        with patch.object(svc, "_ensure_connected", return_value=False):
            # Primera consulta → nueva sesión
            history = await svc.load_history(phone)
            assert len(history) == 0

            await svc.save_messages(phone, [
                HumanMessage(content="quiero ver descuentos"),
                AIMessage(content="Encontré 10 beneficios..."),
            ])

            # Segunda consulta → sesión existente
            history = await svc.load_history(phone)
            assert len(history) == 2
