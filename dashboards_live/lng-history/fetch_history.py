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
from datetime import date, datetime, timedelta

import requests

KEY = os.environ.get('AGSI_KEY')
MAX_MINUTES = float(os.environ.get('MAX_MINUTES', '300'))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'history.json')

HISTORY_START = date(2011, 1, 1)   # primo giorno disponibile su AGSI
CHUNK_DAYS = 299                   # una pagina da 300 righe copre 299 giorni
OVERLAP_DAYS = 10
RETRY_SLEEP = 65
MAX_RETRIES = 8

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
            msg = str(body.get('message', '')) + str(body.get('error', ''))
            if 'limit' in msg.lower() or 'many' in msg.lower():
                print(f'  limite chiamate, attendo {RETRY_SLEEP}s (tentativo {attempt + 1})')
                time.sleep(RETRY_SLEEP)
                continue
            raise RuntimeError(f'errore API: {msg}')
        return body
    raise RuntimeError('troppi tentativi, API ancora limitata')


def fetch_range(code, d_from, d_to):
    """Ritorna {data ISO: percentuale} per il paese nell'intervallo."""
    param = 'type=EU' if code == 'EU' else f'country={code}'
    out = {}
    page = 1
    while True:
        url = (f'https://agsi.gie.eu/api?{param}&from={d_from.isoformat()}'
               f'&to={d_to.isoformat()}&size=300&page={page}')
        body = get_json(url)
        rows = body.get('data', [])
        if not rows:
            break
        new = False
        for row in rows:
            day, full = row.get('gasDayStart'), row.get('full')
            if day and full not in (None, '') and day not in out:
                out[day] = round(float(full), 1)
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
    payload = {'updated': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'countries': countries}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, separators=(',', ':'))


def main():
    if not KEY:
        sys.exit('AGSI_KEY mancante')

    store = load_existing()
    today = date.today()
    complete = True

    for code in COUNTRIES:
        days = store.setdefault(code, {})
        cur = max(HISTORY_START, date.fromisoformat(max(days)) - timedelta(days=OVERLAP_DAYS)) if days else HISTORY_START
        print(f'{code}: da {cur.isoformat()} ({len(days)} giorni gia\' salvati)')

        while cur <= today:
            if time.time() > deadline:
                complete = False
                break
            end = min(cur + timedelta(days=CHUNK_DAYS - 1), today)
            try:
                days.update(fetch_range(code, cur, end))
            except Exception as e:
                print(f'  errore {code} {cur}..{end}: {e}')
                complete = False
                break
            cur = end + timedelta(days=1)

        save(store)   # salva dopo ogni paese e dopo ogni interruzione
        if not complete:
            break

    for code, days in store.items():
        if days:
            print(f'{code}: {min(days)} -> {max(days)}, {len(days)} giorni')
    print('Completo.' if complete else 'Parziale: il prossimo run riprende da qui.')


if __name__ == '__main__':
    main()
