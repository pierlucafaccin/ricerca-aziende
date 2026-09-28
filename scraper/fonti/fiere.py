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
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from fonti.sito import email_generica

BASE = "https://catalogo.fiereparma.it"
UA = "RicercaAziendeBot/0.1 (ricerca contatti B2B; rispetta robots.txt)"
PAUSA = 1.5          # secondi tra una richiesta e l'altra
MAX_PAGINE = 150     # pagine di risultati per filtro
DEBUG_DIR = None     # impostata da raccolta.py (data/debug)

NOMI_FIERE = {
    "tuttofood-2026": "TuttoFood 2026", "tuttofood-2025": "TuttoFood 2025",
    "cibus-2024": "Cibus 2024", "cibus-2021": "Cibus 2021",
}

# Settori della fiera (pochi e generici): basta una parola chiave
SETTORI_FIERA = {"Panificati": ["forno", "bakery", "panific"], "Dolciario": ["dolci", "cioccolat", "confection", "sweet"]}
# Merceologie: elenco preciso, più poche radici sicure
MERCI = {
    "Panificati": {"crackers", "fette biscottate", "grissini", "pane", "pane industriale",
                   "pane e sostituti pane freschi", "pane/paste altre basi surgelate", "pizza classica",
                   "pizza fresca", "pizze e focacce pizze pronte", "sostituti del pane", "specialità piadina",
                   "specialità taralli", "salatini assortiti", "prodotti da forno e ricorrenze"},
    "Dolciario": {"biscotti frollini", "biscotti secchi", "biscotti savoiardi", "biscotti infanzia",
                  "biscotti farciti", "merendine", "pasticceria altra unitipo", "pasticceria assortita",
                  "pasticceria sfoglie", "specialita dolci/pasticceria", "torte colate", "torte lievitate",
                  "ricorrenze forno", "altri prodotti da ricorrenza", "wafers", "torrone",
                  "praline - cioccolatini", "tavolette e barrette", "uova cioccolato pasquali",
                  "ovetti pieni o ripieni", "caramelle al latte, toffee e mou",
                  "caramelle dure ripiene - fondenti - gelée - gommose",
                  "caramelle lecca lecca e con bastoncino", "caramelle senza zucchero", "caramelle dure e gommose",
                  "spalmabili base cioccolato", "spalmabili base nocciola", "snack dolci", "confetti",
                  "cioccolato e prodotti a base cacao", "croissant arrotolati", "pan di spagna", "marzapane",
                  "croccanti", "pralinati e dragées", "prodotti da ricorrenza"},
}
RADICI = {"Panificati": ["grissin", "fette biscottate", "taralli"],
          "Dolciario": ["biscott", "panetton", "pandoro", "colomb", "torron", "wafer", "pralin", "merendin"]}
# Termini che fanno scartare un falso positivo (es. macchinari per il forno)
ESCLUDI = ["macchin", "impiant", "attrezzatur", "linee complete", "forni ", "semilavorat", "dolcific",
           "preparati", "ingredient"]


def _norm(t: str) -> str:
    return " ".join(t.lower().replace("\xa0", " ").split())


def classifica(etichette: list[str], settori_fiera: list[str] | None = None) -> set[str]:
    trovati = set()
    for e in settori_fiera or []:
        t = _norm(e)
        for settore, parole in SETTORI_FIERA.items():
            if any(p in t for p in parole):
                trovati.add(settore)
    for e in etichette:
        t = _norm(e)
        if any(x in t for x in ESCLUDI):
            continue
        for settore in MERCI:
            if t in MERCI[settore] or any(r in t for r in RADICI[settore]):
                trovati.add(settore)
    return trovati


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
    if nome and "Contatti" in righe:
        inizio = max((i for i, r in enumerate(righe) if r.strip() == nome), default=-1)
        fine = righe.index("Contatti")
        blocco = [r.strip() for r in righe[inizio + 1:fine] if len(r.strip()) > 60 and "Padiglione" not in r]
        descrizione = max(blocco, key=len)[:300] if blocco else None

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

    settori, merci, filtri = [], [], []
    for a in soup.find_all("a", href=True):
        m = re.search(r"search\[(settore|merce)\]\[\d*\]=(\d+)", unquote(a["href"]))
        if not m:
            continue
        etichetta = a.get_text(" ", strip=True)
        (settori if m.group(1) == "settore" else merci).append(etichetta)
        filtri.append([m.group(1), m.group(2), etichetta])

    return {
        "url": url, "nome": nome, "descrizione": descrizione, "via": via, "comune": comune,
        "provincia": provincia, "regione": PROVINCE.get(provincia or ""), "nazione": nazione,
        "telefono": telefono,
        "email": e if (e := (email or "").strip().lower()) and email_generica(e) else None,
        "sito": sito if sito and "." in sito else None, "settori_fiera": settori, "merci_fiera": merci,
        "filtri": filtri,
    }


def _scheda(cat: "Catalogo", url: str, gia_visti: dict) -> dict | None:
    if url in gia_visti:
        return gia_visti[url]
    s = cat.get(url)
    if s is None:
        return None
    gia_visti[url] = analizza_scheda(s, url)
    return gia_visti[url]


def _salva_debug(nome: str, soup: BeautifulSoup | None):
    """Copia dell'HTML nel repository, utile per capire le modifiche del sito."""
    if soup is None or not DEBUG_DIR:
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / f"{nome}.html").write_text(str(soup)[:400_000], encoding="utf-8")


PAGINAZIONI = [  # modi comuni di chiedere la pagina n; si prova quale funziona
    lambda url, p, n: (url.rstrip("/") + f"/page/{n}/", p),
    lambda url, p, n: (url, (p or []) + [("paged", n)]),
    lambda url, p, n: (url, (p or []) + [("pg", n)]),
    lambda url, p, n: (url, (p or []) + [("pagina", n)]),
    lambda url, p, n: (url, (p or []) + [("page", n)]),
]


def _tutte_le_pagine(cat, url_fiera, slug, params, prima) -> set[str]:
    schede = _link_schede(prima, url_fiera)
    # 1) link di paginazione veri, se esistono
    da_visitare = list(_link_pagine(prima, url_fiera, slug))
    visitate = set()
    while da_visitare and len(visitate) < MAX_PAGINE:
        u = da_visitare.pop(0)
        if u in visitate:
            continue
        visitate.add(u)
        pg = cat.get(u)
        if pg is not None:
            schede |= _link_schede(pg, u)
            da_visitare += [x for x in _link_pagine(pg, u, slug) if x not in visitate]
    if visitate:
        return schede
    # 2) nessun link: prova i formati di paginazione più comuni
    for i, formato in enumerate(PAGINAZIONI):
        u, p = formato(url_fiera, params, 2)
        pg = cat.get(u, params=p)
        nuove = _link_schede(pg, u) - schede if pg is not None else set()
        if not nuove:
            continue
        print(f"  paginazione riconosciuta (formato {i + 1})")
        schede |= nuove
        for n in range(3, MAX_PAGINE):
            u, p = formato(url_fiera, params, n)
            pg = cat.get(u, params=p)
            nuove = _link_schede(pg, u) - schede if pg is not None else set()
            if not nuove:
                break
            schede |= nuove
        return schede
    print("  paginazione non riconosciuta: letta solo la prima pagina")
    return schede


def espositori(slug: str, gia_visti: dict | None = None, max_schede: int = 3000) -> list[dict]:
    """Restituisce le aziende italiane di panificati/dolciario esposte alla fiera `slug`."""
    if gia_visti is None:
        gia_visti = {}
    cat = Catalogo()
    url_fiera = f"{BASE}/manifestazione/{slug}/"
    home = cat.get(url_fiera)
    _salva_debug(f"{slug}_home", home)
    if home is None:
        return []

    # Filtri: dal form di ricerca oppure dai link presenti nelle schede della prima pagina
    candidati = {}
    for campo in ("settore", "merce"):
        for k, v in _filtri(home, campo).items():
            candidati[(campo, k)] = v
    if not candidati:
        for url in sorted(_link_schede(home, url_fiera))[:40]:
            d = _scheda(cat, url, gia_visti)
            for campo, k, v in (d or {}).get("filtri", []):
                candidati[(campo, k)] = v
    def pertinenti(cand):
        st = {k: v for (c, k), v in cand.items() if c == "settore" and classifica([], [v])}
        return st, {k: v for (c, k), v in cand.items() if c == "merce" and classifica([v])}

    settori, merci = pertinenti(candidati)
    print(f"  filtri trovati: {len(candidati)}; settori pertinenti: {', '.join(settori.values()) or '-'}; "
          f"merceologie pertinenti: {len(merci)}")
    coda = [("settore", k) for k in settori] or [("merce", k) for k in merci]
    fatte = set()
    schede = set()
    if not coda:
        print("  nessun filtro pertinente: scorro tutto il catalogo")
        schede = _tutte_le_pagine(cat, url_fiera, slug, None, home)
    while coda:
        campo, k = coda.pop(0)
        if (campo, k) in fatte:
            continue
        fatte.add((campo, k))
        params = [(f"search[{campo}][]", k)]
        prima = cat.get(url_fiera, params=params)
        if len(fatte) == 1:
            _salva_debug(f"{slug}_filtrata", prima)
        if prima is None:
            continue
        trovate = _tutte_le_pagine(cat, url_fiera, slug, params, prima)
        print(f"  filtro {candidati.get((campo, k), k)}: {len(trovate)} schede")
        schede |= trovate
        # le schede appena lette possono rivelare altri settori pertinenti
        for url in sorted(trovate)[:30]:
            for c, kk, v in (_scheda(cat, url, gia_visti) or {}).get("filtri", []):
                candidati.setdefault((c, kk), v)
        nuovi_settori, _ = pertinenti(candidati)
        if campo == "settore":
            coda += [("settore", x) for x in nuovi_settori if ("settore", x) not in fatte]
    print(f"  {len(schede)} schede da controllare")

    risultati = []
    for url in sorted(schede)[:max_schede]:
        dati = _scheda(cat, url, gia_visti)
        if not dati or dati.get("nazione") not in (None, "ITALIA", "ITALY") or not dati.get("regione"):
            continue
        settori_az = classifica(dati["merci_fiera"], dati["settori_fiera"])
        if not settori_az or not dati.get("nome"):
            continue
        risultati.append({**dati, "settori": sorted(settori_az), "fiera": NOMI_FIERE.get(slug, slug)})
    return risultati
