# Ricerca aziende panificati e dolciario

Motore di ricerca di aziende italiane del settore panificati e dolciario, costruito solo con fonti gratuite.
Gira interamente su GitHub: nessun server e nessun database da gestire.

- **GitHub Actions** raccoglie e arricchisce i dati ogni lunedì (o quando lo avvii a mano).
- **GitHub Pages** pubblica la pagina di ricerca in `docs/`, che legge `docs/aziende.json`.

## Messa online (una volta sola)

1. Crea un repository **pubblico** su GitHub e carica tutti i file di questa cartella
   (compresa la cartella nascosta `.github`).
2. *Settings → Actions → General → Workflow permissions*: scegli **Read and write permissions** e salva.
3. *Settings → Pages*: in *Source* scegli **Deploy from a branch**, branch `main`, cartella `/docs`.
   Dopo un minuto la pagina è su `https://<utente>.github.io/<repository>/`.
4. *Actions → Aggiorna dati aziende → Run workflow*. Per la prima prova metti una sola regione,
   per esempio `IT-25` (Lombardia) e `max_siti` a `100`.

Alla fine il workflow salva i risultati nel repository e la pagina si aggiorna da sola.

> Con l'account GitHub gratuito Pages funziona solo con repository pubblici: dati e codice sono visibili a tutti.

## Codici regione

| Codice | Regione | Codice | Regione |
|---|---|---|---|
| IT-23 | Valle d'Aosta | IT-57 | Marche |
| IT-21 | Piemonte | IT-62 | Lazio |
| IT-25 | Lombardia | IT-65 | Abruzzo |
| IT-32 | Trentino-Alto Adige | IT-67 | Molise |
| IT-34 | Veneto | IT-72 | Campania |
| IT-36 | Friuli-Venezia Giulia | IT-75 | Puglia |
| IT-42 | Liguria | IT-77 | Basilicata |
| IT-45 | Emilia-Romagna | IT-78 | Calabria |
| IT-52 | Toscana | IT-82 | Sicilia |
| IT-55 | Umbria | IT-88 | Sardegna |

## Come funziona la raccolta

| Fase | Fonte | Cosa ricava |
|---|---|---|
| Scoperta | OpenStreetMap (Overpass API) | nome, indirizzo, coordinate, sito, telefono |
| Arricchimento | sito dell'azienda (home + fino a 2 pagine contatti/chi siamo) | email generiche, P.IVA, descrizione, prodotti, B2B/B2C |
| Verifica | VIES (Commissione Europea) | validità P.IVA, ragione sociale, sede legale |

Le sedi con lo stesso sito vengono raggruppate in un'unica azienda.
Lo stato completo sta in `data/stato.json`: ogni esecuzione riprende da lì e rianalizza un sito
solo se è più vecchio di 60 giorni. Se ci sono molti siti, bastano più esecuzioni
(anche con *salta_scoperta* attivo) per completarli.

Settore e tipo di vendita sono stimati con parole chiave (per esempio "GDO", "private label", "horeca"
per il B2B; "carrello", "shop online" per il B2C). Le liste sono in `scraper/classificazione.py`
e si possono ampliare liberamente.

## Regole rispettate

- Si legge `robots.txt` e ci si ferma se il sito lo vieta.
- Si conservano solo email aziendali generiche (info@, commerciale@, PEC…), mai quelle nominative.
- Nessuno scraping di directory o banche dati con termini d'uso che lo vietano.

## Esecuzione in locale

```bash
pip install -r scraper/requirements.txt
python scraper/raccolta.py --regioni IT-25 --max-siti 50
cd docs && python -m http.server 8000   # poi apri http://localhost:8000
```

Opzioni utili: `--includi-senza-sito`, `--salta-scoperta`, `--giorni-refresh`, `--lavoratori`.

## Prossimi passi: fonti a pagamento

Fatturato, dipendenti e stato di salute sono già previsti nel JSON (`fatturato`, `dipendenti`, `salute`)
e la pagina li mostra appena sono valorizzati. Per integrarli:

1. aggiungi un modulo in `scraper/fonti/` (per esempio `atoka.py` oppure `openapi.py`) con una funzione
   che riceve una P.IVA e restituisce i dati;
2. chiamalo in `raccolta.py` dopo la verifica VIES, salvando i risultati in `stato["bilanci"]`;
3. valorizza i tre campi in `costruisci_output`;
4. salva la chiave API in *Settings → Secrets and variables → Actions* e passala al workflow come variabile d'ambiente.

La P.IVA è la chiave che collega tutte le fonti, per questo la raccolta gratuita la cerca su ogni sito.
