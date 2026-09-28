"""Analisi del sito aziendale: email generiche, P.IVA, descrizione, prodotti.

Rispetta robots.txt, legge al massimo 3 pagine per sito e conserva solo
email aziendali generiche (info@, commerciale@, ...), non quelle nominative.
"""
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from classificazione import analizza_testo

UA = "RicercaAziendeBot/0.1 (ricerca contatti B2B; rispetta robots.txt)"
MAX_BYTES = 2_000_000
LINK_UTILI = re.compile(r"contatt|contact|chi-siamo|chisiamo|about|azienda|dove-siamo|note-legali|privacy", re.I)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PIVA_RE = re.compile(
    r"(?:p\.?\s*i\.?\s*v\.?\s*a\.?|partita\s+iva|vat(?:\s*(?:number|no\.?|n\.?))?)"
    r"[^0-9]{0,30}(?:IT)?\s*(\d{11})", re.I)

GENERICI = ("info", "commerciale", "sales", "vendite", "vendita", "ordini", "order", "export",
            "amministrazione", "contatti", "contact", "segreteria", "marketing", "ufficio",
            "office", "customer", "clienti", "servizio", "acquisti", "qualita", "hello",
            "shop", "negozio", "direzione", "produzione", "logistica", "admin", "mail",
            "posta", "sede", "azienda", "pec", "fatture", "support", "assistenza", "italia")
ESCLUSI = ("example.", "sentry", "wixpress", "domain.", "email.com", "yourdomain", "@2x")


def piva_valida(p: str) -> bool:
    if not re.fullmatch(r"\d{11}", p) or p == "0" * 11:
        return False
    somma = 0
    for i, c in enumerate(p[:10]):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        somma += d
    return (10 - somma % 10) % 10 == int(p[10])


def email_generica(email: str) -> bool:
    email = email.lower()
    locale, _, dominio = email.partition("@")
    if any(x in email for x in ESCLUSI) or re.search(r"\.(png|jpe?g|gif|svg|webp)$", email):
        return False
    return any(x in dominio for x in ("pec", "legalmail", "postacert", "cert.")) or any(locale.startswith(g) or g in locale for g in GENERICI)


def _robots(sess: requests.Session, base: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        r = sess.get(urljoin(base, "/robots.txt"), timeout=10)
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except requests.RequestException:
        rp.parse([])
    return rp


def _scarica(sess: requests.Session, url: str) -> tuple[str, str] | None:
    try:
        r = sess.get(url, timeout=15, stream=True, allow_redirects=True)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        contenuto = r.raw.read(MAX_BYTES, decode_content=True)
        r.encoding = r.encoding or "utf-8"
        return r.url, contenuto.decode(r.encoding, errors="replace")
    except (requests.RequestException, LookupError):
        return None


def analizza(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9"})

    pagina = _scarica(sess, url) or _scarica(sess, url.replace("https://", "http://", 1))
    if not pagina:
        return {"raggiungibile": False}

    url_finale, html = pagina
    base = f"{urlparse(url_finale).scheme}://{urlparse(url_finale).netloc}"
    robots = _robots(sess, base)
    if not robots.can_fetch(UA, url_finale):
        return {"raggiungibile": True, "bloccato_robots": True}

    soup = BeautifulSoup(html, "html.parser")
    titolo = soup.title.get_text(strip=True) if soup.title else None
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    descrizione = (meta.get("content") or "").strip()[:300] if meta else None

    pagine_html = [html]
    visti = {url_finale}
    candidati = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url_finale, a["href"]).split("#")[0]
        if urlparse(href).netloc == urlparse(url_finale).netloc and href not in visti \
                and LINK_UTILI.search(href + " " + a.get_text(" ", strip=True)):
            candidati.append(href)
            visti.add(href)
    for href in candidati[:2]:
        if robots.can_fetch(UA, href):
            time.sleep(1)
            extra = _scarica(sess, href)
            if extra:
                pagine_html.append(extra[1])

    testi, email = [], set()
    for h in pagine_html:
        s = BeautifulSoup(h, "html.parser")
        for a in s.select('a[href^="mailto:"]'):
            email.add(a["href"][7:].split("?")[0].strip())
        for tag in s(["script", "style", "noscript"]):
            tag.decompose()
        testi.append(s.get_text(" ", strip=True))
    testo = " ".join([titolo or "", descrizione or ""] + testi)
    email |= set(EMAIL_RE.findall(testo))

    piva = next((p for p in PIVA_RE.findall(testo) if piva_valida(p)), None)

    return {
        "raggiungibile": True,
        "url": url_finale,
        "titolo": titolo,
        "descrizione": descrizione,
        "email": sorted({e.lower() for e in email if email_generica(e)})[:5],
        "piva": piva,
        **analizza_testo(testo),
    }
