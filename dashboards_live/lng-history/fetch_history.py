"""Scarica la serie storica giornaliera da AGSI per ogni paese e la salva in
history.json accanto a questo script.

Due blocchi indipendenti:
  - "countries": riempimento % dal 2011 (formato invariato, la dashboard dei
    livelli continua a leggerlo senza modifiche)
  - "flows": prelievo, iniezione e capacita' di prelievo/iniezione dal 2021,
    in GWh/giorno

Riprende da dove era arrivato: per ogni paese e per ogni blocco riparte
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
FLOW_START = date(2021, 1, 1)      # i flussi si tengono solo da qui in poi
CHUNK_DAYS = 299                   # una pagina da 300 righe copre 299 giorni
OVERLAP_DAYS = 10
RETRY_SLEEP = 65
MAX_RETRIES = 5
CALL_PAUSE = 2   # secondi tra una chiamata e l'altra, per non inondare l'API

# Campi di flusso salvati, nell'ordine in cui finiscono nel JSON.
FLOW_FIELDS = ('withdrawal', 'injection', 'withdrawalCapacity', 'injectionCapacity')

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


def parse_num(value, nd=1):
    """Numero arrotondato, None se il dato manca (AGSI scrive '-' prima che un
    paese abbia dati)."""
    try:
        return round(float(value), nd)
    except (TypeError, ValueError):
        return None


def fetch_range(code, d_from, d_to, expect_rows=False):
    """Ritorna {data ISO: riga} per il paese nell'intervallo.

    Ogni riga e' {'full': %, 'withdrawal': GWh/g, ...} con None dove manca.

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
            day = row.get('gasDayStart')
            if not day or day in out:
                continue
            rec = {'full': parse_num(row.get('full'), 1)}
            for f in FLOW_FIELDS:
                rec[f] = parse_num(row.get(f), 1)
            out[day] = rec
            new = True
        if not new or page >= int(body.get('last_page', 1) or 1):
            break
        page += 1
    return out


def _unpack(block, code, n_series):
    """Riporta un blocco {start, values|series} a {data ISO: valore/lista}."""
    c = block.get(code)
    if not c:
        return {}
    start = date.fromisoformat(c['start'])
    if n_series == 1:
        return {(start + timedelta(days=i)).isoformat(): v
                for i, v in enumerate(c['values']) if v is not None}
    cols = [c.get(f) or [] for f in FLOW_FIELDS]
    ln = max((len(x) for x in cols), default=0)
    out = {}
    for i in range(ln):
        vals = [(col[i] if i < len(col) else None) for col in cols]
        if any(v is not None for v in vals):
            out[(start + timedelta(days=i)).isoformat()] = vals
    return out


def load_existing():
    """Legge history.json e ritorna (livelli, flussi).

    livelli: {codice: {data ISO: %}}
    flussi:  {codice: {data ISO: [withdrawal, injection, wCap, iCap]}}
    """
    if not os.path.exists(OUT):
        return {}, {}
    with open(OUT, encoding='utf-8') as f:
        data = json.load(f)
    levels, flows = {}, {}
    for code in data.get('countries', {}):
        levels[code] = _unpack(data['countries'], code, 1)
    for code in data.get('flows', {}):
        flows[code] = _unpack(data['flows'], code, len(FLOW_FIELDS))
    return levels, flows


def _series(days, first, last, pick):
    values, cur = [], first
    while cur <= last:
        values.append(pick(days.get(cur.isoformat())))
        cur += timedelta(days=1)
    return values


def save(levels, flows):
    countries = {}
    for code, days in levels.items():
        if not days:
            continue
        first, last = date.fromisoformat(min(days)), date.fromisoformat(max(days))
        countries[code] = {
            'name': COUNTRIES.get(code, code),
            'start': first.isoformat(),
            'values': _series(days, first, last, lambda r: r),
        }
    flow_out = {}
    for code, days in flows.items():
        if not days:
            continue
        first, last = date.fromisoformat(min(days)), date.fromisoformat(max(days))
        entry = {'start': first.isoformat()}
        for j, f in enumerate(FLOW_FIELDS):
            entry[f] = _series(days, first, last, lambda r, j=j: r[j] if r else None)
        flow_out[code] = entry
    payload = {
        'updated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'units': {'full': '%', 'flows': 'GWh/d'},
        'countries': countries,
        'flows': flow_out,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, separators=(',', ':'))


def find_gaps(days, not_before=None):
    """Intervalli di giorni mancanti tra il primo e l'ultimo giorno salvato."""
    if not days:
        return []
    cur = date.fromisoformat(min(days))
    last = date.fromisoformat(max(days))
    if not_before and cur < not_before:
        cur = not_before
    gaps, start = [], None
    while cur <= last:
        if cur.isoformat() not in days:
            start = start or cur
        elif start:
            gaps.append((start, cur - timedelta(days=1)))
            start = None
        cur += timedelta(days=1)
    if start:
        gaps.append((start, last))
    return gaps


def main():
    if not KEY:
        sys.exit('AGSI_KEY mancante')

    levels, flows = load_existing()
    today = date.today()
    complete = True

    for code in COUNTRIES:
        lv = levels.setdefault(code, {})
        fl = flows.setdefault(code, {})

        def absorb(d_from, d_to, expect):
            """Scarica una volta sola e smista nei due blocchi."""
            rows = fetch_range(code, d_from, d_to, expect_rows=expect)
            for day, rec in rows.items():
                if rec['full'] is not None:
                    lv[day] = rec['full']
                if date.fromisoformat(day) >= FLOW_START:
                    vals = [rec[f] for f in FLOW_FIELDS]
                    if any(v is not None for v in vals):
                        fl[day] = vals

        def run(d_from, d_to, expect, label):
            nonlocal complete
            cur = d_from
            while cur <= d_to:
                if time.time() > deadline:
                    complete = False
                    return False
                end = min(cur + timedelta(days=CHUNK_DAYS - 1), d_to)
                try:
                    absorb(cur, end, expect)
                except Exception as e:
                    print(f'  errore {code} {label} {cur}..{end}: {e}', flush=True)
                    complete = False
                    return False
                cur = end + timedelta(days=1)
            return True

        # 1) buchi nei livelli lasciati da esecuzioni precedenti
        for g_from, g_to in find_gaps(lv):
            print(f'{code}: buco livelli {g_from}..{g_to}, riscarico', flush=True)
            if not run(g_from, g_to, True, 'livelli'):
                break

        # 2) buchi nei flussi (compreso tutto il 2021+ al primo run con flussi)
        flow_from = max(FLOW_START, date.fromisoformat(min(lv)) if lv else FLOW_START)
        # Serie chiusa prima del 2021 (es. GB dopo la Brexit): nessun flusso da cercare.
        dead = bool(lv) and date.fromisoformat(max(lv)) < FLOW_START
        if dead:
            flow_tail = date.fromisoformat(max(lv))
        elif fl:
            for g_from, g_to in find_gaps(fl, not_before=flow_from):
                print(f'{code}: buco flussi {g_from}..{g_to}, riscarico', flush=True)
                if not run(g_from, g_to, True, 'flussi'):
                    break
            flow_tail = date.fromisoformat(max(fl)) - timedelta(days=OVERLAP_DAYS)
        else:
            flow_tail = flow_from

        # 3) coda: dal piu' indietro fra livelli e flussi fino a oggi
        lv_tail = (date.fromisoformat(max(lv)) - timedelta(days=OVERLAP_DAYS)) if lv else HISTORY_START
        cur = max(HISTORY_START, min(lv_tail, flow_tail))
        print(f'{code}: da {cur.isoformat()} '
              f'({len(lv)} giorni livelli, {len(fl)} giorni flussi)', flush=True)
        run(cur, today, bool(lv), 'coda')

        save(levels, flows)   # salva dopo ogni paese
        if time.time() > deadline:
            break

    for code in COUNTRIES:
        lv, fl = levels.get(code) or {}, flows.get(code) or {}
        if lv:
            print(f'{code}: livelli {min(lv)} -> {max(lv)} ({len(lv)})'
                  + (f', flussi {min(fl)} -> {max(fl)} ({len(fl)})' if fl else ', flussi assenti'))
    print('Completo.' if complete else 'Parziale: il prossimo run riprende da qui.')


if __name__ == '__main__':
    main()
