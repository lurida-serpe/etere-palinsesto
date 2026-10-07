# Etere – palinsesto e stazioni curate

L'app scarica **un solo file**, `curated.json`, da GitHub. Dentro ci sono l'elenco delle stazioni curate
e il palinsesto settimanale (con la copertina di ogni trasmissione). Modifichi il file → le app si aggiornano
da sole (controllano al massimo ogni 30 minuti; GitHub tiene il file in cache ~5 minuti).

## Cosa c'è qui

| File | A cosa serve |
|---|---|
| `etere_scraper.py` | scarica i palinsesti (Blackout, Onda Rossa, Onda d'Urto), abbina le copertine, scrive `curated.json` e `covers/` |
| `test_scraper.py` | test senza rete: `python -m unittest -v test_scraper` |
| `.github/workflows/update-palinsesto.yml` | lo fa girare ogni giorno su GitHub |

## Uso a mano (sul tuo PC)

Metti accanto allo script le cartelle `trasmissioni-blackout/`, `trasmissioni-ondarossa/` (e un giorno
`trasmissioni-ondadurto/`), con un'immagine per trasmissione, **nome file = titolo** come nello script Python
(maiuscole, punteggiatura e accenti non contano).

    pip install pillow
    python etere_scraper.py --cover-base https://raw.githubusercontent.com/UTENTE/REPO/main/covers/

Esce `curated.json` + la cartella `covers/` (immagini già ridimensionate: ~5 MB invece di ~29).
Carica entrambi nel repository. Altre opzioni: `--only blackout`, `--offline`, `--pretty`, `--strict`.

## Come funziona il palinsesto

* Il JSON contiene la **settimana tipo** (lun–dom, orari `HH:MM` come sul sito); l'app calcola da sola cosa è in onda.
* **Radio Blackout** pubblica nell'HTML solo il giorno di oggi: ogni esecuzione aggiorna il giorno corrente e tiene
  gli altri sei dalla volta prima. Serve farlo girare ogni giorno (ci pensa il workflow): dopo una settimana è completo.
* Se un sito non risponde restano i dati precedenti. Il file viene riscritto solo se qualcosa è cambiato.
* Le copertine hanno nel JSON un `?v=<hash>`: se cambi un'immagine l'app la riscarica.

## Aggiungere o togliere una stazione

Modifica la lista `STATIONS` in `etere_scraper.py` (o direttamente `curated.json`) e fai commit.
Per dare il palinsesto a un'altra radio serve scrivere il suo parser e registrarlo in `SCRAPERS`.

## Formato di `curated.json` (schema 1)

    {
      "schema": 1,
      "updated": "2026-10-06T10:00:00Z",
      "coverBase": "https://raw.githubusercontent.com/UTENTE/REPO/main/covers/",
      "stations": [{
        "id": "blackout", "name": "Radio Blackout", "stream": "http://…", "also": ["altro stream uguale"],
        "frequency": "105.25 FM / DAB", "city": "Torino", "lat": 45.07, "lon": 7.68, "region": "Piemonte",
        "home": "https://…", "scheduleUrl": "https://…", "scheduleWeek": [7 link, lun…dom],
        "cover": "asset:covers/blackout.png",      // immagine già nell'app, oppure percorso dentro coverBase
        "tz": "Europe/Rome",
        "shows": [{"d": 0, "s": "09:00", "e": "11:00", "t": "Titolo", "u": "https://pagina", "c": "blackout/titolo.webp?v=1a2b3c4d"}]
      }]
    }

`d` = 0 lunedì … 6 domenica. Se `e` è uguale o minore di `s` la trasmissione finisce il giorno dopo.
Se due voci si sovrappongono vince quella che è iniziata per ultima. Una stazione nel JSON compare nella ricerca
e sulla mappa; una salvata dall'utente prende il palinsesto se ha lo stesso stream (o è stata aggiunta da qui).
