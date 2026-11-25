# Freie Wunschkennzeichen Stuttgart

Übersicht aller freien Wunschkennzeichen **S-… 911** und **S-… 992** der Zulassungsstelle Stuttgart:
**https://pascal787.github.io/kennzeichen/**

`daten.json` wird automatisch etwa alle 30 Minuten aktualisiert. Die Daten stammen aus der öffentlichen
Wunschkennzeichen-Suche des Behörden-Serviceportals, ohne Gewähr. Reserviert wird über die
[Stadt Stuttgart](https://www.stuttgart.de/organigramm/leistungen/kraftfahrzeugkennzeichen-wunschkennzeichen-reservieren).

## Wächter

Der Wächter (`waechter/kennzeichen_bot.py`) läuft auf Pascals MacBook Air alle 30 Minuten (launchd
`de.pascal.kennzeichen`, Protokoll `~/Library/Logs/kennzeichen.log`) und lädt `daten.json` selbst hoch.
Einrichten: `waechter/air-einrichten.sh` (fragt Einstellungen und Gmail-App-Passwort in einem Fenster ab
und legt sie in den Schlüsselbund). Jeder Lauf macht bis zu 25 Suchen
(`KZ_SUCHEN_PRO_LAUF` in `air.sh`). Vom 21. bis 25.11.2026 war die Suche im Portal gestört.
Der Workflow bei GitHub ist abgeschaltet und bleibt als Reserve.
