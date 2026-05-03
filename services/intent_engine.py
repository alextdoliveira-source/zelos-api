import os
from anthropic import Anthropic
from services.supabase_service import supabase
import logging

_client: Anthropic | None = None
logger = logging.getLogger(__name__)


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


async def detect_intent(message: str, user_id: str) -> dict | None:
    """Detecta qual intenção configurada melhor corresponde à mensagem."""
    intents = supabase.table("agent_intents") \
        .select("*") \
        .eq("user_id", user_id) \
        .eq("active", True) \
        .execute()

    if not intents.data:
        return None

    intent_list = "\n".join([
        f"- {i['name']}: {i['trigger_description']}"
        for i in intents.data
        if i.get("trigger_description")
    ])

    response = _get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        messages=[{
            "role": "user",
            "content": f"""Identifique qual intenção melhor corresponde à mensagem abaixo.
Responda APENAS com o nome exato da intenção ou "nenhuma" se não corresponder.

Intenções disponíveis:
{intent_list}

Mensagem: "{message}"
"""
        }]
    )

    detected = response.content[0].text.strip().lower()
    if detected == "nenhuma":
        return None

    return next((i for i in intents.data if i["name"] == detected), None)


async def build_agent_prompt(
    patient_name: str,
    settings: dict,
    training: str,
    intent: dict | None,
    context: dict,
    conversation_history: list,
) -> str:
    """Monta o system prompt completo com intenção detectada."""
    agent_name = settings.get("ai_agent_name", "Sofia")
    prof_name = settings.get("professional_display_name") or agent_name
    personality = settings.get("ai_agent_personality", "acolhedora, profissional e empática")
    biz_start = settings.get("business_hours_start", "08:00")
    biz_end = settings.get("business_hours_end", "18:00")

    prompt = f"""Você é {agent_name}, assistente virtual da clínica de {prof_name}.

PERSONALIDADE: {personality}
HORÁRIO DE ATENDIMENTO: {biz_start} às {biz_end}

SOBRE A CLÍNICA:
{training or 'Clínica de saúde.'}

CONTEXTO DO PACIENTE:
- Nome: {patient_name}
- Última consulta: {context.get('ultima_consulta', 'não encontrada')}
- Próxima consulta: {context.get('proxima_consulta', 'não agendada')}
- Total de consultas: {context.get('total_consultas', 0)}

REGRAS ABSOLUTAS:
- NUNCA dar diagnósticos, orientações clínicas ou médicas
- NUNCA confirmar ao paciente uma ação que não foi efetivada no sistema
- NUNCA inventar horários disponíveis — sempre consultar o sistema
- Encaminhar SEMPRE para {prof_name} questões clínicas
- Fora do horário: informar que retornará em breve
"""

    if intent:
        steps = intent.get("steps", [])
        steps_text = "\n".join([
            f"  Passo {s['order']} — {s['title']} (OBRIGATÓRIO): {s['description']}"
            for s in sorted(steps, key=lambda x: x["order"])
        ]) if steps else "  Executar ação de falha imediatamente."

        failure_action = intent.get("failure_action", "transfer")
        failure_msg = intent.get("failure_message", "")

        prompt += f"""
INTENÇÃO DETECTADA: {intent['display_name']}
{intent.get('trigger_description', '')}

FLUXO OBRIGATÓRIO:
{steps_text}

SE FERRAMENTA FALHAR OU ERRO:
  Ação: {failure_action}
  {f'Mensagem: "{failure_msg}"' if failure_msg else ''}
"""

    return prompt


async def execute_intent(
    patient_id: str,
    user_id: str,
    patient_name: str,
    phone: str,
    message: str,
    settings: dict,
    conversation_history: list,
) -> str:
    """Pipeline completo: detecta intenção → monta prompt → gera resposta."""
    intent = await detect_intent(message=message, user_id=user_id)

    # Busca treinamento
    training_result = supabase.table("agent_training") \
        .select("content") \
        .eq("user_id", user_id) \
        .execute()
    training = training_result.data[0]["content"] if training_result.data else ""

    # Busca contexto do paciente
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    appts = supabase.table("appointments") \
        .select("data, status") \
        .eq("patient_id", patient_id) \
        .execute()

    appts_data = appts.data or []
    past = [a for a in appts_data if a["data"] < now]
    future = sorted([a for a in appts_data if a["data"] > now], key=lambda x: x["data"])

    context = {
        "ultima_consulta": past[-1]["data"][:10] if past else "não encontrada",
        "proxima_consulta": future[0]["data"][:10] if future else "não agendada",
        "total_consultas": len(past),
    }

    system_prompt = await build_agent_prompt(
        patient_name=patient_name,
        settings=settings,
        training=training,
        intent=intent,
        context=context,
        conversation_history=conversation_history,
    )

    messages = conversation_history[-10:] + [{"role": "user", "content": message}]

    response = _get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text
