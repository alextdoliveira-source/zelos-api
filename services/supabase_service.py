import os
import logging
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key or key == "your_service_role_key_here":
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY não configurados")
        _client = create_client(url, key)
    return _client


# Alias síncrono — usado pelos routers existentes
class _LazyClient:
    def __getattr__(self, name):
        return getattr(get_supabase(), name)


supabase: Client = _LazyClient()  # type: ignore
