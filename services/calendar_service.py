import os
import logging
from datetime import datetime, timezone
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from services.supabase_service import supabase

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']

_fernet_key = os.getenv('ENCRYPTION_KEY', '')
fernet = Fernet(_fernet_key.encode()) if _fernet_key else None


def encrypt(value: str) -> str:
    if not fernet:
        return value
    return fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not fernet:
        return value
    return fernet.decrypt(value.encode()).decode()


async def get_credentials(user_id: str) -> Credentials | None:
    result = supabase.table('professional_settings') \
        .select('google_access_token, google_refresh_token, google_token_expiry') \
        .eq('user_id', user_id).single().execute()

    if not result.data or not result.data.get('google_refresh_token'):
        return None

    creds = Credentials(
        token=decrypt(result.data['google_access_token']),
        refresh_token=decrypt(result.data['google_refresh_token']),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
        scopes=SCOPES,
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            supabase.table('professional_settings').update({
                'google_access_token': encrypt(creds.token),
                'google_token_expiry': creds.expiry.isoformat() if creds.expiry else None,
            }).eq('user_id', user_id).execute()
        except Exception as e:
            logger.error(f"Erro ao renovar token: {e}")
            return None

    return creds


async def get_calendar_service(user_id: str):
    creds = await get_credentials(user_id)
    if not creds:
        raise Exception('Google Calendar não conectado')
    return build('calendar', 'v3', credentials=creds)


def _get_calendar_id(user_id: str) -> str:
    result = supabase.table('professional_settings') \
        .select('google_calendar_id').eq('user_id', user_id).single().execute()
    return (result.data or {}).get('google_calendar_id') or 'primary'


async def create_event(user_id: str, event_data: dict) -> str:
    service = await get_calendar_service(user_id)
    calendar_id = _get_calendar_id(user_id)

    event = {
        'summary': event_data['title'],
        'description': event_data.get('description', ''),
        'start': {'dateTime': event_data['start'], 'timeZone': 'America/Sao_Paulo'},
        'end':   {'dateTime': event_data['end'],   'timeZone': 'America/Sao_Paulo'},
    }

    conference_version = 0
    if event_data.get('meet_link'):
        event['conferenceData'] = {
            'createRequest': {'requestId': event_data.get('appointment_id', 'zelos')}
        }
        conference_version = 1

    result = service.events().insert(
        calendarId=calendar_id,
        body=event,
        conferenceDataVersion=conference_version,
    ).execute()

    return result['id']


async def update_event(user_id: str, google_event_id: str, event_data: dict):
    service = await get_calendar_service(user_id)
    calendar_id = _get_calendar_id(user_id)

    event = service.events().get(calendarId=calendar_id, eventId=google_event_id).execute()
    event['summary'] = event_data.get('title', event['summary'])
    event['start'] = {'dateTime': event_data['start'], 'timeZone': 'America/Sao_Paulo'}
    event['end']   = {'dateTime': event_data['end'],   'timeZone': 'America/Sao_Paulo'}

    service.events().update(calendarId=calendar_id, eventId=google_event_id, body=event).execute()


async def delete_event(user_id: str, google_event_id: str):
    service = await get_calendar_service(user_id)
    calendar_id = _get_calendar_id(user_id)
    try:
        service.events().delete(calendarId=calendar_id, eventId=google_event_id).execute()
    except Exception as e:
        logger.warning(f"Evento {google_event_id} não encontrado no Calendar: {e}")


async def list_today_events(user_id: str) -> list:
    service = await get_calendar_service(user_id)
    calendar_id = _get_calendar_id(user_id)

    now = datetime.now(timezone.utc)
    start = now.replace(hour=0,  minute=0,  second=0,  microsecond=0).isoformat()
    end   = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=start,
        timeMax=end,
        singleEvents=True,
        orderBy='startTime',
    ).execute()

    return result.get('items', [])
