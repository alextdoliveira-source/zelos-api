import os
from anthropic import Anthropic
import logging

_client: Anthropic | None = None
logger = logging.getLogger(__name__)


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


async def generate_agent_response(
    patient_name: str,
    settings: dict,
    training: str,
    conversation_history: list,
    message: str,
) -> str:
    """Gera resposta do agente sem fluxo de intenção (fallback genérico)."""
    agent_name = settings.get("ai_agent_name", "Sofia")
    prof_name = settings.get("professional_display_name") or settings.get("ai_agent_name", "a profissional")
    personality = settings.get("ai_agent_personality", "acolhedora, profissional e empática")
    biz_start = settings.get("business_hours_start", "08:00")
    biz_end = settings.get("business_hours_end", "18:00")

    system = f"""Você é {agent_name}, assistente virtual da clínica de {prof_name}.

PERSONALIDADE: {personality}
HORÁRIO DE ATENDIMENTO: {biz_start} às {biz_end}

SOBRE A CLÍNICA:
{training or 'Clínica de saúde.'}

PACIENTE: {patient_name}

REGRAS ABSOLUTAS:
- NUNCA dar diagnósticos, orientações clínicas ou médicas
- NUNCA confirmar ao paciente uma ação que não foi efetivada no sistema
- Encaminhar SEMPRE para {prof_name} questões clínicas
- Fora do horário: informar que retornará em breve"""

    messages = conversation_history[-10:] + [{"role": "user", "content": message}]

    response = _get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=system,
        messages=messages,
    )
    return response.content[0].text
