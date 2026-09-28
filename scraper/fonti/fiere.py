"""Espositori delle fiere alimentari dal catalogo pubblico di Fiere di Parma.

Il catalogo (catalogo.fiereparma.it) ospita TuttoFood e Cibus. Ogni scheda
espositore riporta indirizzo, telefono, email, sito, settori e merceologie.
Il modulo:
  1. legge i filtri "Settori" della fiera e sceglie quelli di panificati/dolciario
     (se non li trova usa le merceologie, altrimenti scorre tutto il catalogo);
  2. scorre le pagine dei risultati e raccoglie i link alle schede;
  3. apre ogni scheda e tiene solo le aziende italiane del settore.
"""
import re
import time
from urllib.parse import unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = "https://catalogo.fiereparma.it"
UA = "RicercaAziendeBot/0.1 (ricerca contatti B2B; rispetta robots.txt)"
PAUSA = 1.5          # secondi tra una richiesta e l'altra
MAX_PAGINE = 150     # pagine di risultati per filtro

NOMI_FIERE = {
    "tuttofood-2026": "TuttoFood 2026", "tuttofood-2025": "TuttoFood 2025",
    "cibus-2024": "Cibus 2024", "cibus-2021": "Cibus 2021",
}

# Parole che identificano i settori di interesse (su settori e merceologie della fiera)
PAROLE = {
    "Panificati": ["forno", "pane", "grissin", "cracker", "fette biscottate", "taralli", "piadin",
                   "focacc", "pizza", "sostituti del pane", "panific", "bakery", "friselle"],
    "Dolciario": ["dolc", "biscott", "merendin", "torron", "pasticc", "pralin", "cioccolat",
                  "caramell", "wafer", "torte", "ricorrenz", "confett", "panetton", "colomb",
                  "pandoro", "savoiardi", "croissant", "snack dolci", "spalmabil"],
}
# Termini che fanno scartare un falso positivo (es. "Forni" come macchinari)
ESCLUDI = ["macchin", "impiant", "attrezzatur", "linee complete", "forni a", "forni per",
           "forni elettrici", "forni rotativi", "forni statici", "forni convezione", "semilavorat",
           "dolcific"]

# Sigla provincia -> regione (nomi uguali a quelli di fonti/osm.py)
PROVINCE = {}
for regione, sigle in {
    "Piemonte": "TO VC NO CN AT AL BI VB", "Valle d'Aosta": "AO",
    "Lombardia": "VA CO SO MI BG BS PV CR MN LC LO MB", "Trentino-Alto Adige": "BZ TN",
    "Veneto": "VR VI BL TV VE PD RO", "Friuli-Venezia Giulia": "UD GO TS PN",
    "Liguria": "IM SV GE SP", "Emilia-Romagna": "PC PR RE MO BO FE RA FC RN",
    "Toscana": "MS LU PT FI LI PI AR SI GR PO", "Umbria": "PG TR", "Marche": "PU AN MC AP FM",
    "Lazio": "VT RI RM LT FR", "Abruzzo": "AQ TE PE CH", "Molise": "CB IS",
    "Campania": "CE BN NA AV SA", "Puglia": "FG BA TA BR LE BT", "Basilicata": "PZ MT",
    "Calabria": "CS CZ RC KR VV", "Sicilia": "TP PA ME AG CL EN CT RG SR",
    "Sardegna": "SS NU CA OR SU OT OG VS CI",
}.items():
    for s in sigle.split():
        PROVINCE[s] = regione


def classifica(etichette: list[str]) -> set[str]:
    trovati = set()
    for e in etichette:
        t = e.lower()
        if any(x in t for x in ESCLUDI):
            continue
        for settore, parole in PAROLE.items():
            if any(p in t for p in parole):
                trovati.add(settore)
    return trovati


class Catalogo:
    def __init__(self):
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})
        self.robots = RobotFileParser()
        try:
            r = self.sess.get(BASE + "/robots.txt", timeout=15)
            self.robots.parse(r.text.splitlines() if r.status_code == 200 else [])
        except requests.RequestException:
            self.robots.parse([])
        self._ultima = 0.0

    def get(self, url: str, params=None) -> BeautifulSoup | None:
        if not self.robots.can_fetch(UA, url):
            print(f"  robots.txt vieta {url}")
            return None
        attesa = PAUSA - (time.time() - self._ultima)
        if attesa > 0:
            time.sleep(attesa)
        self._ultima = time.time()
        try:
            r = self.sess.get(url, params=params, timeout=30)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code} su {r.url}")
                return None
            if "charset" not in r.headers.get("content-type", "").lower():
                r.encoding = "utf-8"
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as e:
            print(f"  errore su {url}: {e}")
            return None


def _filtri(soup: BeautifulSoup, campo: str) -> dict[str, str]:
    """Valori dei filtri del form di ricerca (es. campo='settore' -> {id: etichetta})."""
    out = {}
    for el in soup.find_all(["option", "input"]):
        nome = el.get("name") or (el.find_parent("select") or {}).get("name", "")
        if f"[{campo}]" not in nome:
            continue
        valore = (el.get("value") or "").strip()
        if not valore.isdigit():
            continue
        if el.name == "option":
            etichetta = el.get_text(" ", strip=True)
        else:
            lab = el.find_parent("label") or (el.get("id") and soup.find("label", attrs={"for": el["id"]}))
            etichetta = (lab or el.parent).get_text(" ", strip=True)
        out[valore] = etichetta
    return out


def _link_schede(soup: BeautifulSoup, base_url: str) -> set[str]:
    link = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if "/azienda/" in urlparse(href).path:
            link.add(href.split("#")[0])
    return link


def _link_pagine(soup: BeautifulSoup, base_url: str, slug: str) -> set[str]:
    pagine = set()
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True).isdigit():
            href = urljoin(base_url, a["href"])
            if f"/manifestazione/{slug}" in href:
                pagine.add(href.split("#")[0])
    return pagine


def _valore_dopo(righe: list[str], etichetta: str) -> str | None:
    for i, r in enumerate(righe):
        if r.strip().lower() == etichetta.lower():
            for succ in righe[i + 1:i + 4]:
                if succ.strip():
                    return succ.strip()
    return None


def analizza_scheda(soup: BeautifulSoup, url: str) -> dict:
    righe = [r for r in soup.get_text("\n").splitlines() if r.strip()]
    h1 = soup.find("h1")
    nome = h1.get_text(" ", strip=True) if h1 else None

    descrizione = None
    if h1:
        for p in h1.find_all_next("p", limit=6):
            testo = p.get_text(" ", strip=True)
            if len(testo) > 60:
                descrizione = testo[:300]
                break

    indirizzo = _valore_dopo(righe, "Indirizzo")
    telefono = _valore_dopo(righe, "Telefono")
    email = next((a["href"][7:].split("?")[0] for a in soup.select('a[href^="mailto:"]')), None) \
        or _valore_dopo(righe, "E-mail")
    sito = _valore_dopo(righe, "Web")

    comune = provincia = via = None
    nazione = None
    if indirizzo:
        parti = [p.strip() for p in indirizzo.split(" - ") if p.strip()]
        via = parti[0] if parti else None
        for p in parti:
            m = re.match(r"^(\d{5})\s+(.+)$", p)
            if m:
                comune = m.group(2).title()
            m = re.fullmatch(r"\(?([A-Z]{2})\)?", p)
            if m:
                provincia = m.group(1)
        nazione = parti[-1].upper() if parti else None

    settori, merci = [], []
    for a in soup.find_all("a", href=True):
        href = unquote(a["href"])
        if "search[settore]" in href:
            settori.append(a.get_text(" ", strip=True))
        elif "search[merce]" in href:
            merci.append(a.get_text(" ", strip=True))

    return {
        "url": url, "nome": nome, "descrizione": descrizione, "via": via, "comune": comune,
        "provincia": provincia, "regione": PROVINCE.get(provincia or ""), "nazione": nazione,
        "telefono": telefono, "email": (email or "").strip().lower() or None,
        "sito": sito if sito and "." in sito else None, "settori_fiera": settori, "merci_fiera": merci,
    }


def espositori(slug: str, gia_visti: dict | None = None, max_schede: int = 1500) -> list[dict]:
    """Restituisce le aziende italiane di panificati/dolciario esposte alla fiera `slug`."""
    gia_visti = gia_visti or {}
    cat = Catalogo()
    url_fiera = f"{BASE}/manifestazione/{slug}/"
    home = cat.get(url_fiera)
    if home is None:
        return []

    filtri = {}
    for campo in ("settore", "merce"):
        tutti = _filtri(home, campo)
        scelti = {k: v for k, v in tutti.items() if classifica([v])}
        print(f"  filtri '{campo}': {len(tutti)} trovati, {len(scelti)} pertinenti: "
              + ", ".join(list(scelti.values())[:8]))
        if scelti:
            filtri = {campo: scelti}
            break

    schede = set()
    ricerche = [[(f"search[{c}][]", i)] for c, ids in filtri.items() for i in ids] or [None]
    if ricerche == [None]:
        print("  nessun filtro riconosciuto: scorro tutto il catalogo")
    for params in ricerche:
        da_visitare, visitate = [(url_fiera, params)], set()
        while da_visitare and len(visitate) < MAX_PAGINE:
            url, p = da_visitare.pop(0)
            chiave = (url, str(p))
            if chiave in visitate:
                continue
            visitate.add(chiave)
            pagina = cat.get(url, params=p)
            if pagina is None:
                continue
            schede |= _link_schede(pagina, url)
            for nuova in _link_pagine(pagina, url, slug):
                # i link di paginazione contengono già i filtri nella query
                da_visitare.append((nuova, None if "search" in nuova else p))
    print(f"  {len(schede)} schede da controllare")

    risultati = []
    for url in sorted(schede)[:max_schede]:
        if url in gia_visti:
            dati = gia_visti[url]
        else:
            s = cat.get(url)
            if s is None:
                continue
            dati = analizza_scheda(s, url)
            gia_visti[url] = dati
        if dati.get("nazione") not in (None, "ITALIA", "ITALY") or not dati.get("regione"):
            continue
        settori = classifica(dati["settori_fiera"] + dati["merci_fiera"])
        if not settori or not dati.get("nome"):
            continue
        risultati.append({**dati, "settori": sorted(settori), "fiera": NOMI_FIERE.get(slug, slug)})
    return risultati
