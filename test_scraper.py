"""Test offline dello scraper: pagine finte con la stessa struttura HTML dei siti (niente rete).

    python -m unittest -v test_scraper
"""
import json
import os
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace

import etere_scraper as sc

# ------------------------------------------------------------------ pagine finte

BLACKOUT = """
<ul>
<li class="flex w-full rbo-show-item"><span class="rbo-orario">00:00 - 07:00 </span>
 <div class="rbo-trasmissione"><a href="/shows/musica-notte" class="rbo-trasmissione"><span>Musica di Notte</span></a></div></li>
<li class="flex w-full rbo-show-item selected"><span class="rbo-orario">09:00 - 11:00 </span>
 <div class="rbo-trasmissione"><a href="/shows/matinata" class="rbo-trasmissione"><span>Matinata</span></a></div></li>
<li class="flex w-full rbo-show-item"><span class="rbo-orario">22:00 - 02:00 </span>
 <div class="rbo-trasmissione"><a href="https://radioblackout.org/shows/tarda" class="rbo-trasmissione"><span>CONTRO&#8260;BANDE &amp; co</span></a></div></li>
</ul>
"""

ONDAROSSA = """
<h3>Lunedì</h3><table>
<tr><td>07:30 - 09:00</td><td><a href="/rassegna">Rassegna stampa</a> -</td></tr>
<tr><td>23:00 - 01:00</td><td>Notte lunga</td></tr>
<tr><td>01:00 - 02:00</td><td>Dopo mezzanotte</td></tr>
</table>
<h3>Martedì</h3><table>
<tr><td>01:00 - 03:00</td><td>Replica: Ponte Radio</td></tr>
<tr><td>10:00 - 11:00</td><td>&nbsp;</td></tr>
</table>
<h3>Altro</h3><table><tr><td>10:00 - 11:00</td><td>Ignorato</td></tr></table>
"""

# vecchio formato (con intestazioni "PALINSESTO ...")
ONDADURTO_OLD = """
<p><em>PALINSESTO STRAORDINARIO FESTA</em></p><p>8.00 Da ignorare</p>
<p><em>PALINSESTO NORMALE</em></p>
<div>8.00 Rassegna</div><div>13.25 – Info <a href="/podcast/info">x</a></div><div>23.30 Musica</div><div>00.00 Notturno (R)</div>
"""
# nuovo formato (solo entry-content, <br> tra le righe)
ONDADURTO_NEW = """<div class="entry-content">8.00 Rassegna<br>12.00 Pranzo<br>20.00 Sera</div>"""


def fake_fetch(pages):
    def fetch(url):
        for key, page in pages.items():
            if key in url:
                if isinstance(page, Exception):
                    raise page
                return page
        raise OSError("404 " + url)
    return fetch


class BlackoutTests(unittest.TestCase):
    def test_parse_today_only(self):
        days = sc.parse_blackout(BLACKOUT, weekday=2)  # mercoledì
        self.assertEqual(list(days), [2])
        titles = [s[3] for s in days[2]]
        self.assertEqual(titles, ["Musica di Notte", "Matinata", "CONTRO\u2044BANDE & co"])
        self.assertEqual(days[2][0][4], "https://radioblackout.org/shows/musica-notte")
        self.assertEqual(days[2][2][1:3], (22 * 60, 2 * 60))   # fine <= inizio: finisce il giorno dopo

    def test_rollover_goes_to_next_day(self):
        page = BLACKOUT.replace("00:00 - 07:00", "23:00 - 23:30")  # il primo orario è alle 23, poi 09:00 "torna indietro"
        days = sc.parse_blackout(page, weekday=6)  # domenica -> lunedì
        self.assertEqual(sorted(days), [0, 6])


class OndaRossaTests(unittest.TestCase):
    def test_week_and_spill(self):
        days = sc.parse_ondarossa(ONDAROSSA)
        self.assertEqual(sorted(days), [0, 1])
        lun = {s[1]: s for s in days[0]}
        self.assertEqual(lun[7 * 60 + 30][3], "Rassegna stampa")        # " -" finale tolto
        self.assertEqual(lun[7 * 60 + 30][4], "https://www.ondarossa.info/rassegna")
        # "01:00" dopo "23:00" appartiene a martedì, ma martedì ha già il suo slot alle 01:00: vince il suo
        mar = [s for s in days[1] if s[1] == 60]
        self.assertEqual([s[3] for s in mar], ["Replica: Ponte Radio"])
        self.assertTrue(all(s[3] != "Ignorato" for d in days.values() for s in d))
        self.assertFalse(any(s[3] == "" for d in days.values() for s in d))


class OndaDUrtoTests(unittest.TestCase):
    def test_old_and_new_format(self):
        old = sc._ou_day_entries(ONDADURTO_OLD)
        self.assertEqual([e[1] for e in old], ["Rassegna", "Info x", "Musica", "Notturno (R)"])
        self.assertEqual(old[1][2], "https://www.radiondadurto.org/podcast/info")
        new = sc._ou_day_entries(ONDADURTO_NEW)
        self.assertEqual([(e[0], e[1]) for e in new], [(480, "Rassegna"), (720, "Pranzo"), (1200, "Sera")])

    def test_ends_from_next_start_and_midnight(self):
        pages = {f"/{slug}/": ONDADURTO_OLD for slug, _ in sc._OU_DAYS}
        days = sc.scrape_ondadurto(fake_fetch(pages))
        mon = {s[1]: s for s in days[0]}
        self.assertEqual(mon[480][2], 13 * 60 + 25)        # fine = inizio della successiva
        # "00.00 Notturno" dopo le 23.30 è già martedì 00:00; "Musica" 23:30 finisce a mezzanotte
        self.assertEqual(mon[23 * 60 + 30][2], 24 * 60)
        tue0 = [s for s in days[1] if s[1] == 0]
        self.assertEqual(tue0[0][3], "Notturno (R)")
        self.assertEqual(tue0[0][2], 8 * 60)               # fino alle 8.00 di martedì

    def test_missing_day_uses_previous(self):
        pages = {f"/{slug}/": ONDADURTO_OLD for slug, _ in sc._OU_DAYS}
        full = sc.scrape_ondadurto(fake_fetch(pages))
        pages["/mercoledi/"] = OSError("giù")
        partial = sc.scrape_ondadurto(fake_fetch(pages), previous=full)
        self.assertEqual(sorted(partial[2]), sorted(full[2]))   # mercoledì ripreso dalla volta scorsa
        # senza dati precedenti la durata è limitata
        partial2 = sc.scrape_ondadurto(fake_fetch(pages))
        self.assertTrue(all(s[2] - s[1] <= sc.OU_MAX_MINUTES for d in partial2.values() for s in d))

    def test_all_pages_down(self):
        with self.assertRaises(RuntimeError):
            sc.scrape_ondadurto(fake_fetch({}))


class CoverTests(unittest.TestCase):
    def test_matching_rules(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ["Matinata.jpg", "CONTRO\u2044BANDE.webp", "L’Informazione di Blackout.jpg",
                         "Città Aperta.png", "nota.txt", "Replica: Ponte Radio.png"]:
                open(os.path.join(d, name), "wb").write(b"x")
            idx = sc.CoverIndex(d)
            self.assertTrue(idx.find("matinata").endswith("Matinata.jpg"))                       # maiuscole
            self.assertTrue(idx.find("CONTRO/BANDE").endswith("CONTRO\u2044BANDE.webp"))         # slash diversi
            self.assertTrue(idx.find("L'Informazione di Blackout").endswith(".jpg"))            # apostrofo
            self.assertTrue(idx.find("Citta Aperta").endswith("Città Aperta.png"))               # accenti
            self.assertTrue(idx.find("Replica: Ponte Radio").endswith(".png"))
            self.assertIsNone(idx.find("Non esiste"))
            self.assertEqual(len(idx.files), 5)                                                  # nota.txt escluso

    def test_slug(self):
        self.assertEqual(sc.slugify("L’Informazione di Blackout"), "l-informazione-di-blackout")
        self.assertEqual(sc.slugify("4×4 MONSTER TRACK"), "4-4-monster-track")
        self.assertEqual(sc.slugify("???"), "show")


class BuildTests(unittest.TestCase):
    def args(self, tmp, **kw):
        base = dict(out=os.path.join(tmp, "curated.json"), previous=None, cover_base="https://x/covers/",
                    covers_src=os.path.join(tmp, "src"), covers_out=os.path.join(tmp, "covers"),
                    only=None, offline=False, strict=False, pretty=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_blackout_keeps_other_days_and_failures_keep_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = {"radioblackout": BLACKOUT, "ondarossa": ONDAROSSA}
            pages.update({f"radiondadurto.org/palinsesto/{s}/": ONDADURTO_NEW for s, _ in sc._OU_DAYS})
            a = self.args(tmp)
            # primo giro: "oggi" = mercoledì
            orig = sc._rome_today
            try:
                sc._rome_today = lambda: date(2026, 10, 7)   # mercoledì
                data, fails = sc.build(a, fake_fetch(pages))
                self.assertEqual(fails, [])
                json.dump(data, open(a.out, "w"))
                bo = next(s for s in data["stations"] if s["id"] == "blackout")
                self.assertEqual({s["d"] for s in bo["shows"]}, {2})
                # secondo giro: "oggi" = giovedì, e Onda Rossa non risponde
                pages["ondarossa"] = OSError("giù")
                sc._rome_today = lambda: date(2026, 10, 8)
                a2 = self.args(tmp, previous=a.out)
                data2, fails2 = sc.build(a2, fake_fetch(pages))
                self.assertEqual(fails2, ["ondarossa"])
                bo2 = next(s for s in data2["stations"] if s["id"] == "blackout")
                self.assertEqual({s["d"] for s in bo2["shows"]}, {2, 3})          # mercoledì conservato
                or2 = next(s for s in data2["stations"] if s["id"] == "ondarossa")
                self.assertTrue(or2["shows"])                                      # dati della volta scorsa
            finally:
                sc._rome_today = orig

    def test_json_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = sc.build(self.args(tmp, offline=True), fake_fetch({}))
            self.assertEqual(data["schema"], 1)
            ids = [s["id"] for s in data["stations"]]
            self.assertEqual(len(ids), 12)
            quar = next(s for s in data["stations"] if s["id"] == "quar")
            self.assertNotIn("lat", quar)                   # radio diffusa: senza posizione
            self.assertEqual(quar["tz"], "Europe/Rome")
            self.assertTrue(data["coverBase"].endswith("/"))

    def test_covers_are_converted(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow assente")
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src", "trasmissioni-blackout")
            os.makedirs(src)
            Image.new("RGB", (1600, 1200), (200, 30, 30)).save(os.path.join(src, "matinata.jpg"))
            pages = {"radioblackout": BLACKOUT}
            orig = sc._rome_today
            sc._rome_today = lambda: date(2026, 10, 7)
            try:
                data, _ = sc.build(self.args(tmp, only="blackout"), fake_fetch(pages))
            finally:
                sc._rome_today = orig
            bo = next(s for s in data["stations"] if s["id"] == "blackout")
            mat = next(s for s in bo["shows"] if s["t"] == "Matinata")
            self.assertRegex(mat["c"], r"^blackout/matinata\.webp\?v=[0-9a-f]{8}$")
            out = os.path.join(tmp, "covers", "blackout", "matinata.webp")
            with Image.open(out) as im:
                self.assertLessEqual(max(im.size), sc.COVER_MAX_PX)
            self.assertNotIn("c", next(s for s in bo["shows"] if s["t"] == "Musica di Notte"))


if __name__ == "__main__":
    unittest.main()
