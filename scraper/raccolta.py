#!/usr/bin/env python3
"""Raccolta aziende panificati e dolciario da fonti aperte.

Fasi:
  1. scoperta   - OpenStreetMap regione per regione, espositori delle fiere (TuttoFood, Cibus)
  2. arricchimento - analisi dei siti web (email generiche, P.IVA, prodotti, B2B/B2C)
  3. verifica   - P.IVA su VIES
  4. output     - docs/aziende.json, un record per azienda (sedi raggruppate per sito)

Lo stato completo sta in data/stato.json, così ogni esecuzione riprende da dove
si era fermata e riarricchisce solo i siti più vecchi di --giorni-refresh.
"""
import argparse
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from classificazione import settori_da_osm
from fonti import fiere, osm, sito, vies

RADICE = Path(__file__).resolve().parent.parent
STATO = RADICE / "data" / "stato.json"
OUTPUT = RADICE / "docs" / "aziende.json"


def oggi() -> str:
    return dt.date.today().isoformat()


def carica(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def salva(path: Path, obj, compatto=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    opzioni = {"separators": (",", ":")} if compatto else {"indent": 1}
    tmp.write_text(json.dumps(obj, ensure_ascii=False, **opzioni), encoding="utf-8")
    tmp.replace(path)


def dominio(url: str | None) -> str | None:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    host = urlparse(url.strip()).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host or None


def scoperta(stato: dict, regioni: list[str], solo_con_sito: bool):
    for codice in regioni:
        print(f"[OSM] {codice} {osm.REGIONI[codice]} ...", flush=True)
        try:
            luoghi = osm.cerca_regione(codice)
        except Exception as e:  # una regione fallita non blocca le altre
            print(f"  errore: {e}")
            continue
        tenuti = 0
        for l in luoghi:
            if solo_con_sito and not dominio(l["sito"]):
                continue
            l["visto"] = oggi()
            stato["luoghi"][l["id"]] = {**stato["luoghi"].get(l["id"], {}), **l}
            tenuti += 1
        print(f"  {len(luoghi)} trovati, {tenuti} tenuti")
        time.sleep(5)


def scoperta_fiere(stato: dict, cataloghi: list[str]):
    visti = stato.setdefault("fiere", {})
    for slug in cataloghi:
        print(f"[FIERE] {slug} ...", flush=True)
        try:
            trovati = fiere.espositori(slug, visti)
        except Exception as e:
            print(f"  errore: {e}")
            continue
        for d in trovati:
            nome = d["nome"].title() if d["nome"].isupper() else d["nome"]
            lid = "fiera:" + urlparse(d["url"]).path.strip("/").split("/")[-1]
            vecchio = stato["luoghi"].get(lid, {})
            stato["luoghi"][lid] = {
                **vecchio, "id": lid, "nome": nome, "indirizzo": d["via"], "comune": d["comune"],
                "provincia": d["provincia"], "regione": d["regione"], "lat": None, "lon": None,
                "sito": d["sito"], "telefono": d["telefono"], "email": d["email"], "tag_osm": {},
                "settori_fonte": d["settori"], "descrizione_fonte": d["descrizione"],
                "prodotti_fonte": [m.lower() for m in d["merci_fiera"] if fiere.classifica([m])][:10],
                "fiere": sorted(set(vecchio.get("fiere", [])) | {d["fiera"]}), "visto": oggi(),
            }
        print(f"  {len(trovati)} aziende italiane del settore")


def arricchimento(stato: dict, max_siti: int, giorni_refresh: int, lavoratori: int):
    limite = (dt.date.today() - dt.timedelta(days=giorni_refresh)).isoformat()
    siti = {}
    for l in stato["luoghi"].values():
        d = dominio(l.get("sito"))
        if d:
            siti.setdefault(d, l["sito"])

    da_fare = [d for d in siti if stato["siti"].get(d, {}).get("aggiornato", "") < limite]
    da_fare.sort(key=lambda d: stato["siti"].get(d, {}).get("aggiornato", ""))  # mai visti per primi
    da_fare = da_fare[:max_siti]
    print(f"[SITI] {len(da_fare)} da analizzare su {len(siti)}", flush=True)

    fatti = 0
    with ThreadPoolExecutor(max_workers=lavoratori) as pool:
        futuri = {pool.submit(sito.analizza, siti[d]): d for d in da_fare}
        for f in as_completed(futuri):
            d = futuri[f]
            try:
                ris = f.result()
            except Exception as e:
                ris = {"raggiungibile": False, "errore": str(e)[:200]}
            stato["siti"][d] = {**ris, "aggiornato": oggi()}
            fatti += 1
            if fatti % 50 == 0:
                print(f"  {fatti}/{len(da_fare)}", flush=True)
                salva(STATO, stato)


def verifica_piva(stato: dict, max_verifiche: int):
    piva = {s["piva"] for s in stato["siti"].values() if s.get("piva")}
    da_fare = [p for p in piva if p not in stato["vies"]][:max_verifiche]
    print(f"[VIES] {len(da_fare)} P.IVA da verificare", flush=True)
    for p in da_fare:
        ris = vies.verifica(p)
        if ris:
            stato["vies"][p] = {**ris, "aggiornato": oggi()}
        time.sleep(1.5)


def costruisci_output(stato: dict) -> dict:
    gruppi: dict[str, list[dict]] = {}
    for l in stato["luoghi"].values():
        gruppi.setdefault(dominio(l.get("sito")) or l["id"], []).append(l)

    aziende = []
    for chiave, luoghi in gruppi.items():
        luoghi.sort(key=lambda l: l["id"])
        base = luoghi[0]
        web = stato["siti"].get(chiave, {})
        piva = web.get("piva")
        v = stato["vies"].get(piva, {}) if piva else {}

        settori = set(web.get("settori", []))
        for l in luoghi:
            settori |= settori_da_osm(l.get("tag_osm", {}), l.get("nome", ""))
            settori |= set(l.get("settori_fonte", []))
        fiere_az = sorted({f for l in luoghi for f in l.get("fiere", [])})
        prodotti = list(dict.fromkeys(web.get("prodotti", [])
                                      + [p for l in luoghi for p in l.get("prodotti_fonte", [])]))[:12]
        descr_fiera = next((l["descrizione_fonte"] for l in luoghi if l.get("descrizione_fonte")), None)
        email = [e for e in [l.get("email") for l in luoghi] if e] + web.get("email", [])
        sedi = [{k: l.get(k) for k in ("indirizzo", "comune", "provincia", "regione", "lat", "lon")}
                for l in luoghi]

        aziende.append({
            "id": chiave,
            "nome": base["nome"],
            "ragione_sociale": v.get("ragione_sociale"),
            "piva": piva,
            "piva_verificata": v.get("valida"),
            "sede_legale": v.get("indirizzo"),
            "sito": web.get("url") or base.get("sito"),
            "sito_raggiungibile": web.get("raggiungibile"),
            "email": list(dict.fromkeys(e.lower() for e in email))[:5],
            "telefono": next((l["telefono"] for l in luoghi if l.get("telefono")), None),
            "settori": sorted(settori),
            "prodotti": prodotti,
            "tipo_vendita": web.get("tipo_vendita"),
            "descrizione": web.get("descrizione") or descr_fiera,
            "fiere": fiere_az,
            "sedi": sedi,
            "regioni": sorted({s["regione"] for s in sedi}),
            # Campi riservati alle integrazioni a pagamento (Atoka, OpenAPI, Cerved...)
            "fatturato": None,
            "dipendenti": None,
            "salute": None,
            "fonti": (["OpenStreetMap"] if any(l["id"].startswith("osm:") for l in luoghi) else [])
                     + fiere_az + (["sito web"] if web.get("raggiungibile") else []) + (["VIES"] if v else []),
            "aggiornato": web.get("aggiornato") or base.get("visto"),
        })

    aziende.sort(key=lambda a: a["nome"].lower())
    return {"aggiornato": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
            "totale": len(aziende), "aziende": aziende}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regioni", default="tutte", help="codici separati da virgola (es. IT-25,IT-45) o 'tutte'")
    ap.add_argument("--max-siti", type=int, default=600, help="siti da analizzare per esecuzione")
    ap.add_argument("--max-vies", type=int, default=300, help="P.IVA da verificare per esecuzione")
    ap.add_argument("--giorni-refresh", type=int, default=60, help="dopo quanti giorni rianalizzare un sito")
    ap.add_argument("--lavoratori", type=int, default=8, help="siti analizzati in parallelo")
    ap.add_argument("--includi-senza-sito", action="store_true", help="tieni anche i luoghi senza sito web")
    ap.add_argument("--salta-scoperta", action="store_true", help="solo arricchimento dei dati già raccolti")
    ap.add_argument("--fiere", default="tuttofood-2026,cibus-2024",
                    help="cataloghi di catalogo.fiereparma.it separati da virgola, 'no' per saltarli")
    args = ap.parse_args()

    regioni = list(osm.REGIONI) if args.regioni.strip() in ("", "tutte") else \
        [r.strip().upper() for r in args.regioni.split(",")]
    sconosciute = [r for r in regioni if r not in osm.REGIONI]
    if sconosciute:
        raise SystemExit(f"Regioni non valide: {sconosciute}. Valide: {', '.join(osm.REGIONI)}")

    stato = carica(STATO, {"luoghi": {}, "siti": {}, "vies": {}})
    try:
        if not args.salta_scoperta:
            scoperta(stato, regioni, solo_con_sito=not args.includi_senza_sito)
            if args.fiere.strip().lower() not in ("", "no"):
                scoperta_fiere(stato, [f.strip() for f in args.fiere.split(",") if f.strip()])
        arricchimento(stato, args.max_siti, args.giorni_refresh, args.lavoratori)
        verifica_piva(stato, args.max_vies)
    finally:
        salva(STATO, stato)
        out = costruisci_output(stato)
        salva(OUTPUT, out, compatto=True)
        print(f"[OK] {out['totale']} aziende in {OUTPUT.relative_to(RADICE)}")


if __name__ == "__main__":
    main()
