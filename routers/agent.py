import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from services.supabase_service import supabase
from services.whatsapp_service import send_text_message
from models.schemas import ActionApprove, ActionReject

router = APIRouter()
logger = logging.getLogger(__name__)

DEFAULT_INTENTS = [
    {
        "name": "reagendamento",
        "display_name": "Reagendamento",
        "trigger_description": "Paciente menciona remarcar, reagendar, trocar horário, não pode ir ou mudar consulta.",
        "steps": [
            {"order": 1, "title": "Identificar consulta", "description": "Pergunte qual consulta deseja reagendar se houver mais de uma futura. Confirme data e horário antes de prosseguir.", "required": True},
            {"order": 2, "title": "Verificar disponibilidade", "description": "Consulte os horários disponíveis. Pergunte preferência de período (manhã/tarde). Ofereça apenas um horário por vez.", "required": True},
            {"order": 3, "title": "Efetivar no sistema", "description": "Reagende no sistema antes de confirmar ao paciente. Nunca informe novo horário sem ter efetivado a mudança.", "required": True},
            {"order": 4, "title": "Confirmar ao paciente", "description": "Informe o novo dia e horário. Avise que um lembrete será enviado com algumas horas de antecedência.", "required": True},
        ],
        "failure_action": "transfer",
        "failure_message": "Vou te encaminhar para a equipe agora pra ela te ajudar com isso, tá bom? Em alguns minutinhos alguém da clínica fala com você por aqui.",
    },
    {
        "name": "confirmacao",
        "display_name": "Confirmação de consulta",
        "trigger_description": "Paciente quer confirmar presença em consulta agendada.",
        "steps": [
            {"order": 1, "title": "Identificar agendamento", "description": "Pergunte qual consulta quer confirmar se houver mais de uma futura.", "required": True},
            {"order": 2, "title": "Confirmar no sistema", "description": "Efetive a confirmação no sistema. Nunca afirme ao paciente que confirmou sem ter efetivado.", "required": True},
            {"order": 3, "title": "Responder ao paciente", "description": "Se janela não chegou: avise que lembrete será enviado. Se passou: ofereça reagendamento.", "required": True},
        ],
        "failure_action": "transfer",
        "failure_message": "Vou te encaminhar para a equipe agora pra ela te ajudar com isso, tá bom?",
    },
    {
        "name": "cancelamento",
        "display_name": "Cancelamento",
        "trigger_description": "Paciente quer cancelar uma consulta agendada.",
        "steps": [
            {"order": 1, "title": "Identificar consulta", "description": "Confirme qual consulta deseja cancelar.", "required": True},
            {"order": 2, "title": "Tentar salvar", "description": "Pergunte o motivo e ofereça reagendamento antes de cancelar. Mostre disponibilidade.", "required": True},
            {"order": 3, "title": "Escalar se insistir", "description": "Se paciente confirmar cancelamento: escalar para profissional antes de efetivar.", "required": True},
        ],
        "failure_action": "escalate",
        "failure_message": "",
    },
    {
        "name": "duvida_clinica",
        "display_name": "Dúvida clínica",
        "trigger_description": "Paciente pergunta sobre diagnóstico, medicamentos, sintomas, dieta específica ou qualquer orientação clínica.",
        "steps": [
            {"order": 1, "title": "Transferir imediatamente", "description": "Nunca responder questões clínicas. Sempre transferir para o profissional.", "required": True},
        ],
        "failure_action": "transfer",
        "failure_message": "Essa é uma ótima pergunta! Vou passar para a profissional responder com mais precisão. Ela retorna com você em breve. 😊",
    },
    {
        "name": "erro_ferramenta",
        "display_name": "Erro / ferramenta falhou",
        "trigger_description": "Ferramenta retornou erro, indisponibilidade, falha de integração, retorno vazio inesperado.",
        "steps": [],
        "failure_action": "transfer",
        "failure_message": "Vou te encaminhar para a equipe agora pra ela te ajudar com isso, tá bom? Em alguns minutinhos alguém da clínica fala com você por aqui.",
    },
    {
        "name": "pedido_depoimento",
        "display_name": "Pedido de depoimento",
        "trigger_description": "Paciente responde a mensagem de pedido de depoimento ou feedback.",
        "steps": [
            {"order": 1, "title": "Analisar sentimento", "description": "Se positivo: salvar depoimento. Se negativo: escalar para profissional.", "required": True},
            {"order": 2, "title": "Agradecer e confirmar", "description": "Agradecer pelo depoimento e informar que será muito útil para outras pessoas.", "required": True},
        ],
        "failure_action": "respond",
        "failure_message": "Obrigado pelo seu retorno! Suas palavras são muito importantes para nós. 💚",
    },
]


# ── Ciclo do agente ──────────────────────────────────────────────────────────

async def agent_cycle(user_id: str):
    """Roda as 5 regras de acompanhamento para todos os pacientes do profissional."""
    logger.info(f"Iniciando ciclo do agente para user_id={user_id}")

    settings_result = supabase.table("professional_settings") \
        .select("*").eq("user_id", user_id).limit(1).execute()
    settings = settings_result.data[0] if settings_result.data else {}

    followup_days = int(settings.get("followup_days_after", 3))
    reminder_hours = int(settings.get("reminder_hours_before", 24))

    patients = supabase.table("patients") \
        .select("id, nome, telefone") \
        .eq("user_id", user_id) \
        .execute().data or []

    now = datetime.now(timezone.utc)

    for patient in patients:
        patient_id = patient["id"]
        patient_name = patient["nome"]
        phone = patient.get("telefone", "")

        appts = supabase.table("appointments") \
            .select("data, horario, status") \
            .eq("patient_id", patient_id) \
            .execute().data or []

        past = sorted([a for a in appts if a["data"] < now.date().isoformat()], key=lambda x: x["data"])
        future = sorted([a for a in appts if a["data"] >= now.date().isoformat()], key=lambda x: x["data"])

        # Regra 1 — Lembrete pré-consulta (24h antes)
        if future and settings.get("reminder_d1_enabled", True):
            next_appt = future[0]
            appt_dt = datetime.fromisoformat(f"{next_appt['data']}T{next_appt['horario']}")
            diff_hours = (appt_dt - now.replace(tzinfo=None)).total_seconds() / 3600
            if reminder_hours - 1 <= diff_hours <= reminder_hours + 1:
                _create_action(patient_id, "send_reminder", {
                    "phone": phone,
                    "patient_name": patient_name,
                    "appointment_date": next_appt["data"],
                    "appointment_time": next_appt["horario"],
                    "message": f"Olá {patient_name}! 😊 Lembrando que você tem consulta amanhã às {next_appt['horario']}. Confirma sua presença?",
                })

        # Regra 2 — Follow-up pós-consulta
        if past and settings.get("followup_enabled", True):
            last_appt = past[-1]
            days_since = (now.date() - datetime.fromisoformat(last_appt["data"]).date()).days
            if days_since == followup_days:
                _create_signal(patient_id, "follow_up_due", "info",
                    f"{patient_name} teve consulta há {followup_days} dias — follow-up pendente")

        # Regra 3 — Risco de abandono (sem retorno há 30+ dias)
        if past and settings.get("abandonment_enabled", True):
            last_appt = past[-1]
            days_since = (now.date() - datetime.fromisoformat(last_appt["data"]).date()).days
            if days_since >= 30 and not future:
                _create_signal(patient_id, "abandonment_risk", "warning",
                    f"{patient_name} sem retorno há {days_since} dias")

        # Regra 4 — Sem histórico algum (novo lead)
        if not past and not future:
            _create_signal(patient_id, "no_return", "info",
                f"{patient_name} nunca teve consulta agendada")

    logger.info(f"Ciclo concluído para user_id={user_id}")


def _create_action(patient_id: str, action_type: str, payload: dict):
    """Cria ação pendente se ainda não existir hoje."""
    today = datetime.now(timezone.utc).date().isoformat()
    existing = supabase.table("agent_actions") \
        .select("id") \
        .eq("patient_id", patient_id) \
        .eq("action_type", action_type) \
        .gte("created_at", today) \
        .limit(1).execute()
    if not existing.data:
        supabase.table("agent_actions").insert({
            "patient_id": patient_id,
            "action_type": action_type,
            "payload": payload,
            "status": "pending",
        }).execute()


def _create_signal(patient_id: str, signal_type: str, severity: str, message: str):
    """Cria sinal de IA se ainda não existir não-resolvido."""
    existing = supabase.table("ai_signals") \
        .select("id") \
        .eq("patient_id", patient_id) \
        .eq("signal_type", signal_type) \
        .eq("resolved", False) \
        .limit(1).execute()
    if not existing.data:
        supabase.table("ai_signals").insert({
            "patient_id": patient_id,
            "signal_type": signal_type,
            "severity": severity,
            "message": message,
        }).execute()


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/agent/run")
async def run_agent(user_id: str):
    await agent_cycle(user_id)
    return {"status": "ok"}


@router.get("/agent/signals/{user_id}")
async def get_signals(user_id: str):
    patients = supabase.table("patients") \
        .select("id") \
        .eq("user_id", user_id) \
        .execute().data or []
    patient_ids = [p["id"] for p in patients]

    if not patient_ids:
        return []

    signals = supabase.table("ai_signals") \
        .select("*, patients(nome, telefone)") \
        .in_("patient_id", patient_ids) \
        .eq("resolved", False) \
        .order("created_at", desc=True) \
        .execute()

    return signals.data or []


@router.post("/agent/action/approve")
async def approve_action(body: ActionApprove):
    action = supabase.table("agent_actions") \
        .select("*").eq("id", body.action_id).single().execute()
    if not action.data:
        raise HTTPException(status_code=404, detail="Ação não encontrada")

    payload = action.data.get("payload", {})
    action_type = action.data.get("action_type")

    if action_type == "send_reminder" and payload.get("phone"):
        await send_text_message(payload["phone"], payload["message"])

    supabase.table("agent_actions").update({
        "status": "sent",
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", body.action_id).execute()

    return {"status": "sent"}


@router.post("/agent/action/reject")
async def reject_action(body: ActionReject):
    supabase.table("agent_actions").update({
        "status": "rejected",
    }).eq("id", body.action_id).execute()
    return {"status": "rejected"}


@router.get("/agent/pending-actions/{user_id}")
async def get_pending_actions(user_id: str):
    patients = supabase.table("patients") \
        .select("id").eq("user_id", user_id).execute().data or []
    patient_ids = [p["id"] for p in patients]
    if not patient_ids:
        return []

    actions = supabase.table("agent_actions") \
        .select("*, patients(nome)") \
        .in_("patient_id", patient_ids) \
        .eq("status", "pending") \
        .order("created_at", desc=True) \
        .execute()
    return actions.data or []


@router.get("/agent/default-intents")
async def get_default_intents():
    return {"intents": DEFAULT_INTENTS}


@router.post("/agent/intents/seed/{user_id}")
async def seed_intents(user_id: str):
    existing = supabase.table("agent_intents") \
        .select("id").eq("user_id", user_id).limit(1).execute()
    if existing.data:
        return {"status": "already_seeded", "count": 0}

    for intent in DEFAULT_INTENTS:
        supabase.table("agent_intents").insert({**intent, "user_id": user_id}).execute()

    return {"status": "seeded", "count": len(DEFAULT_INTENTS)}
