import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.supabase_service import supabase
from services.whatsapp_service import send_text_message
import anthropic
import os

router = APIRouter()
logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


class GenerateSummaryRequest(BaseModel):
    appointment_id: str
    patient_id: str
    patient_name: str
    raw_notes: str
    specialty: str | None = None


class SendSummaryRequest(BaseModel):
    appointment_id: str
    patient_id: str
    summary: str


@router.post("/consultation/generate-summary")
async def generate_summary(body: GenerateSummaryRequest):
    specialty_context = f"O profissional é da área de {body.specialty}." if body.specialty else ""

    prompt = f"""Você é um assistente de saúde que ajuda profissionais a comunicar o progresso do paciente de forma clara e humanizada.

{specialty_context}

O profissional fez as seguintes anotações durante a consulta com {body.patient_name}:

---
{body.raw_notes}
---

Transforme essas anotações em um resumo de acompanhamento para enviar ao paciente via WhatsApp. O resumo deve:
- Ser escrito em primeira pessoa do profissional ("Olá [nome], na nossa consulta de hoje...")
- Ter tom acolhedor, positivo e motivador
- Mencionar os pontos principais da consulta
- Incluir orientações ou próximos passos se houver nas anotações
- Ter no máximo 5 parágrafos curtos
- NÃO incluir informações clínicas sensíveis ou diagnósticos
- Terminar com uma mensagem encorajadora

Retorne APENAS o texto do resumo, sem explicações adicionais."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = message.content[0].text.strip()

        # Salva no banco
        supabase.table("consultation_notes").upsert(
            {
                "appointment_id": body.appointment_id,
                "patient_id": body.patient_id,
                "raw_notes": body.raw_notes,
                "generated_summary": summary,
                "summary_status": "generated",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="appointment_id",
        ).execute()

        return {"summary": summary}

    except Exception as e:
        logger.error(f"Erro ao gerar resumo: {e}")
        raise HTTPException(status_code=500, detail="Erro ao gerar resumo com IA")


@router.post("/consultation/send-summary")
async def send_summary(body: SendSummaryRequest):
    # Busca telefone do paciente
    patient = supabase.table("patients") \
        .select("telefone, nome") \
        .eq("id", body.patient_id) \
        .single().execute()

    if not patient.data:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")

    phone = patient.data.get("telefone", "").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Paciente sem telefone cadastrado")

    # Envia via WhatsApp
    await send_text_message(phone, body.summary)

    # Atualiza status
    supabase.table("consultation_notes").update({
        "generated_summary": body.summary,
        "summary_status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("appointment_id", body.appointment_id).execute()

    logger.info(f"Resumo enviado para {phone}")
    return {"status": "sent"}
