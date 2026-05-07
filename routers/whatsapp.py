import os
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from services.supabase_service import supabase
from services.intent_engine import execute_intent
from services.whatsapp_service import send_text_message

router = APIRouter()
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


def _save_message(patient_id: str, nutritionist_id: str, content: str, sender: str, sender_name: str, whatsapp_id: str = None):
    supabase.table("messages").insert({
        "patient_id": patient_id,
        "nutritionist_id": nutritionist_id,
        "content": content,
        "sender": sender,
        "sender_name": sender_name,
        "whatsapp_message_id": whatsapp_id,
        "status": "sent",
    }).execute()

    supabase.table("patients").update({
        "last_message_at": datetime.now(timezone.utc).isoformat(),
        "last_message_preview": content[:60],
    }).eq("id", patient_id).execute()


def _increment_unread(patient_id: str):
    supabase.rpc("increment_unread", {"p_patient_id": patient_id}).execute()


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    secret = request.query_params.get("secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()

    event = body.get("event", "")
    if event != "messages.upsert":
        return {"status": "ignored"}

    data = body.get("data", {})
    msg_data = data.get("messages", [{}])[0]

    if msg_data.get("key", {}).get("fromMe"):
        return {"status": "ignored"}

    phone_raw = msg_data.get("key", {}).get("remoteJid", "")
    phone = phone_raw.replace("@s.whatsapp.net", "").replace("@g.us", "")
    message_text = (
        msg_data.get("message", {}).get("conversation")
        or msg_data.get("message", {}).get("extendedTextMessage", {}).get("text")
        or ""
    ).strip()

    if not phone or not message_text:
        return {"status": "ignored"}

    logger.info(f"Mensagem recebida de {phone}: {message_text[:60]}")

    patient_result = supabase.table("patients") \
        .select("id, nome, nutritionist_id, handoff_status") \
        .ilike("telefone", f"%{phone[-9:]}%") \
        .limit(1) \
        .execute()

    if not patient_result.data:
        logger.warning(f"Paciente não encontrado para {phone}")
        return {"status": "patient_not_found"}

    patient = patient_result.data[0]
    patient_id = patient["id"]
    patient_name = patient["nome"]
    nutritionist_id = patient["nutritionist_id"]
    handoff_status = patient.get("handoff_status", "ai")

    # Salva mensagem do paciente no inbox
    _save_message(patient_id, nutritionist_id, message_text, "patient", patient_name)
    _increment_unread(patient_id)

    # Salva também em patient_interactions (histórico legado)
    supabase.table("patient_interactions").insert({
        "patient_id": patient_id,
        "type": "whatsapp",
        "content": message_text,
        "direction": "inbound",
    }).execute()

    # Se profissional assumiu a conversa, IA não responde
    if handoff_status == "human":
        logger.info(f"[Inbox] Conversa em modo humano — IA silenciada para {patient_name}")
        return {"status": "ok", "mode": "human"}

    # Busca configurações do profissional
    settings_result = supabase.table("professional_settings") \
        .select("*") \
        .eq("user_id", nutritionist_id) \
        .limit(1) \
        .execute()
    settings = settings_result.data[0] if settings_result.data else {}

    # Busca histórico de conversa
    history_result = supabase.table("patient_interactions") \
        .select("direction, content") \
        .eq("patient_id", patient_id) \
        .order("created_at", desc=True) \
        .limit(10) \
        .execute()

    conversation_history = [
        {
            "role": "user" if i["direction"] == "inbound" else "assistant",
            "content": i["content"],
        }
        for i in reversed(history_result.data or [])
        if i.get("content") not in ["[áudio recebido]", ""]
    ]

    # Gera resposta da IA
    try:
        response_text = await execute_intent(
            patient_id=patient_id,
            user_id=nutritionist_id,
            patient_name=patient_name,
            phone=phone,
            message=message_text,
            settings=settings,
            conversation_history=conversation_history,
        )
    except Exception as e:
        logger.error(f"Erro ao gerar resposta: {e}")
        response_text = "Olá! Estamos com uma instabilidade no momento. Em breve retornaremos. 🙏"

    # Envia via WhatsApp
    await send_text_message(phone, response_text)

    # Salva resposta da IA no inbox
    agent_name = settings.get("agent_name", "Sofia")
    _save_message(patient_id, nutritionist_id, response_text, "ai", agent_name)

    # Salva em patient_interactions
    supabase.table("patient_interactions").insert({
        "patient_id": patient_id,
        "type": "whatsapp",
        "content": response_text,
        "direction": "outbound",
    }).execute()

    return {"status": "ok", "mode": "ai"}


class SendMessageRequest(BaseModel):
    patient_id: str
    message: str
    phone: str


@router.post("/whatsapp/send")
async def send_message_endpoint(body: SendMessageRequest):
    await send_text_message(body.phone, body.message)
    return {"status": "ok", "whatsapp_message_id": None}
