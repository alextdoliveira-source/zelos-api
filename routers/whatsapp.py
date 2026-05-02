import os
import logging
from fastapi import APIRouter, Request, HTTPException
from services.supabase_service import supabase
from services.intent_engine import execute_intent
from services.whatsapp_service import send_text_message

router = APIRouter()
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    # Valida secret via query param
    secret = request.query_params.get("secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()

    # Evolution API envia evento 'messages.upsert'
    event = body.get("event", "")
    if event != "messages.upsert":
        return {"status": "ignored"}

    data = body.get("data", {})
    msg_data = data.get("messages", [{}])[0]

    # Filtra mensagens enviadas pelo próprio bot
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

    # Busca paciente pelo telefone
    patient_result = supabase.table("patients") \
        .select("id, nome, user_id") \
        .ilike("telefone", f"%{phone[-9:]}%") \
        .limit(1) \
        .execute()

    if not patient_result.data:
        logger.warning(f"Paciente não encontrado para {phone}")
        return {"status": "patient_not_found"}

    patient = patient_result.data[0]
    patient_id = patient["id"]
    patient_name = patient["nome"]
    user_id = patient["user_id"]

    # Busca configurações do profissional
    settings_result = supabase.table("professional_settings") \
        .select("*") \
        .eq("user_id", user_id) \
        .limit(1) \
        .execute()
    settings = settings_result.data[0] if settings_result.data else {}

    # Busca histórico de conversa (últimas 10 interações)
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

    # Salva mensagem recebida
    supabase.table("patient_interactions").insert({
        "patient_id": patient_id,
        "type": "whatsapp",
        "content": message_text,
        "direction": "inbound",
    }).execute()

    # Gera resposta via engine de intenções
    try:
        response_text = await execute_intent(
            patient_id=patient_id,
            user_id=user_id,
            patient_name=patient_name,
            phone=phone,
            message=message_text,
            settings=settings,
            conversation_history=conversation_history,
        )
    except Exception as e:
        logger.error(f"Erro ao gerar resposta: {e}")
        response_text = "Olá! Estamos com uma instabilidade no momento. Em breve retornaremos. 🙏"

    # Envia resposta
    await send_text_message(phone, response_text)

    # Salva resposta enviada
    supabase.table("patient_interactions").insert({
        "patient_id": patient_id,
        "type": "whatsapp",
        "content": response_text,
        "direction": "outbound",
    }).execute()

    return {"status": "ok"}
