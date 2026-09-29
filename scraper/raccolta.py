#!/usr/bin/env python3
"""Raccolta aziende panificati e dolciario da fonti aperte.

Fasi:
  1. scoperta   - OpenStreetMap regione per regione, espositori delle fiere (TuttoFood, Cibus)
  2. arricchimento - analisi dei siti web (email generiche, P.IVA, prodotti, B2B/B2C)
  3. verifica   - P.IVA su VIES
  4. output     - docs/aziende.json, un record per azienda (sedi raggruppate per sito o marchio)

Lo stato completo sta in data/stato.json, così ogni esecuzione riprende da dove
si era fermata e riarricchisce solo i siti più vecchi di --giorni-refresh.
"""
import argparse
import re
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


# Profili social e pagine di servizi condivisi: non identificano un'azienda
SOCIAL = ("facebook.com", "instagram.com", "linktr.ee", "tripadvisor.", "google.", "goo.gl",
          "youtube.com", "tiktok.com", "wa.me", "whatsapp.com", "twitter.com", "x.com", "linkedin.com",
          "paginegialle.it", "just-eat", "deliveroo", "glovo")


def dominio(url: str | None) -> str | None:
    """Dominio del sito, usato per unire le sedi della stessa azienda (None per i social)."""
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    host = urlparse(url.strip()).netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    if not host or any(host == x or host.endswith("." + x) or x.endswith(".") and x in host for x in SOCIAL):
        return None
    return host


MIN_LOCALI_CATENA = 3       # locali con lo stesso marchio per parlare di catena
MIN_LOCALI_STESSO_SITO = 2  # lo stesso sito web su due locali distinti è già un indizio forte


def _norm_marchio(m: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9àèéìòù ]", " ", m.lower()).replace("pizzeria", "").split())


def raggruppa(luoghi) -> dict[str, list[dict]]:
    """Unisce le sedi della stessa azienda: per sito web e, per la ristorazione, anche per marchio."""
    luoghi = list(luoghi)
    marchio_di_dominio = {}
    for l in luoghi:
        if l.get("categoria") == "ristorazione" and l.get("brand") and dominio(l.get("sito")):
            marchio_di_dominio.setdefault(dominio(l["sito"]), _norm_marchio(l["brand"]))
    gruppi: dict[str, list[dict]] = {}
    for l in luoghi:
        d = dominio(l.get("sito"))
        if l.get("categoria") == "ristorazione":
            m = _norm_marchio(l["brand"]) if l.get("brand") else marchio_di_dominio.get(d)
            chiave = f"marchio:{m}" if m else (d or l["id"])
        else:
            chiave = d or l["id"]
        gruppi.setdefault(chiave, []).append(l)
    # la ristorazione conta solo se è una catena
    def catena(k, v):
        soglia = MIN_LOCALI_CATENA if k.startswith("marchio:") else MIN_LOCALI_STESSO_SITO
        return len(v) >= soglia
    return {k: v for k, v in gruppi.items()
            if any(l.get("categoria") != "ristorazione" for l in v) or catena(k, v)}


def scoperta(stato: dict, regioni: list[str], solo_con_sito: bool, ristorazione: bool):
    for codice in regioni:
        print(f"[OSM] {codice} {osm.REGIONI[codice]} ...", flush=True)
        try:
            luoghi = osm.cerca_regione(codice)
        except Exception as e:  # una regione fallita non blocca le altre
            print(f"  errore: {e}")
            continue
        tenuti = 0
        for l in luoghi:
            if solo_con_sito and not l.get("sito"):
                continue
            l["visto"] = oggi()
            stato["luoghi"][l["id"]] = {**stato["luoghi"].get(l["id"], {}), **l}
            tenuti += 1
        print(f"  {len(luoghi)} trovati, {tenuti} tenuti (con sito web)")
        time.sleep(5)
        if not ristorazione:
            continue
        try:
            locali = osm.cerca_ristorazione(codice)
        except Exception as e:
            print(f"  ristorazione, errore: {e}")
            continue
        nuovi = 0
        for l in locali:
            esistente = stato["luoghi"].get(l["id"])
            if not (l.get("sito") or l.get("brand")) or (esistente and esistente.get("categoria") != "ristorazione"):
                continue  # senza sito né marchio, oppure già presente come produttore
            l["visto"] = oggi()
            stato["luoghi"][l["id"]] = {**stato["luoghi"].get(l["id"], {}), **l}
            nuovi += 1
        print(f"  ristorazione: {len(locali)} locali trovati, {nuovi} con sito o marchio")
        time.sleep(5)


def scoperta_fiere(stato: dict, cataloghi: list[str], max_schede: int):
    visti = stato.setdefault("fiere", {})
    fiere.DEBUG_DIR = RADICE / "data" / "debug"
    trovati_ora = set()
    for slug in cataloghi:
        print(f"[FIERE] {slug} ...", flush=True)
        try:
            trovati = fiere.espositori(slug, visti, max_schede, salva=lambda: salva(STATO, stato))
        except Exception as e:
            print(f"  errore: {e}")
            continue
        for d in trovati:
            nome = d["nome"].title() if d["nome"].isupper() else d["nome"]
            lid = "fiera:" + urlparse(d["url"]).path.strip("/").split("/")[-1]
            trovati_ora.add(lid)
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
    if trovati_ora:  # toglie gli espositori di giri precedenti che non superano più i filtri
        for lid in [k for k in stato["luoghi"] if k.startswith("fiera:") and k not in trovati_ora]:
            del stato["luoghi"][lid]


def arricchimento(stato: dict, max_siti: int, giorni_refresh: int, lavoratori: int):
    limite = (dt.date.today() - dt.timedelta(days=giorni_refresh)).isoformat()
    siti = {}
    for gruppo in raggruppa(stato["luoghi"].values()).values():
        for l in gruppo:
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
    gruppi = raggruppa(stato["luoghi"].values())

    aziende = []
    for chiave, luoghi in gruppi.items():
        luoghi.sort(key=lambda l: l["id"])
        base = luoghi[0]
        domini = [d for l in luoghi if (d := dominio(l.get("sito")))]
        web = next((stato["siti"][d] for d in domini if d in stato["siti"]), {})
        piva = web.get("piva")
        v = stato["vies"].get(piva, {}) if piva else {}

        settori = set(web.get("settori", []))
        for l in luoghi:
            settori |= settori_da_osm(l.get("tag_osm", {}), l.get("nome", ""))
            settori |= set(l.get("settori_fonte", []))
            if l.get("categoria") == "ristorazione":
                settori.add("Ristorazione organizzata")
        fiere_az = sorted({f for l in luoghi for f in l.get("fiere", [])})
        prodotti = list(dict.fromkeys(web.get("prodotti", [])
                                      + [p for l in luoghi for p in l.get("prodotti_fonte", [])]))[:12]
        descr_fiera = next((l["descrizione_fonte"] for l in luoghi if l.get("descrizione_fonte")), None)
        email = [e for e in [l.get("email") for l in luoghi] if e] + web.get("email", [])
        sedi = [{k: l.get(k) for k in ("indirizzo", "comune", "provincia", "regione", "lat", "lon")}
                for l in luoghi]

        aziende.append({
            "id": chiave,
            "nome": next((l["brand"] for l in luoghi if l.get("categoria") == "ristorazione" and l.get("brand")),
                         None) or base["nome"],
            "ragione_sociale": v.get("ragione_sociale"),
            "piva": piva,
            "piva_verificata": v.get("valida"),
            "sede_legale": v.get("indirizzo"),
            "sito": web.get("url") or next((l["sito"] for l in luoghi if l.get("sito")), None),
            "sito_raggiungibile": web.get("raggiungibile"),
            "email": list(dict.fromkeys(e.lower() for e in email))[:5],
            "locali": len(luoghi) if any(l.get("categoria") == "ristorazione" for l in luoghi) else None,
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
    ap.add_argument("--senza-ristorazione", action="store_true",
                    help="non cercare pizzerie e catene di ristorazione")
    ap.add_argument("--salta-scoperta", action="store_true", help="solo arricchimento dei dati già raccolti")
    ap.add_argument("--max-schede-fiere", type=int, default=500,
                    help="schede espositore nuove da leggere per fiera a ogni esecuzione")
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
            scoperta(stato, regioni, solo_con_sito=not args.includi_senza_sito,
                     ristorazione=not args.senza_ristorazione)
            if args.fiere.strip().lower() not in ("", "no"):
                scoperta_fiere(stato, [f.strip() for f in args.fiere.split(",") if f.strip()], args.max_schede_fiere)
        arricchimento(stato, args.max_siti, args.giorni_refresh, args.lavoratori)
        verifica_piva(stato, args.max_vies)
    finally:
        salva(STATO, stato)
        out = costruisci_output(stato)
        salva(OUTPUT, out, compatto=True)
        print(f"[OK] {out['totale']} aziende in {OUTPUT.relative_to(RADICE)}")


if __name__ == "__main__":
    main()
