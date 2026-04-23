import operator
from typing import Any, Annotated, Optional

from langchain_core.messages import BaseMessage

try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict


class AgentState(TypedDict):
    """Estado compartido entre todos los agentes."""
    messages: Annotated[list[BaseMessage], operator.add]
    next: str
    context: dict
    session_id: Optional[str]
    audit_service: Optional[Any]
    # Identificación de usuario
    phone_number: Optional[str]   # Número de WhatsApp del usuario
    user_profile: Optional[dict]  # Perfil obtenido de sofia-api-users
    user_prefs:   Optional[dict]  # Preferencias persistentes (ciudad, etc.)
    is_new_session: bool          # True si es la primera consulta de esta sesión


def messages_to_dict(messages: list[BaseMessage]) -> list[dict]:
    """Convierte mensajes LangChain a dicts serializables para auditoría."""
    result = []
    for msg in messages:
        role = msg.__class__.__name__.replace("Message", "").lower()
        content = (
            msg.content if isinstance(msg.content, str) else str(msg.content)
        )
        entry: dict = {"role": role, "content": content[:2000]}
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            entry["tool_calls"] = [
                {"name": tc["name"], "args": tc["args"]}
                for tc in msg.tool_calls
            ]
        if hasattr(msg, "tool_call_id"):
            entry["tool_call_id"] = msg.tool_call_id
        result.append(entry)
    return result
