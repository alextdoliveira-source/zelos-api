import os
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Header
from typing import Optional
from services.supabase_service import supabase

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger(__name__)


def _check_secret(secret: Optional[str]):
    expected = os.environ.get("ADMIN_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/stats")
async def admin_stats(x_admin_secret: Optional[str] = Header(default=None)):
    _check_secret(x_admin_secret)

    # Profissionais
    profs_res = supabase.table("nutritionists") \
        .select("id, nome, especialidade, user_id, created_at").execute()
    profs = profs_res.data or []

    # Pacientes (todos)
    patients_res = supabase.table("patients") \
        .select("id, user_id, kanban_status, created_at").execute()
    patients = patients_res.data or []

    # Consultas (todos)
    appts_res = supabase.table("appointments") \
        .select("id, nutritionist_id, status, data").execute()
    appts = appts_res.data or []

    # Interações WhatsApp (todos)
    interactions_res = supabase.table("patient_interactions") \
        .select("id, user_id, direction, created_at").execute()
    interactions = interactions_res.data or []

    # Sinais de IA não resolvidos
    signals_res = supabase.table("ai_signals") \
        .select("id, patient_id").eq("resolved", False).execute()
    signals = signals_res.data or []

    # Agent configs
    agents_res = supabase.table("agent_configs") \
        .select("user_id, status_config").execute()
    agents = agents_res.data or []

    # Mapear patient_id → user_id (para cruzar sinais)
    patient_user_map: dict[str, str] = {p["id"]: p["user_id"] for p in patients}

    # Mapear nutritionist.id (UUID) → user_id (para cruzar consultas)
    prof_id_to_user: dict[str, str] = {p["id"]: p["user_id"] for p in profs}

    # Montar dicionário por user_id
    prof_map: dict[str, dict] = {}
    for p in profs:
        uid = p["user_id"]
        prof_map[uid] = {
            "nome": p["nome"],
            "especialidade": p.get("especialidade") or "",
            "member_since": p.get("created_at", "")[:10],
            "patients": 0,
            "appointments": 0,
            "messages_sent": 0,
            "open_signals": 0,
            "agent_status": "sem agente",
        }

    for pt in patients:
        uid = pt.get("user_id")
        if uid in prof_map:
            prof_map[uid]["patients"] += 1

    for a in appts:
        nid = a.get("nutritionist_id")
        uid = prof_id_to_user.get(nid)
        if uid and uid in prof_map:
            prof_map[uid]["appointments"] += 1

    for i in interactions:
        uid = i.get("user_id")
        if uid in prof_map and i.get("direction") == "outbound":
            prof_map[uid]["messages_sent"] += 1

    for s in signals:
        pid = s.get("patient_id")
        uid = patient_user_map.get(pid)
        if uid and uid in prof_map:
            prof_map[uid]["open_signals"] += 1

    for ag in agents:
        uid = ag.get("user_id")
        if uid in prof_map:
            sc = ag.get("status_config", "")
            prof_map[uid]["agent_status"] = "ativo" if sc == "ativo" else "configurando" if sc == "configurando" else "inativo"

    # Mensagens últimas 24h (global)
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    msgs_24h = sum(
        1 for i in interactions
        if i.get("created_at", "") >= since and i.get("direction") == "outbound"
    )

    # Consultas hoje
    today = datetime.now(timezone.utc).date().isoformat()
    appts_today = sum(1 for a in appts if a.get("data") == today)

    return {
        "totals": {
            "professionals": len(profs),
            "patients": len(patients),
            "appointments": len(appts),
            "appointments_today": appts_today,
            "messages_sent_24h": msgs_24h,
            "open_signals": len(signals),
        },
        "professionals": list(prof_map.values()),
    }


@router.get("/professionals")
async def list_professionals(x_admin_secret: Optional[str] = Header(default=None)):
    _check_secret(x_admin_secret)
    res = supabase.table("nutritionists").select("*").execute()
    return res.data or []
