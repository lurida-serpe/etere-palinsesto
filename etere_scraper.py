#!/usr/bin/env python3
"""
Etere – scraper del palinsesto: genera `curated.json`, il file che l'app scarica da GitHub.

Cosa fa
-------
1. Contiene l'elenco delle stazioni "curate" (STATIONS qui sotto): è la stessa lista che prima
   era scritta a mano dentro l'app (CuratedStations.kt). Aggiungere/togliere una radio = modificare
   questa lista (oppure direttamente curated.json), rilanciare lo script, fare commit.
2. Per le radio che hanno un fetcher (per ora Radio Blackout, Radio Onda Rossa, Radio Onda d'Urto)
   scarica il palinsesto dal sito e lo salva come SETTIMANA TIPO (lunedì..domenica, orari HH:MM).
   I parser sono quelli di radio-blackout-multi-v3.py, adattati: invece di datetime assoluti
   producono slot settimanali, perché l'app calcola da sola cosa è in onda "adesso".
3. Per ogni trasmissione cerca l'immagine nella cartella `trasmissioni-<id>/` (stessa logica di
   find_show_cover_file del Python: nome file == titolo, con confronto tollerante su maiuscole,
   punteggiatura e accenti), la ridimensiona/converte in WebP e la scrive in `covers/<id>/`.
   Nel JSON finisce solo il percorso relativo (+ ?v=<hash>, che fa aggiornare la cache dell'app
   quando cambi l'immagine).

Robustezza
----------
- Se un sito non risponde, per quella radio (o per quel giorno) restano i dati dell'esecuzione
  precedente: il file `curated.json` esistente viene usato come "memoria".
- Radio Blackout pubblica nell'HTML SOLO il giorno corrente: ogni esecuzione aggiorna il giorno
  di oggi e conserva gli altri sei. Esegui lo script ogni giorno (vedi README: GitHub Actions) e in
  una settimana la griglia è completa.
- Il file di output viene riscritto solo se qualcosa è davvero cambiato (così Git non registra una
  modifica al giorno per il solo campo "updated").

Uso tipico
----------
    python etere_scraper.py \\
        --out curated.json \\
        --cover-base https://raw.githubusercontent.com/UTENTE/REPO/main/covers/ \\
        --covers-src . --covers-out covers

Solo la struttura delle stazioni, senza rete:   python etere_scraper.py --offline
Solo una radio:                                  python etere_scraper.py --only blackout

Dipendenze: solo libreria standard. Pillow (pip install pillow) serve soltanto per le copertine.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

SCHEMA = 1
DEFAULT_TZ = "Europe/Rome"
DEFAULT_COVER_BASE = "https://raw.githubusercontent.com/UTENTE/REPO/main/covers/"

# =====================================================================
# Stazioni curate (le stesse che erano nell'app). Per le nuove radio basta aggiungere un blocco.
#   cover: "asset:covers/x.png" = immagine già dentro l'app; oppure un percorso relativo a
#          --cover-base (es. "stations/x.webp"); oppure None (l'app usa una copertina generica).
# =====================================================================
STATIONS: List[dict] = [
    dict(id="blackout", name="Radio Blackout",
         stream="http://stream.radioblackout.org/blackout.mp3",
         also=["https://blimp.streampunk.cc/_stream/blackout.ogg"],
         frequency="105.25 FM / DAB", city="Torino", lat=45.0703, lon=7.6869, region="Piemonte",
         home="https://radioblackout.org", scheduleUrl="https://radioblackout.org/palinsesto",
         cover="asset:covers/blackout.png"),
    dict(id="ondarossa", name="Radio Onda Rossa",
         stream="https://s.streampunk.cc/ondarossa.mp3",
         frequency="87.9 FM", city="Roma", lat=41.9028, lon=12.4964, region="Lazio",
         home="https://www.ondarossa.info", scheduleUrl="https://www.ondarossa.info/palinsesto",
         cover="asset:covers/ondarossa.jpg"),
    dict(id="wombat", name="Radio Wombat",
         stream="https://s.streampunk.cc/wombat.mp3",
         frequency="1359 AM", city="Firenze", lat=43.7696, lon=11.2558, region="Toscana",
         home="https://radiowombat.net", scheduleUrl="https://radiowombat.net/palinsesto/",
         cover="asset:covers/wombat.jpg"),
    dict(id="eustachio", name="Radio Eustachio",
         stream="https://s.streampunk.cc/radioeustachio.mp3",
         city="Verona", lat=45.4384, lon=10.9916, region="Veneto",
         cover="asset:covers/eustachio.jpg"),
    dict(id="spore", name="Radio Spore",
         stream="https://stream.radiospore.oziosi.org:8003/spore.ogg",
         city="Bologna", lat=44.4949, lon=11.3426, region="Emilia-Romagna",
         home="https://radiospore.oziosi.org", scheduleUrl="https://radiospore.oziosi.org/palinsesto",
         cover="asset:covers/spore.png"),
    # "radio diffusa": nessun luogo -> compare nella ricerca ma non sulla mappa
    dict(id="quar", name="Radio Quar",
         stream="https://radioquar.com/radio/8000/radio320.mp3",
         city="", lat=None, lon=None, region="",
         home="https://radioquar.com",
         cover="asset:covers/quar.png"),
    dict(id="ondadurto", name="Radio Onda d'Urto",
         stream="https://hochimin.urtostream.org:8443/radiondadurto.mp3",
         frequency="99.6 FM", city="Brescia", lat=45.5416, lon=10.2118, region="Lombardia",
         home="https://www.radiondadurto.org",
         scheduleWeek=[f"https://www.radiondadurto.org/palinsesto/{g}/" for g in
                       ("lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica")],
         cover="asset:covers/ondadurto.png"),
    dict(id="ciroma", name="Radio Ciroma",
         stream="https://s.streampunk.cc/ciroma.mp3",
         frequency="105.7 FM", city="Cosenza", lat=39.2983, lon=16.2538, region="Calabria",
         cover="asset:covers/ciroma.png"),
    dict(id="cittafujiko", name="Radio Città Fujiko",
         stream="https://streaming.radiocittafujiko.it:8000/rcf.mp3",
         frequency="103.1 FM", city="Bologna", lat=44.4949, lon=11.3426, region="Emilia-Romagna",
         home="https://www.radiocittafujiko.it",
         scheduleWeek=[f"https://www.radiocittafujiko.it/{g}-di-radio-citta-fujiko-2/" for g in
                       ("il-lunedi", "il-martedi", "il-mercoledi", "il-giovedi", "il-venerdi",
                        "il-sabato", "la-domenica")],
         cover="asset:covers/cittafujiko.png"),
    dict(id="controradio", name="Controradio",
         stream="https://s4.yesstreaming.net:17199/stream",
         frequency="93.6 / 98.9 FM", city="Firenze", lat=43.7696, lon=11.2558, region="Toscana",
         home="https://www.controradio.it",
         scheduleUrl="https://www.controradio.it/il-palinsesto-settimanale-di-controradio/?table=1",
         cover="asset:covers/controradio.jpg"),
    dict(id="rca", name="Radio Città Aperta",
         stream="https://www.radiocittaperta.it/redirected-weighted.php",
         city="Roma", lat=41.9028, lon=12.4964, region="Lazio",
         home="https://www.radiocittaperta.it", scheduleUrl="https://www.radiocittaperta.it/palinsesto/",
         cover="asset:covers/rca.jpg"),
    dict(id="rogna", name="Radio Rogna",
         stream="http://51.75.144.165:8185/stream",
         city="Sarzana", lat=44.1117, lon=9.9611, region="Liguria",
         home="http://www.radiorogna.it/", scheduleUrl="http://www.radiorogna.it/",
         cover="asset:covers/rogna.png"),
]

# =====================================================================
# Rete
# =====================================================================
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch_html(url: str, timeout: int = 15, retries: int = 2) -> str:
    last: Optional[Exception] = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - si riprova e poi si rilancia
            last = exc
    assert last is not None
    raise last


Fetch = Callable[[str], str]

# =====================================================================
# Tipi: uno slot è (giorno 0=lun..6=dom, minuti di inizio, minuti di fine, titolo, url)
# Se fine <= inizio la trasmissione finisce il giorno dopo (come nel Python originale).
# =====================================================================
Slot = Tuple[int, int, int, str, Optional[str]]

_TAG_RE = re.compile(r"<[^>]+>")
_ORARIO_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_A_HREF_RE = re.compile(r'<a\s+[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _clean(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", raw))).strip()


def _hhmm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _rome_today() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(DEFAULT_TZ)).date()
    except Exception:  # noqa: BLE001 - tzdata assente (es. Windows senza pacchetto tzdata)
        return date.today()


# =====================================================================
# Radio Blackout – la pagina mostra solo il giorno corrente (app Vue)
# =====================================================================
_BO_LI_RE = re.compile(r'<li\s+class="([^"]*rbo-show-item[^"]*)"[^>]*>(.*?)</li>', re.DOTALL)
_BO_LINK_RE = re.compile(
    r'<a\s+href="([^"]+)"\s+class="rbo-trasmissione">\s*<span>(.*?)</span>', re.DOTALL)
BLACKOUT_URL = "https://radioblackout.org/palinsesto"


def parse_blackout(page: str, weekday: int) -> Dict[int, List[Slot]]:
    """Ritorna {giorno: [slot]} per il giorno `weekday` (0=lun)."""
    out: Dict[int, List[Slot]] = {}
    offset = 0
    last_start = -1
    for _cls, body in _BO_LI_RE.findall(page):
        o = _ORARIO_RE.search(body)
        l = _BO_LINK_RE.search(body)
        if not o or not l:
            continue
        start_s, end_s = o.groups()
        href, title = l.groups()
        title = html.unescape(_TAG_RE.sub("", title)).strip()
        if not title:
            continue
        if href.startswith("/"):
            href = "https://radioblackout.org" + href
        s, e = _mins(start_s), _mins(end_s)
        if s < last_start:           # difesa: un orario che "torna indietro" è un nuovo giorno
            offset += 1
        last_start = s
        day = (weekday + offset) % 7
        out.setdefault(day, []).append((day, s, e, title, href))
    return out


def scrape_blackout(fetch: Fetch, today: Optional[date] = None) -> Dict[int, List[Slot]]:
    today = today or _rome_today()
    days = parse_blackout(fetch(BLACKOUT_URL), today.weekday())
    if not any(days.values()):
        raise RuntimeError("nessuna trasmissione trovata nella pagina di Radio Blackout")
    return days


# =====================================================================
# Radio Onda Rossa – tutta la settimana in una pagina: <h3>Giorno</h3><table>...
# =====================================================================
ONDAROSSA_URL = "https://www.ondarossa.info/palinsesto"
_OR_DAYS = {"lunedì": 0, "martedì": 1, "mercoledì": 2, "giovedì": 3,
            "venerdì": 4, "sabato": 5, "domenica": 6}
_OR_H3_TABLE_RE = re.compile(r"<h3>\s*([^<]+?)\s*</h3>\s*<table>(.*?)</table>", re.DOTALL)
_TR_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)


def parse_ondarossa(page: str) -> Dict[int, List[Slot]]:
    own: Dict[int, List[Slot]] = {}
    spill: List[Slot] = []
    for day_name, table in _OR_H3_TABLE_RE.findall(page):
        day = _OR_DAYS.get(day_name.strip().lower())
        if day is None:
            continue
        own.setdefault(day, [])
        extra = 0
        last_start = -1
        for tr in _TR_RE.findall(table):
            cells = _TD_RE.findall(tr)
            if len(cells) < 2:
                continue
            o = _ORARIO_RE.search(cells[0])
            if not o:
                continue
            start_s, end_s = o.groups()
            link = _A_HREF_RE.search(cells[1])
            if link:
                href, title = link.groups()
                if href.startswith("/"):
                    href = "https://www.ondarossa.info" + href
            else:
                href, title = None, cells[1]
            title = html.unescape(_TAG_RE.sub("", title)).strip()
            title = re.sub(r"\s*-\s*$", "", title).strip()
            if not title or title == "&nbsp;":
                continue
            s, e = _mins(start_s), _mins(end_s)
            if s < last_start:
                extra += 1
            last_start = s
            slot = ((day + extra) % 7, s, e, title, href)
            (own[day] if extra == 0 else spill).append(slot)
    return _merge_spill(own, spill)


def _merge_spill(own: Dict[int, List[Slot]], spill: List[Slot]) -> Dict[int, List[Slot]]:
    """Le righe dopo la mezzanotte stanno nella tabella del giorno prima ma appartengono al giorno
    dopo: si aggiungono solo se quel giorno non ha già uno slot che parte alla stessa ora."""
    for slot in spill:
        day, start = slot[0], slot[1]
        bucket = own.setdefault(day, [])
        if not any(x[1] == start for x in bucket):
            bucket.append(slot)
    return own


def scrape_ondarossa(fetch: Fetch) -> Dict[int, List[Slot]]:
    days = parse_ondarossa(fetch(ONDAROSSA_URL))
    if not any(days.values()):
        raise RuntimeError("nessuna trasmissione trovata nella pagina di Radio Onda Rossa")
    return days


# =====================================================================
# Radio Onda d'Urto – una pagina per giorno, solo l'orario di INIZIO di ogni trasmissione
# =====================================================================
ONDADURTO_BASE = "https://www.radiondadurto.org/palinsesto"
_OU_DAYS = [("lunedi", 0), ("martedi", 1), ("mercoledi", 2), ("giovedi", 3),
            ("venerdi", 4), ("sabato", 5), ("domenica", 6)]
_OU_HEADER_RE = re.compile(r"PALINSESTO\s+(.*?)</p>", re.IGNORECASE | re.DOTALL)
_OU_SPLIT_RE = re.compile(r"</?(?:div|p)[^>]*>|<br\s*/?>", re.IGNORECASE)
_OU_LINE_RE = re.compile(r"^(\d{1,2})[.:](\d{2})\s*[–-]?\s*(.*)$")
_OU_ENTRY_CONTENT_RE = re.compile(r'<div class="entry-content">(.*?)</div>', re.IGNORECASE | re.DOTALL)
# Se per una trasmissione manca la successiva (pagina di un giorno non scaricata e nessun dato
# precedente) la durata viene limitata a questo, invece di estenderla fino a chissà quando.
OU_MAX_MINUTES = 12 * 60


def _ou_current_block(page: str) -> str:
    matches = list(_OU_HEADER_RE.finditer(page))
    if matches:
        blocks = []
        for i, m in enumerate(matches):
            title = re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", m.group(1)))).strip().rstrip(":").strip()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(page)
            blocks.append((title, page[m.end():end]))
        for title, body in blocks:
            if "normale" in title.lower():
                return body
        return blocks[-1][1]
    m = _OU_ENTRY_CONTENT_RE.search(page)
    return m.group(1) if m else ""


def _ou_day_entries(page: str) -> List[Tuple[int, str, Optional[str]]]:
    entries = []
    for raw in _OU_SPLIT_RE.split(_ou_current_block(page)):
        href = None
        link = _A_HREF_RE.search(raw)
        if link:
            href = link.group(1)
            if href.startswith("/"):
                href = "https://www.radiondadurto.org" + href
        text = re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", raw))).strip()
        m = _OU_LINE_RE.match(text)
        if not m:
            continue
        hh, mm, title = m.groups()
        title = title.strip().rstrip(":").strip()
        if title:
            entries.append((int(hh) * 60 + int(mm), title, href))
    return entries


def scrape_ondadurto(fetch: Fetch, previous: Optional[Dict[int, List[Slot]]] = None
                     ) -> Dict[int, List[Slot]]:
    """previous: i dati della volta scorsa, usati per i giorni la cui pagina non è raggiungibile."""
    starts: Dict[int, List[Tuple[int, str, Optional[str]]]] = {}
    own_days: List[int] = []
    for slug, weekday in _OU_DAYS:
        url = f"{ONDADURTO_BASE}/{slug}/"
        try:
            entries = _ou_day_entries(fetch(url))
        except Exception as exc:  # noqa: BLE001
            print(f"  [ondadurto] {url}: {exc!r}", file=sys.stderr)
            continue
        if not entries:
            print(f"  [ondadurto] {url}: nessuna riga riconosciuta", file=sys.stderr)
            continue
        own_days.append(weekday)
        extra = 0
        last = -1
        for minutes, title, href in entries:
            if minutes < last:
                extra += 1
            last = minutes
            starts.setdefault((weekday + extra) % 7, []).append((minutes, title, href))
    if not own_days:
        raise RuntimeError("nessuna pagina del palinsesto di Onda d'Urto utilizzabile")

    # giorni mancanti: si riprendono dalla volta scorsa (hanno già l'orario di fine calcolato)
    carried: Dict[int, List[Slot]] = {}
    if previous:
        for d in range(7):
            if d not in own_days and previous.get(d):
                carried[d] = list(previous[d])

    # i giorni scaricati: gli slot partono dagli orari di inizio; la fine = inizio della successiva
    flat: List[Tuple[int, str, Optional[str], int]] = []   # (minuto assoluto nella settimana, ...)
    for d, entries in starts.items():
        seen = set()
        for minutes, title, href in sorted(entries, key=lambda x: x[0]):
            if minutes in seen:
                continue
            seen.add(minutes)
            flat.append((d * 1440 + minutes, title, href, d))
    # le voci dei giorni "carried" servono da punti di arrivo per calcolare le fine
    anchors = sorted([f[0] for f in flat] +
                     [d * 1440 + s[1] for d, slots in carried.items() for s in slots])
    flat.sort()
    out: Dict[int, List[Slot]] = {d: list(v) for d, v in carried.items()}
    for abs_start, title, href, _d in flat:
        nxt = next((a for a in anchors if a > abs_start), anchors[0] + 7 * 1440)
        dur = min(nxt - abs_start, OU_MAX_MINUTES)
        day, start = divmod(abs_start, 1440)
        out.setdefault(day, []).append((day, start, start + dur, title, href))
    # una voce può comparire due volte (riportata dalla volta scorsa + "scivolata" dopo la mezzanotte
    # dal giorno prima): si tiene una sola voce per orario di inizio
    return {d: _dedupe_sort(v) for d, v in out.items()}


# =====================================================================
# Registro dei fetcher: id stazione -> funzione (fetch, previous_by_day) -> {giorno: [slot]}
# Per aggiungere un'altra radio: scrivi scrape_xxx(...) e aggiungila qui.
# =====================================================================
SCRAPERS: Dict[str, Callable[[Fetch, Dict[int, List[Slot]]], Dict[int, List[Slot]]]] = {
    "blackout": lambda f, prev: scrape_blackout(f),
    "ondarossa": lambda f, prev: scrape_ondarossa(f),
    "ondadurto": lambda f, prev: scrape_ondadurto(f, prev),
}

# stazioni la cui fonte pubblica un solo giorno: gli altri giorni si conservano dalla volta prima
PARTIAL_DAY_SOURCES = {"blackout"}

# =====================================================================
# Copertine delle trasmissioni
# =====================================================================
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
COVER_MAX_PX = 640
COVER_QUALITY = 82


def normalize_title(name: str) -> str:
    """Come _normalize_show_name del Python: minuscolo, solo lettere/numeri/spazi, spazi singoli."""
    name = re.sub(r"[^\w\s]", " ", name, flags=re.UNICODE)
    return re.sub(r"\s+", " ", name).strip().lower()


def fold(name: str) -> str:
    """Come normalize_title ma senza accenti (secondo tentativo)."""
    nfd = unicodedata.normalize("NFD", normalize_title(name))
    return "".join(c for c in nfd if not unicodedata.combining(c))


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")
    return s or "show"


class CoverIndex:
    """Le immagini di una cartella `trasmissioni-<id>/`, cercabili per titolo."""

    def __init__(self, directory: Optional[str]):
        self.exact: Dict[str, str] = {}
        self.norm: Dict[str, str] = {}
        self.folded: Dict[str, str] = {}
        self.files: List[str] = []
        if directory and os.path.isdir(directory):
            for entry in sorted(os.listdir(directory)):
                stem, ext = os.path.splitext(entry)
                if ext.lower() not in IMAGE_EXTS:
                    continue
                path = os.path.join(directory, entry)
                self.files.append(path)
                self.exact.setdefault(stem.strip().lower(), path)
                self.norm.setdefault(normalize_title(stem), path)
                self.folded.setdefault(fold(stem), path)

    def find(self, title: str) -> Optional[str]:
        return (self.exact.get(title.strip().lower())
                or self.norm.get(normalize_title(title))
                or self.folded.get(fold(title)))


def build_cover(src: str, dst: str) -> Optional[str]:
    """Converte `src` in WebP ridimensionato; ritorna l'hash breve del risultato (o None se fallisce)."""
    try:
        from PIL import Image
    except ImportError:
        print("  Pillow non installato: pip install pillow (copertine saltate)", file=sys.stderr)
        return None
    try:
        import io
        with Image.open(src) as im:
            im.load()
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if has_alpha else "RGB")
            im.thumbnail((COVER_MAX_PX, COVER_MAX_PX), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=COVER_QUALITY, method=4)
        data = buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        print(f"  immagine non convertibile {src}: {exc!r}", file=sys.stderr)
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    same = False
    if os.path.exists(dst):
        with open(dst, "rb") as f:
            same = f.read() == data
    if not same:
        with open(dst, "wb") as f:
            f.write(data)
    return hashlib.sha1(data).hexdigest()[:8]


# =====================================================================
# JSON
# =====================================================================

def _slot_to_json(slot: Slot, cover: Optional[str]) -> dict:
    day, s, e, title, url = slot
    o = {"d": day, "s": _hhmm(s), "e": _hhmm(e), "t": title}
    if url:
        o["u"] = url
    if cover:
        o["c"] = cover
    return o


def _slot_from_json(o: dict) -> Slot:
    return (int(o["d"]), _mins(o["s"]), _mins(o["e"]), o["t"], o.get("u"))


def _station_to_json(st: dict, shows: List[dict]) -> dict:
    o = {"id": st["id"], "name": st["name"], "stream": st["stream"]}
    if st.get("also"):
        o["also"] = st["also"]
    for key in ("frequency", "city", "lat", "lon", "region", "home", "scheduleUrl", "scheduleWeek", "cover"):
        v = st.get(key)
        if v not in (None, "", []):
            o[key] = v
    o["tz"] = st.get("tz", DEFAULT_TZ)
    if shows:
        o["shows"] = shows
    return o


def load_previous(path: Optional[str]) -> Dict[str, Dict[int, List[Slot]]]:
    prev: Dict[str, Dict[int, List[Slot]]] = {}
    if not path or not os.path.exists(path):
        return prev
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for st in data.get("stations", []):
            days: Dict[int, List[Slot]] = {}
            for sh in st.get("shows", []):
                slot = _slot_from_json(sh)
                days.setdefault(slot[0], []).append(slot)
            if days:
                prev[st["id"]] = days
    except Exception as exc:  # noqa: BLE001
        print(f"curated.json precedente illeggibile ({exc!r}): si riparte da zero", file=sys.stderr)
    return prev


def _dedupe_sort(slots: List[Slot]) -> List[Slot]:
    seen = set()
    out = []
    for s in sorted(slots, key=lambda x: (x[0], x[1], x[2])):
        key = (s[0], s[1])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def build(args, fetch: Fetch = fetch_html) -> Tuple[dict, List[str]]:
    prev_all = load_previous(args.previous)
    only = set(args.only.split(",")) if args.only else None
    stations_json = []
    failures: List[str] = []

    for st in STATIONS:
        sid = st["id"]
        prev_days = prev_all.get(sid, {})
        days: Dict[int, List[Slot]] = {d: list(v) for d, v in prev_days.items()}
        scraper = SCRAPERS.get(sid)
        status = "senza palinsesto"
        if scraper and not args.offline and (only is None or sid in only):
            try:
                fresh = scraper(fetch, prev_days)
                if sid in PARTIAL_DAY_SOURCES:
                    days.update(fresh)                     # sostituisce solo i giorni scaricati
                else:
                    for d in range(7):                     # settimana intera: giorni mancanti dal precedente
                        if d in fresh:
                            days[d] = fresh[d]
                n = sum(len(v) for v in fresh.values())
                status = f"ok ({n} trasmissioni scaricate, {len(fresh)} giorni)"
            except Exception as exc:  # noqa: BLE001
                status = f"ERRORE {exc!r}: restano i dati precedenti"
                failures.append(sid)
                print(f"[{sid}] {status}", file=sys.stderr)
        elif scraper:
            status = "dati precedenti (nessun download)"

        # --- copertine delle trasmissioni
        shows: List[dict] = []
        if days:
            src_dir = os.path.join(args.covers_src, f"trasmissioni-{sid}") if args.covers_src else None
            index = CoverIndex(src_dir)
            used = set()
            missing = []
            cache: Dict[str, Optional[str]] = {}
            for slot in _dedupe_sort([s for v in days.values() for s in v]):
                title = slot[3]
                cover_rel = None
                if title in cache:
                    cover_rel = cache[title]
                else:
                    src = index.find(title)
                    if src and not args.offline:
                        rel_name = f"{sid}/{slugify(title)}.webp"
                        h = build_cover(src, os.path.join(args.covers_out, rel_name))
                        if h:
                            cover_rel = f"{rel_name}?v={h}"
                            used.add(src)
                    elif src is None and index.files:
                        missing.append(title)
                    cache[title] = cover_rel
                # in offline si mantiene la copertina già scritta nel JSON precedente
                shows.append(_slot_to_json(slot, cover_rel))
            if index.files:
                unused = [os.path.basename(p) for p in index.files if p not in used]
                if not args.offline:
                    print(f"[{sid}] copertine: {len(used)} usate, {len(unused)} immagini senza trasmissione"
                          f"{(' (es. ' + ', '.join(unused[:4]) + ')') if unused else ''}")
                    uniq_missing = sorted(set(missing))
                    if uniq_missing:
                        print(f"[{sid}] {len(uniq_missing)} trasmissioni senza immagine (es. "
                              f"{', '.join(uniq_missing[:5])})")
        print(f"[{sid}] {status}")
        stations_json.append(_station_to_json(st, shows))

    return {
        "schema": SCHEMA,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverBase": args.cover_base,
        "stations": stations_json,
    }, failures


def _strip_volatile(data: dict) -> dict:
    d = dict(data)
    d.pop("updated", None)
    return d


def preserve_covers_offline(new: dict, previous_path: Optional[str]) -> None:
    """In --offline le copertine non si ricalcolano: si riprendono dal JSON precedente."""
    if not previous_path or not os.path.exists(previous_path):
        return
    try:
        with open(previous_path, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:  # noqa: BLE001
        return
    old_cov = {}
    for st in old.get("stations", []):
        for sh in st.get("shows", []):
            if sh.get("c"):
                old_cov[(st["id"], sh["t"])] = sh["c"]
    for st in new["stations"]:
        for sh in st.get("shows", []):
            c = old_cov.get((st["id"], sh["t"]))
            if c and "c" not in sh:
                sh["c"] = c


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Genera curated.json per l'app Etere.")
    ap.add_argument("--out", default="curated.json", help="file JSON da scrivere")
    ap.add_argument("--previous", default=None,
                    help="JSON della volta scorsa (default: lo stesso file di --out, se esiste)")
    ap.add_argument("--cover-base", default=DEFAULT_COVER_BASE,
                    help="URL della cartella che conterrà le copertine (finisce con /)")
    ap.add_argument("--covers-src", default=".",
                    help="cartella che contiene trasmissioni-blackout/, trasmissioni-ondarossa/, ...")
    ap.add_argument("--covers-out", default="covers", help="dove scrivere le copertine convertite")
    ap.add_argument("--only", default=None, help="solo queste radio, es. blackout,ondarossa")
    ap.add_argument("--offline", action="store_true", help="niente download: solo struttura + dati precedenti")
    ap.add_argument("--strict", action="store_true", help="esci con errore se una radio fallisce")
    ap.add_argument("--pretty", action="store_true", help="JSON indentato (più leggibile, più pesante)")
    args = ap.parse_args(argv)
    if not args.cover_base.endswith("/"):
        args.cover_base += "/"
    if args.previous is None and os.path.exists(args.out):
        args.previous = args.out

    data, failures = build(args)
    if args.offline:
        preserve_covers_offline(data, args.previous)

    # riscrive solo se è cambiato qualcosa oltre al timestamp
    unchanged = False
    if os.path.exists(args.out):
        try:
            with open(args.out, encoding="utf-8") as f:
                unchanged = _strip_volatile(json.load(f)) == _strip_volatile(data)
        except Exception:  # noqa: BLE001
            pass
    if unchanged:
        print(f"{args.out}: nessuna modifica.")
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            if args.pretty:
                json.dump(data, f, ensure_ascii=False, indent=1)
            else:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        n = sum(len(s.get("shows", [])) for s in data["stations"])
        print(f"{args.out}: scritto ({len(data['stations'])} stazioni, {n} trasmissioni, "
              f"{os.path.getsize(args.out) // 1024} KB).")

    if failures and args.strict:
        print(f"Radio con errori: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
