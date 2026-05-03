import os
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from services.calendar_service import encrypt, create_event, update_event, delete_event, list_today_events
from services.supabase_service import supabase

router = APIRouter()
logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']


def _get_flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {
            'web': {
                'client_id':     os.getenv('GOOGLE_CLIENT_ID'),
                'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
                'redirect_uris': [os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:8000/calendar/callback')],
                'auth_uri':  'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token',
            }
        },
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:8000/calendar/callback')
    return flow


class CalendarEventRequest(BaseModel):
    user_id: str
    patient_id: str
    appointment_id: str
    title: str
    start_datetime: str
    end_datetime: str
    description: str = ''
    meet_link: bool = False


class DisconnectRequest(BaseModel):
    user_id: str


# ── OAuth ──────────────────────────────────────────────────────────────────────

@router.get('/auth-url')
async def get_auth_url(user_id: str):
    flow = _get_flow()
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
        state=user_id,
    )
    return {'auth_url': auth_url}


@router.get('/callback')
async def calendar_callback(code: str, state: str):
    user_id = state
    try:
        flow = _get_flow(state=state)
        flow.fetch_token(code=code)
        creds = flow.credentials

        supabase.table('professional_settings').upsert({
            'user_id': user_id,
            'google_access_token':  encrypt(creds.token),
            'google_refresh_token': encrypt(creds.refresh_token) if creds.refresh_token else None,
            'google_token_expiry':  creds.expiry.isoformat() if creds.expiry else None,
            'google_connected': True,
        }, on_conflict='user_id').execute()

        logger.info(f"Google Calendar conectado para user_id={user_id}")
    except Exception as e:
        logger.error(f"Erro no callback OAuth: {e}")
        return HTMLResponse(content=f"""
            <html><body>
            <p>Erro ao conectar: {e}</p>
            <script>window.close();</script>
            </body></html>
        """)

    return HTMLResponse(content="""
        <html><body style="font-family:sans-serif;text-align:center;padding:40px">
        <h2>✅ Google Calendar conectado!</h2>
        <p>Esta janela fechará automaticamente.</p>
        <script>
          window.opener?.postMessage({ type: 'GOOGLE_CALENDAR_CONNECTED' }, '*');
          setTimeout(() => window.close(), 1500);
        </script>
        </body></html>
    """)


@router.get('/status')
async def calendar_status(user_id: str):
    result = supabase.table('professional_settings') \
        .select('google_connected, google_calendar_id') \
        .eq('user_id', user_id).single().execute()
    if not result.data:
        return {'connected': False, 'calendar_id': None}
    return {
        'connected':   result.data.get('google_connected', False),
        'calendar_id': result.data.get('google_calendar_id'),
    }


@router.post('/disconnect')
async def calendar_disconnect(body: DisconnectRequest):
    supabase.table('professional_settings').update({
        'google_access_token':  None,
        'google_refresh_token': None,
        'google_token_expiry':  None,
        'google_connected': False,
    }).eq('user_id', body.user_id).execute()
    return {'status': 'disconnected'}


# ── Events ─────────────────────────────────────────────────────────────────────

@router.post('/events')
async def create_calendar_event(event: CalendarEventRequest):
    try:
        google_event_id = await create_event(
            user_id=event.user_id,
            event_data={
                'title':          event.title,
                'start':          event.start_datetime,
                'end':            event.end_datetime,
                'description':    event.description,
                'meet_link':      event.meet_link,
                'appointment_id': event.appointment_id,
            },
        )
        supabase.table('appointments').update({
            'google_event_id': google_event_id,
        }).eq('id', event.appointment_id).execute()

        return {'google_event_id': google_event_id}
    except Exception as e:
        logger.error(f"Erro ao criar evento: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/today/{user_id}')
async def get_today_events(user_id: str):
    try:
        events = await list_today_events(user_id)
        return {'events': events}
    except Exception:
        return {'events': [], 'connected': False}
