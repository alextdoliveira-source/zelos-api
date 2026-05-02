from pydantic import BaseModel
from typing import Optional, Any


class WhatsAppMessage(BaseModel):
    event: str
    instance: str
    data: dict


class ActionApprove(BaseModel):
    action_id: str


class ActionReject(BaseModel):
    action_id: str
    reason: Optional[str] = None


class AgentRunRequest(BaseModel):
    user_id: str


class SeedIntentsRequest(BaseModel):
    user_id: str
