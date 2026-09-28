"""Scoperta aziende da OpenStreetMap tramite Overpass API (gratuita, dati ODbL)."""
import time
import requests

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Codici ISO 3166-2 delle regioni, ordinati da nord a sud
REGIONI = {
    "IT-23": "Valle d'Aosta", "IT-21": "Piemonte", "IT-25": "Lombardia",
    "IT-32": "Trentino-Alto Adige", "IT-34": "Veneto", "IT-36": "Friuli-Venezia Giulia",
    "IT-42": "Liguria", "IT-45": "Emilia-Romagna", "IT-52": "Toscana",
    "IT-55": "Umbria", "IT-57": "Marche", "IT-62": "Lazio", "IT-65": "Abruzzo",
    "IT-67": "Molise", "IT-72": "Campania", "IT-75": "Puglia", "IT-77": "Basilicata",
    "IT-78": "Calabria", "IT-82": "Sicilia", "IT-88": "Sardegna",
}

PRODOTTI_RE = "pan|biscott|dolc|cioccol|pasticc|merend|confett|grissin|cracker|torron|forno|bakery|confection"
NOMI_RE = "dolciari|biscottific|panifici|pasticceri|cioccolat|confetteri|torronific|prodotti da forno|arte bianca"


def _query(codice: str) -> str:
    return f"""
[out:json][timeout:240];
area["ISO3166-2"="{codice}"]["admin_level"="4"]->.r;
(
  nwr["shop"~"^(bakery|pastry|confectionery|chocolate)$"](area.r);
  nwr["craft"~"^(bakery|confectionery|pastry_chef)$"](area.r);
  nwr["industrial"~"^(bakery|food|food_industry)$"](area.r);
  nwr["man_made"="works"]["product"~"{PRODOTTI_RE}",i](area.r);
  nwr["name"~"{NOMI_RE}",i]["website"](area.r);
);
out center tags;
"""


def _chiama(query: str) -> dict:
    ultimo_errore = None
    for tentativo in range(3):
        for url in ENDPOINTS:
            try:
                r = requests.post(url, data={"data": query}, timeout=300,
                                  headers={"User-Agent": "RicercaAziende/0.1"})
                if r.status_code == 200:
                    return r.json()
                ultimo_errore = f"HTTP {r.status_code} da {url}"
            except requests.RequestException as e:
                ultimo_errore = str(e)
        time.sleep(30 * (tentativo + 1))
    raise RuntimeError(f"Overpass non disponibile: {ultimo_errore}")


def _record(el: dict, codice: str) -> dict | None:
    tags = el.get("tags", {})
    nome = tags.get("name") or tags.get("brand") or tags.get("operator")
    if not nome:
        return None
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    via = " ".join(x for x in [tags.get("addr:street"), tags.get("addr:housenumber")] if x)
    return {
        "id": f"osm:{el['type'][0]}{el['id']}",
        "nome": nome.strip(),
        "indirizzo": ", ".join(x for x in [via, tags.get("addr:postcode")] if x) or None,
        "comune": tags.get("addr:city"),
        "provincia": tags.get("addr:province"),
        "regione": REGIONI[codice],
        "lat": lat,
        "lon": lon,
        "sito": tags.get("website") or tags.get("contact:website") or tags.get("url"),
        "telefono": tags.get("phone") or tags.get("contact:phone"),
        "email": tags.get("email") or tags.get("contact:email"),
        "tag_osm": {k: v for k, v in tags.items()
                    if k in ("shop", "craft", "industrial", "man_made", "product", "cuisine")},
    }


def cerca_regione(codice: str) -> list[dict]:
    dati = _chiama(_query(codice))
    risultati = []
    for el in dati.get("elements", []):
        rec = _record(el, codice)
        if rec:
            risultati.append(rec)
    return risultati
