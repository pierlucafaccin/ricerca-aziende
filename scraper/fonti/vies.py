"""Verifica P.IVA tramite il servizio VIES della Commissione Europea (gratuito)."""
import requests

URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/IT/vat/{}"


def verifica(piva: str) -> dict | None:
    """Restituisce {valida, ragione_sociale, indirizzo} oppure None se il servizio non risponde."""
    try:
        r = requests.get(URL.format(piva), timeout=20, headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        d = r.json()
        if d.get("userError") not in (None, "VALID", "INVALID"):
            return None  # servizio nazionale momentaneamente non disponibile: si riprova al giro dopo
        pulisci = lambda s: " ".join(s.split()) if s and s.strip() not in ("---", "") else None
        return {
            "valida": bool(d.get("isValid")),
            "ragione_sociale": pulisci(d.get("name")),
            "indirizzo": pulisci(d.get("address")),
        }
    except (requests.RequestException, ValueError):
        return None
