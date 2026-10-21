"""Scarica la serie storica giornaliera di riempimento (%) da AGSI per ogni paese
e la salva in history.json accanto a questo script.

Riprende da dove era arrivato: se history.json esiste, per ogni paese riparte
dall'ultimo giorno salvato (meno qualche giorno di sovrapposizione per le
correzioni di AGSI). Se l'API limita le chiamate, aspetta e riprova; se supera
il budget di tempo salva quello che ha e esce senza errore, il run successivo
continua.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

KEY = os.environ.get('AGSI_KEY')
MAX_MINUTES = float(os.environ.get('MAX_MINUTES', '300'))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'history.json')

HISTORY_START = date(2011, 1, 1)   # primo giorno disponibile su AGSI
CHUNK_DAYS = 299                   # una pagina da 300 righe copre 299 giorni
OVERLAP_DAYS = 10
RETRY_SLEEP = 65
MAX_RETRIES = 5
CALL_PAUSE = 2   # secondi tra una chiamata e l'altra, per non inondare l'API

# Paesi presenti nell'elenco di https://agsi.gie.eu/api/about?show=listing.
COUNTRIES = {
    'EU': 'Europa',
    'AT': 'Austria', 'BE': 'Belgio', 'BG': 'Bulgaria', 'HR': 'Croazia',
    'CZ': 'Repubblica Ceca', 'DK': 'Danimarca', 'FR': 'Francia',
    'DE': 'Germania', 'HU': 'Ungheria', 'IE': 'Irlanda', 'IT': 'Italia',
    'LV': 'Lettonia', 'NL': 'Paesi Bassi', 'PL': 'Polonia', 'PT': 'Portogallo',
    'RO': 'Romania', 'SK': 'Slovacchia', 'ES': 'Spagna', 'SE': 'Svezia',
    'GB': 'Regno Unito', 'UA': 'Ucraina',
}

deadline = time.time() + MAX_MINUTES * 60


def get_json(url):
    """GET con attesa e nuovi tentativi se l'API risponde con un limite di chiamate."""
    for attempt in range(MAX_RETRIES):
        res = requests.get(url, headers={'x-key': KEY}, timeout=60)
        try:
            body = res.json()
        except ValueError:
            body = None
        if res.status_code == 429 or (body is None and 'many' in res.text.lower()):
            print(f'  limite chiamate, attendo {RETRY_SLEEP}s (tentativo {attempt + 1})')
            time.sleep(RETRY_SLEEP)
            continue
        if body is None:
            raise RuntimeError(f'risposta non JSON ({res.status_code}): {res.text[:200]}')
        if body.get('error'):
            msg = str(body.get('message', '')) + ' ' + str(body.get('error', ''))
            # Chiave sbagliata o accesso negato: inutile riprovare.
            if 'access denied' in msg.lower() or 'invalid' in msg.lower():
                raise RuntimeError(f'errore API: {msg}')
            # Ogni altro errore (anche "Try/Catch error") e' trattato come un
            # blocco temporaneo: attesa e nuovo tentativo.
            print(f'  errore API "{msg.strip()}", attendo {RETRY_SLEEP}s (tentativo {attempt + 1}/{MAX_RETRIES})', flush=True)
            time.sleep(RETRY_SLEEP)
            continue
        time.sleep(CALL_PAUSE)
        return body
    raise RuntimeError('troppi tentativi, API ancora in errore')


def parse_full(value):
    """Percentuale con un decimale, None se il dato manca (AGSI scrive '-' prima
    che un paese abbia dati)."""
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def fetch_range(code, d_from, d_to, expect_rows=False):
    """Ritorna {data ISO: percentuale} per il paese nell'intervallo.

    expect_rows: il paese ha gia' dati prima di questo intervallo, quindi una
    risposta vuota e' sospetta (a volte AGSI risponde vuoto dopo un errore):
    si riprova un paio di volte prima di accettarla.
    """
    param = 'type=EU' if code == 'EU' else f'country={code}'
    out = {}
    page = 1
    empty_tries = 0
    while True:
        url = (f'https://agsi.gie.eu/api?{param}&from={d_from.isoformat()}'
               f'&to={d_to.isoformat()}&size=300&page={page}')
        body = get_json(url)
        rows = body.get('data', [])
        if not rows:
            if page == 1 and expect_rows and empty_tries < 2:
                empty_tries += 1
                print(f'  risposta vuota {code} {d_from}..{d_to}, riprovo tra {RETRY_SLEEP}s', flush=True)
                time.sleep(RETRY_SLEEP)
                continue
            break
        new = False
        for row in rows:
            day, val = row.get('gasDayStart'), parse_full(row.get('full'))
            if day and val is not None and day not in out:
                out[day] = val
                new = True
        if not new or page >= int(body.get('last_page', 1) or 1):
            break
        page += 1
    return out


def load_existing():
    """Legge history.json e lo riporta a {codice: {data ISO: valore}}."""
    if not os.path.exists(OUT):
        return {}
    with open(OUT, encoding='utf-8') as f:
        data = json.load(f)
    result = {}
    for code, c in data.get('countries', {}).items():
        start = date.fromisoformat(c['start'])
        result[code] = {
            (start + timedelta(days=i)).isoformat(): v
            for i, v in enumerate(c['values']) if v is not None
        }
    return result


def save(store):
    countries = {}
    for code, days in store.items():
        if not days:
            continue
        first = date.fromisoformat(min(days))
        last = date.fromisoformat(max(days))
        values = []
        cur = first
        while cur <= last:
            values.append(days.get(cur.isoformat()))
            cur += timedelta(days=1)
        countries[code] = {'name': COUNTRIES[code], 'start': first.isoformat(), 'values': values}
    payload = {'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'countries': countries}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, separators=(',', ':'))


def find_gaps(days):
    """Intervalli di giorni mancanti tra il primo e l'ultimo giorno salvato."""
    if not days:
        return []
    cur = date.fromisoformat(min(days))
    last = date.fromisoformat(max(days))
    gaps, start = [], None
    while cur <= last:
        if cur.isoformat() not in days:
            start = start or cur
        elif start:
            gaps.append((start, cur - timedelta(days=1)))
            start = None
        cur += timedelta(days=1)
    return gaps


def main():
    if not KEY:
        sys.exit('AGSI_KEY mancante')

    store = load_existing()
    today = date.today()
    complete = True

    for code in COUNTRIES:
        days = store.setdefault(code, {})

        # Prima riscarica i buchi lasciati da esecuzioni precedenti.
        for g_from, g_to in find_gaps(days):
            print(f'{code}: buco {g_from}..{g_to}, riscarico', flush=True)
            cur = g_from
            while cur <= g_to and time.time() <= deadline:
                end = min(cur + timedelta(days=CHUNK_DAYS - 1), g_to)
                try:
                    days.update(fetch_range(code, cur, end, expect_rows=True))
                except Exception as e:
                    print(f'  errore {code} {cur}..{end}: {e}', flush=True)
                    complete = False
                    break
                cur = end + timedelta(days=1)

        cur = max(HISTORY_START, date.fromisoformat(max(days)) - timedelta(days=OVERLAP_DAYS)) if days else HISTORY_START
        print(f'{code}: da {cur.isoformat()} ({len(days)} giorni gia\' salvati)', flush=True)

        while cur <= today:
            if time.time() > deadline:
                complete = False
                break
            end = min(cur + timedelta(days=CHUNK_DAYS - 1), today)
            try:
                days.update(fetch_range(code, cur, end, expect_rows=bool(days)))
            except Exception as e:
                # Questo paese si ferma qui, il prossimo run riprende da dove
                # era arrivato; si passa comunque agli altri paesi.
                print(f'  errore {code} {cur}..{end}: {e}', flush=True)
                complete = False
                break
            cur = end + timedelta(days=1)

        save(store)   # salva dopo ogni paese
        if time.time() > deadline:
            break

    for code, days in store.items():
        if days:
            print(f'{code}: {min(days)} -> {max(days)}, {len(days)} giorni')
    print('Completo.' if complete else 'Parziale: il prossimo run riprende da qui.')


if __name__ == '__main__':
    main()
