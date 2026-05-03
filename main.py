import os
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from routers import whatsapp, agent, consultation, calendar, admin
from services.supabase_service import supabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def run_all_cycles():
    """Roda o ciclo do agente para todos os profissionais cadastrados."""
    try:
        settings = supabase.table("professional_settings").select("user_id").execute()
        for row in settings.data or []:
            await agent.agent_cycle(row["user_id"])
    except Exception as e:
        logger.error(f"Erro no ciclo automático: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(run_all_cycles, "interval", hours=1, id="agent_cycle")
    scheduler.start()
    logger.info("Scheduler iniciado — ciclo do agente a cada 1h")
    yield
    scheduler.shutdown()


app = FastAPI(title="Zelos API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(whatsapp.router, tags=["WhatsApp"])
app.include_router(agent.router, tags=["Agent"])
app.include_router(consultation.router, tags=["Consultation"])
app.include_router(calendar.router, prefix="/calendar", tags=["Calendar"])
app.include_router(admin.router, tags=["Admin"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "zelos-api"}
