"""Classificazione euristica: settore, prodotti e tipo di vendita (B2B/B2C)."""
import re

PRODOTTI = {
    "Panificati": ["pane", "grissini", "crackers", "fette biscottate", "taralli", "focaccia",
                   "focacce", "piadina", "piadine", "friselle", "pan carrè", "pancarrè",
                   "pane in cassetta", "pinsa", "basi pizza", "prodotti da forno", "panificio",
                   "arte bianca", "lievito madre", "bruschette", "crostini"],
    "Dolciario": ["biscotti", "panettone", "pandoro", "colomba", "cioccolato", "praline",
                  "torrone", "confetti", "caramelle", "merendine", "croissant", "cornetti",
                  "pasticceria", "wafer", "amaretti", "cantucci", "cantuccini", "savoiardi",
                  "crostate", "dolci", "dolciaria", "dolciario", "gelato", "semifreddi",
                  "mignon", "brioche", "tiramisù", "cannoli", "sfogliatelle", "meringhe"],
}

SEGNALI = {
    "B2B": ["gdo", "grande distribuzione", "private label", "marchio del distributore",
            "horeca", "ingrosso", "grossisti", "distributori", "rivenditori", "export",
            "stabilimento", "linee di produzione", "co-packing", "copacking", "foodservice",
            "catering", "richiedi un preventivo", "listino", "partner commerciali", "ifs", "brc"],
    "B2C": ["aggiungi al carrello", "carrello", "shop online", "negozio online", "acquista ora",
            "spedizione gratuita", "consegna a domicilio", "i nostri negozi", "punti vendita",
            "orari di apertura", "vieni a trovarci", "ordina online", "e-commerce"],
}

_cache = {}


def _pattern(parola: str) -> re.Pattern:
    if parola not in _cache:
        _cache[parola] = re.compile(r"(?<![\w])" + re.escape(parola) + r"(?![\w])", re.I)
    return _cache[parola]


def _conta(testo: str, parole: list[str]) -> dict[str, int]:
    return {p: len(_pattern(p).findall(testo)) for p in parole if _pattern(p).search(testo)}


def settori_da_osm(tag: dict, nome: str = "") -> set[str]:
    s = set()
    valori = (" ".join(tag.values()) + " " + nome).lower()
    if any(v in valori for v in ("bakery", "panific", "pane ", "grissin", "cracker", "forno", "arte bianca")):
        s.add("Panificati")
    if any(v in valori for v in ("pastry", "confection", "chocolate", "dolc", "biscott",
                                 "cioccol", "torron", "merend", "pasticc", "panetton", "pandor", "confett")):
        s.add("Dolciario")
    return s


def analizza_testo(testo: str) -> dict:
    testo = testo or ""
    settori, prodotti = [], []
    for settore, parole in PRODOTTI.items():
        trovati = _conta(testo, parole)
        if sum(trovati.values()) >= 2:
            settori.append(settore)
        prodotti += sorted(trovati, key=trovati.get, reverse=True)[:6]

    punti = {k: sum(_conta(testo, v).values()) for k, v in SEGNALI.items()}
    b2b, b2c = punti["B2B"] >= 2, punti["B2C"] >= 2
    tipo = "B2B e B2C" if b2b and b2c else "B2B" if b2b else "B2C" if b2c else None

    return {"settori": settori, "prodotti": prodotti[:10], "tipo_vendita": tipo}
