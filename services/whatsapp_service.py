import os
import httpx
import logging

logger = logging.getLogger(__name__)

EVOLUTION_URL = os.environ.get("EVOLUTION_API_URL", "")
EVOLUTION_KEY = os.environ.get("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "zelos")


async def send_text_message(number: str, message: str) -> bool:
    """Envia mensagem de texto via Evolution API."""
    if not EVOLUTION_URL or not EVOLUTION_KEY:
        logger.warning("Evolution API não configurada — mensagem não enviada")
        return False

    # Remove caracteres não numéricos do número
    number_clean = "".join(filter(str.isdigit, number))
    if not number_clean.endswith("@s.whatsapp.net"):
        number_clean = f"{number_clean}@s.whatsapp.net"

    url = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
    payload = {"number": number_clean, "text": message}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Erro ao enviar WhatsApp para {number}: {e}")
        return False
