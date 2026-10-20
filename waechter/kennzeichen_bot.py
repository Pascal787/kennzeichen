"""Kennzeichen-Wächter für Wunschkennzeichen der Zulassungsstelle Stuttgart.

Fragt über das Behörden-Serviceportal ab, welche Kennzeichen mit den gesuchten
Zahlen (z. B. S-AB 911) frei sind, speichert die Liste und meldet sich, sobald
ein neues Kennzeichen frei wird. Reserviert wird nichts.

Aufruf:  python kennzeichen_bot.py          (ein Prüflauf)
         python kennzeichen_bot.py --test   (Testbenachrichtigung senden)
"""

import http.cookiejar
import json
import logging
import os
import random
import smtplib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

# Läuft entweder auf dem PC (Windows-Aufgabe) oder bei GitHub Actions. Bei GitHub kommen die
# Einstellungen aus dem Secret KZ_CONFIG, und das Hochladen übernimmt der Workflow.
CLOUD = os.environ.get("GITHUB_ACTIONS") == "true"
ORDNER = Path(__file__).resolve().parent
CONFIG = ORDNER / "config.json"
ZUSTAND = Path(os.environ.get("KZ_ZUSTAND", ORDNER / "frei.json"))
LISTE = ORDNER / "freie_kennzeichen.txt"
LOG = ORDNER / "verlauf.log"
WEBSITE = Path(os.environ.get("KZ_WEBSITE", ORDNER / "website"))  # Git-Repo, per GitHub Pages veröffentlicht
WEBSITE_DATEN = WEBSITE / "daten.json"
WEBSITE_HERZSCHLAG = 2 * 3600  # auch ohne Änderung spätestens so oft hochladen, damit „geprüft vor …“ stimmt
MAX_EREIGNISSE = 300

BASE = "https://www.behoerden-serviceportal.de"
PROZESS = "m40191.wk"
START_DATEN = {"leistungId": "99036049069000", "p": "081110", "oeId": "L100022.OE.6043503"}
START_SEITE = (f"{BASE}/onlineantraege/onlineantrag?prozessKey={PROZESS}"
               "&oeId=L100022.OE.6043503&leistungId=99036049069000&p=081110")
ORTSKUERZEL = "S"
RESERVIEREN = "https://www.stuttgart.de/organigramm/leistungen/kraftfahrzeugkennzeichen-wunschkennzeichen-reservieren"

OHNE_WERT = {"TEXT", "H1", "H2", "H3", "HINTBOX", "IMAGE"}
MAX_TREFFER = 30  # das Portal liefert höchstens 30 Vorschläge pro Suche
BUCHSTABEN = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

log = logging.getLogger("kennzeichen")


class PortalFehler(Exception):
    pass


class Portal:
    """Spricht die JSON-Schnittstelle des Online-Antrags an, Schritt für Schritt wie die Webseite."""

    def __init__(self, pause):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.pause = pause
        self.antwort = None

    def _xsrf(self):
        return next((c.value for c in self.jar if c.name == "XSRF-TOKEN"), "")

    def _anfrage(self, methode, pfad, daten=None, json_antwort=True):
        time.sleep(self.pause * random.uniform(0.8, 1.4))
        kopf = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Kennzeichen-Waechter/1.0",
            "Accept": "application/json, text/plain, */*" if json_antwort else "text/html",
            "Accept-Language": "de",
        }
        if self._xsrf():
            kopf["X-XSRF-TOKEN"] = self._xsrf()
        rumpf = None
        if daten is not None:
            rumpf = json.dumps(daten).encode("utf-8")
            kopf["Content-Type"] = "application/json"
        url = pfad if pfad.startswith("http") else BASE + pfad
        anfrage = urllib.request.Request(url, data=rumpf, method=methode, headers=kopf)
        with self.opener.open(anfrage, timeout=45) as r:
            inhalt = r.read()
        if json_antwort and inhalt:
            return json.loads(inhalt)
        return None

    def oeffnen(self):
        self._anfrage("GET", START_SEITE, json_antwort=False)
        self._anfrage("GET", "/rest/api/identity/me")

    def _uebernehmen(self, antwort):
        if not antwort or "processFormDto" not in antwort:
            titel = (antwort or {}).get("title", "leere Antwort")
            raise PortalFehler(f"Portal antwortet nicht mit einem Formular: {titel}")
        self.antwort = antwort

    def starten(self):
        self._uebernehmen(self._anfrage("POST", f"/rest/api/prozesse/start/{PROZESS}", START_DATEN))

    @property
    def formular(self):
        return self.antwort["processFormDto"]["id"]

    def felder(self):
        for abschnitt in self.antwort["processFormDto"]["sections"]:
            for gruppe in abschnitt["fieldGroups"]:
                for instanz in gruppe.get("instances") or []:
                    for zeile in instanz["rows"]:
                        for feld in zeile["fields"]:
                            yield gruppe["id"], instanz["instanceIndex"], feld

    def feld(self, feld_id):
        return next((f for _, _, f in self.felder() if f["id"] == feld_id), None)

    def abschicken(self, werte, knopf):
        werte_neu = {}
        for gruppe, index, feld in self.felder():
            eintrag = {"fieldType": feld["type"]}
            if feld["type"] not in OHNE_WERT:
                eintrag["fieldValue"] = werte.get(feld["id"], feld.get("value"))
            werte_neu.setdefault(gruppe, {}).setdefault(str(index), {})[feld["id"]] = eintrag
        aufgabe = urllib.request.quote(self.antwort["taskId"], safe="")
        self._uebernehmen(self._anfrage("POST", f"/rest/api/prozesse/aufgaben/{aufgabe}/formular/abschicken", {
            "formFieldValues": werte_neu,
            "appliedReplacementsToken": self.antwort["processFormDto"].get("appliedReplacementsToken"),
            "pressedButton": knopf,
        }))

    def schritt(self, werte, knopf, erwartet):
        self.abschicken(werte, knopf)
        if erwartet not in self.formular:
            raise PortalFehler(f"Erwartet war Schritt {erwartet}, das Portal zeigt {self.formular}")

    def loeschen(self):
        """Bricht den Antrag serverseitig ab, damit keine halben Anträge liegen bleiben."""
        if not self.antwort:
            return
        try:
            instanz = urllib.request.quote(self.antwort["rootProcessInstanceId"], safe="")
            self._anfrage("DELETE", f"/rest/api/prozesse/instanzen/{instanz}")
        except Exception as e:  # Aufräumen darf den Lauf nicht scheitern lassen
            log.warning("Antrag konnte nicht gelöscht werden: %s", e)


class Suche:
    """Kennzeichensuche über einen Antrag. Das Portal beantwortet pro Antrag nur 30 Suchen
    (danach kommt stillschweigend „nichts gefunden“), deshalb wird rechtzeitig ein neuer
    Antrag begonnen."""

    MAX_PRO_ANTRAG = 25

    def __init__(self, cfg):
        self.cfg = cfg
        self.portal = Portal(cfg.get("pause_sekunden", 2))
        self.portal.oeffnen()
        self.anzahl = None
        self.antraege = 0

    def _neuer_antrag(self):
        p = self.cfg["person"]
        self.portal.loeschen()
        self.antraege += 1
        self.anzahl = 0
        self.portal.starten()
        self.portal.schritt({"ikfzArtDerAP": "nP"}, "weiter", "Personenangaben")
        self.portal.schritt({
            "antragstellerVorname": p["vorname"], "antragstellerNachname": p["nachname"],
            "antragstellerGeburtsdatum": p["geburtsdatum"], "antragstellerGeburtsort": p["geburtsort"],
            "antragstellerStrasse": p["strasse"], "antragstellerHausnummer": p["hausnummer"],
            "antragstellerHausnummernzusatz": p.get("hausnummernzusatz", ""),
            "antragstellerPlz": p["plz"], "antragstellerOrt": p["ort"], "ikfzEMail": p.get("email", ""),
        }, "weiter", "AngabenKennzeichen")
        self.portal.schritt({"ikfzFahrzeugart": self.cfg.get("fahrzeugart", "PKW"),
                             "ikfzEHKennzeichen": self.cfg.get("e_oder_h_kennzeichen", "nein"),
                             "ikfzSaison": "nein"}, "weiter", "KennzeichenSuchen")

    def __call__(self, muster):
        if self.antraege == 0 or self.anzahl >= self.MAX_PRO_ANTRAG:
            self._neuer_antrag()
        self.anzahl += 1
        self.portal.abschicken({"ikfzKennzeichen": muster, "ikfzKennzeichenAlternativen": "",
                                "ikfzZahlenSuchkritierien": "KEINE"}, "suchen")
        feld = self.portal.feld("ikfzKennzeichenAlternativen")
        return [w["value"] for w in feld["possibleValues"]] if feld else []

    def schliessen(self):
        self.portal.loeschen()


def muster_liste(cfg):
    """Alle Suchen, die zusammen jedes S-X <zahl> und S-XY <zahl> abdecken.

    Eine Sammelsuche (S-??-zahl) liefert nur 30 beliebige Treffer. Pro Anfangsbuchstabe
    gibt es höchstens 26 Kombinationen, diese Suchen sind also immer vollständig."""
    return [(zahl, m) for zahl in cfg["zahlen"] for m in ["?"] + [f"{b}?" for b in BUCHSTABEN]]


def schluessel(zahl, muster):
    return f"{zahl}|{muster}"


def passt(kennzeichen, zahl, muster):
    _, buchstaben, z = kennzeichen.split("-")
    return z == zahl and len(buchstaben) == len(muster) and (muster == "?" or buchstaben[0] == muster[0])


def anzeigen(kennzeichen):
    """S-AB-911 -> S-AB 911"""
    o, b, z = kennzeichen.split("-")
    return f"{o}-{b} {z}"


def benachrichtigen(cfg, titel, text):
    log.info("Benachrichtigung: %s – %s", titel, text)
    if os.name == "nt":
        windows_meldung(titel, text)
    handy_push(cfg, titel, text)
    email_senden(cfg, titel, text)


def windows_meldung(titel, text):
    skript = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$x = $t.GetElementsByTagName('text');"
        "$x[0].AppendChild($t.CreateTextNode($env:KZ_TITEL)) > $null;"
        "$x[1].AppendChild($t.CreateTextNode($env:KZ_TEXT)) > $null;"
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", skript],
                       env={**os.environ, "KZ_TITEL": titel, "KZ_TEXT": text},
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60, check=False)
    except Exception as e:
        log.warning("Windows-Benachrichtigung fehlgeschlagen: %s", e)


def handy_push(cfg, titel, text):
    """Push über ntfy.sh (kostenlos, kein Konto nötig)."""
    thema = cfg.get("ntfy_thema", "").strip()
    if not thema:
        return
    nachricht = {"topic": thema, "title": titel, "message": text, "tags": ["red_car"],
                 "actions": [{"action": "view", "label": "Jetzt reservieren", "url": RESERVIEREN}]}
    if cfg.get("website"):
        nachricht["click"] = cfg["website"]
    try:
        anfrage = urllib.request.Request(
            "https://ntfy.sh/", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps(nachricht).encode("utf-8"))
        urllib.request.urlopen(anfrage, timeout=30).close()
    except Exception as e:
        log.warning("Handy-Benachrichtigung (ntfy) fehlgeschlagen: %s", e)


def email_senden(cfg, titel, text):
    """E-Mail über das eigene Gmail-Konto (braucht ein Google-App-Passwort)."""
    adresse = cfg.get("email", "").strip()
    passwort = cfg.get("gmail_app_passwort", "").replace(" ", "")
    if not (adresse and passwort):
        return
    mail = EmailMessage()
    mail["Subject"] = titel
    mail["From"] = f"Kennzeichen-Wächter <{adresse}>"
    mail["To"] = adresse
    mail.set_content(f"{text}\n\nAlle freien Kennzeichen: {cfg.get('website', '')}\n"
                     f"Reservieren bei der Stadt Stuttgart: {RESERVIEREN}\n")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(adresse, passwort)
            smtp.send_message(mail)
    except Exception as e:
        log.warning("E-Mail fehlgeschlagen: %s", e)


def liste_schreiben(cfg, zustand):
    zeilen = [f"Freie Wunschkennzeichen Stuttgart – Stand {zustand['stand']}",
              "(jede Buchstabengruppe wird etwa stündlich neu geprüft)", ""]
    for zahl in cfg["zahlen"]:
        passend = sorted(k for k in zustand["kennzeichen"] if k.endswith(f"-{zahl}"))
        zeilen.append(f"--- {zahl}: {len(passend)} frei ---")
        for k in passend:
            zeilen.append(f"{anzeigen(k):<12} bekannt seit {zustand['kennzeichen'][k]['frei_seit']}")
        zeilen.append("")
    LISTE.write_text("\n".join(zeilen), encoding="utf-8")


def iso(datum_de):
    """'20.10.2026 18:34' -> '2026-10-20T18:34'"""
    return datetime.strptime(datum_de, "%d.%m.%Y %H:%M").isoformat(timespec="minutes")


def git(*args):
    return subprocess.run(["git", "-C", str(WEBSITE), *args], capture_output=True, text=True, timeout=120,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def website_aktualisieren(zustand):
    """Schreibt website/daten.json und lädt sie hoch – bei Änderungen sofort, sonst als Lebenszeichen alle 2 Std."""
    if not (WEBSITE / ".git").exists():
        return
    daten = {
        "stand": zustand.get("letzter_erfolg"),
        "kennzeichen": [{"k": k, "seit": iso(v["frei_seit"])} for k, v in sorted(zustand["kennzeichen"].items())],
        "ereignisse": zustand.get("ereignisse", []),
    }
    if CLOUD:  # der Workflow committet und pusht selbst
        WEBSITE_DATEN.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return
    ohne_stand = lambda d: {k: v for k, v in d.items() if k != "stand"}
    alt = json.loads(WEBSITE_DATEN.read_text(encoding="utf-8")) if WEBSITE_DATEN.exists() else {}
    letzter = zustand.get("website_hochgeladen")
    faellig = (ohne_stand(alt) != ohne_stand(daten) or zustand.get("website_ausstehend") or not letzter
               or (datetime.now() - datetime.fromisoformat(letzter)).total_seconds() >= WEBSITE_HERZSCHLAG)
    if not faellig:
        return
    WEBSITE_DATEN.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        git("add", "daten.json")
        if git("diff", "--cached", "--quiet").returncode != 0:
            git("commit", "-q", "-m", f"Daten {zustand['stand']}")
        push = git("push", "-q")
        if push.returncode != 0:
            raise RuntimeError(push.stderr.strip() or "git push fehlgeschlagen")
        zustand["website_hochgeladen"] = datetime.now().isoformat(timespec="seconds")
        zustand.pop("website_ausstehend", None)
    except Exception as e:
        zustand["website_ausstehend"] = True
        log.warning("Website konnte nicht aktualisiert werden: %s", e)
    zustand_speichern(zustand)


def zustand_laden(cfg):
    jetzt = datetime.now().isoformat(timespec="seconds")
    if not ZUSTAND.exists():
        return {"stand": "", "kennzeichen": {}, "geprueft": {}, "letzter_erfolg": jetzt}
    zustand = json.loads(ZUSTAND.read_text(encoding="utf-8"))
    if "geprueft" not in zustand:  # Liste stammt aus einem vollständigen Lauf vor der Umstellung
        zustand["geprueft"] = {schluessel(*m): "2000-01-01T00:00" for m in muster_liste(cfg)}
        zustand["letzter_erfolg"] = jetzt
    return zustand


def zustand_speichern(zustand):
    ZUSTAND.write_text(json.dumps(zustand, indent=1, ensure_ascii=False), encoding="utf-8")


def lauf(cfg):
    """Prüft die am längsten nicht geprüften Suchmuster, höchstens `suchen_pro_lauf` Stück.

    Das Portal reagiert auf viele Suchen in kurzer Zeit mit Fehlern. Deshalb wird die Arbeit
    über mehrere Läufe verteilt und bei einem Fehler sofort aufgehört, statt nachzubohren."""
    zustand = zustand_laden(cfg)

    # GitHub lässt geplante Läufe oft ausfallen; deshalb wird alle 5 Min. angeklopft und
    # hier übersprungen, wenn der letzte echte Lauf noch nicht lange genug her ist.
    mindestabstand = float(os.environ.get("KZ_MINDESTABSTAND_MIN") or 0)
    if mindestabstand and zustand.get("letzter_lauf"):
        vergangen = (datetime.now() - datetime.fromisoformat(zustand["letzter_lauf"])).total_seconds() / 60
        if vergangen < mindestabstand:
            log.info("Letzter Lauf vor %.0f Min. – diesmal übersprungen", vergangen)
            return
    zustand["letzter_lauf"] = datetime.now().isoformat(timespec="seconds")

    reihenfolge = sorted(muster_liste(cfg), key=lambda m: zustand["geprueft"].get(schluessel(*m), ""))
    budget = cfg.get("suchen_pro_lauf", Suche.MAX_PRO_ANTRAG)
    neu, weg, erledigt = [], [], 0
    suche = None
    try:
        suche = Suche(cfg)
        for zahl, muster in reihenfolge[:budget]:
            treffer = set(suche(f"{ORTSKUERZEL}-{muster}-{zahl}"))
            if len(treffer) >= MAX_TREFFER:
                raise PortalFehler(f"Suche {muster} {zahl} ist abgeschnitten ({len(treffer)} Treffer)")
            vorher = {k for k in zustand["kennzeichen"] if passt(k, zahl, muster)}
            if not treffer and len(vorher) > 3:
                raise PortalFehler(f"Suche {muster} {zahl}: plötzlich nichts frei (vorher {len(vorher)}) – unplausibel")
            jetzt = datetime.now().strftime("%d.%m.%Y %H:%M")
            if schluessel(zahl, muster) in zustand["geprueft"]:  # beim allerersten Prüfen ist nichts „neu“
                neu += sorted(treffer - vorher)
            weg += sorted(vorher - treffer)
            for k in vorher - treffer:
                del zustand["kennzeichen"][k]
            for k in treffer - vorher:
                zustand["kennzeichen"][k] = {"frei_seit": jetzt}
            zustand["geprueft"][schluessel(zahl, muster)] = datetime.now().isoformat(timespec="seconds")
            zustand["letzter_erfolg"] = datetime.now().isoformat(timespec="seconds")
            zustand.pop("gewarnt", None)
            zustand_speichern(zustand)
            erledigt += 1
    except (PortalFehler, urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
        nr = f"Suche {suche.anzahl} im {suche.antraege}. Antrag" if suche and suche.antraege else "beim Öffnen"
        log.warning("Lauf nach %d von %d Suchen abgebrochen (%s): %s", erledigt, budget, nr, e)
    finally:
        if suche:
            suche.schliessen()

    zustand["stand"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    zeit = datetime.now().isoformat(timespec="minutes")
    ereignisse = zustand.setdefault("ereignisse", [])
    ereignisse[:0] = ([{"t": zeit, "k": k, "art": "neu"} for k in neu]
                      + [{"t": zeit, "k": k, "art": "weg"} for k in weg])
    del ereignisse[MAX_EREIGNISSE:]
    zustand_speichern(zustand)
    if not CLOUD:
        liste_schreiben(cfg, zustand)
    website_aktualisieren(zustand)
    frei = zustand["kennzeichen"]
    log.info("%d Suchen ok – %d frei (%s), neu: %s, nicht mehr frei: %s", erledigt, len(frei),
             ", ".join(f"{z}: {sum(k.endswith('-' + z) for k in frei)}" for z in cfg["zahlen"]),
             ", ".join(map(anzeigen, neu)) or "–", ", ".join(map(anzeigen, weg)) or "–")

    if neu:
        benachrichtigen(cfg, f"{len(neu)} Wunschkennzeichen neu frei!", ", ".join(map(anzeigen, neu)))

    letzter = datetime.fromisoformat(zustand.get("letzter_erfolg", datetime.now().isoformat()))
    # 12 Std., weil das Portal nachts Wartungspausen macht („Ups, wir sind aktuell leider nicht erreichbar“)
    if (datetime.now() - letzter).total_seconds() > 12 * 3600 and not zustand.get("gewarnt"):
        benachrichtigen(cfg, "Kennzeichen-Wächter hat Probleme",
                        f"Seit {letzter:%d.%m. %H:%M} keine erfolgreiche Abfrage. Details im Protokoll"
                        + (" bei GitHub Actions." if CLOUD else " verlauf.log."))
        zustand["gewarnt"] = True
        zustand_speichern(zustand)


def main():
    ausgaben = [logging.StreamHandler(sys.stdout)]
    if not CLOUD:
        ausgaben.append(logging.FileHandler(LOG, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=ausgaben)
    if os.environ.get("KZ_CONFIG"):
        cfg = json.loads(os.environ["KZ_CONFIG"])
    else:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))  # -sig: Windows-Editor speichert oft mit BOM
    if os.environ.get("GMAIL_APP_PASSWORT"):
        cfg["gmail_app_passwort"] = os.environ["GMAIL_APP_PASSWORT"]
    if "--test" in sys.argv:
        benachrichtigen(cfg, "Kennzeichen-Wächter Test", "Benachrichtigungen funktionieren.")
        return
    try:
        lauf(cfg)
    except Exception:
        log.exception("Prüflauf fehlgeschlagen")
        sys.exit(1)


if __name__ == "__main__":
    main()
