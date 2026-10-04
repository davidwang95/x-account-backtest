"""Shared local contracts. No provider credentials or AI calls."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')

def account_handle(value: str) -> str:
    value = value.strip()
    if '://' in value or value.lower().startswith(('x.com/', 'twitter.com/', 'www.x.com/', 'www.twitter.com/')):
        parsed = urlsplit(value if '://' in value else 'https://' + value)
        if parsed.hostname not in {'x.com','www.x.com','twitter.com','www.twitter.com'} or parsed.username or parsed.password:
            raise ValueError('Use an X handle or an x.com/twitter.com profile URL.')
        parts = parsed.path.strip('/').split('/')
        if len(parts) != 1:
            raise ValueError('Supply a profile, not a post URL.')
        value = parts[0]
    value = value.removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', value) or value.lower() in {'home','search','settings','login','i','explore','intent'}:
        raise ValueError('Invalid X account handle.')
    return value.lower()

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def publication_day(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('A publication timestamp must include its timezone.')
    return parsed.astimezone(NY).date().isoformat()

def safe_csv_cell(value):
    # Verbatim source text is kept in JSON; spreadsheet exports quote formulas.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value

def safe_https_url(value):
    try:
        parsed = urlsplit(str(value))
        return (parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
                and parsed.port in (None,443) and not any(c.isspace() or ord(c)<32 for c in str(value))
                and not any(key.lower() in {'apikey','api_key','token','key','authorization','access_token'} for key,_ in parse_qsl(parsed.query)))
    except (ValueError,TypeError):
        return False

def date_window(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError('Publication start must be on or before end.')
    begin = datetime.combine(first, datetime.min.time(), NY)
    finish = datetime.combine(last + timedelta(days=1), datetime.min.time(), NY)
    return begin.astimezone(timezone.utc).isoformat(), finish.astimezone(timezone.utc).isoformat()
