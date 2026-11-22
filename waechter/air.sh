#!/bin/bash
# Ein Prüflauf auf dem Mac (launchd, alle 30 Minuten). Einstellungen und Gmail-Passwort kommen aus dem
# Schlüsselbund. Seit 21.11.2026 lässt das Portal nur noch etwa 3 Suchen kurz hintereinander zu, danach
# kommt die Fehlerseite („Ups, wir sind aktuell leider nicht erreichbar…“).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATEN="$HOME/Library/Application Support/kennzeichen"
k() { security find-generic-password -a pascal -s "kennzeichen-$1" -w; }

git -C "$REPO" pull -q --rebase || echo "git pull fehlgeschlagen, prüfe mit altem Stand"
KZ_CONFIG="$(k config)" GMAIL_APP_PASSWORT="$(k gmail 2>/dev/null || true)" \
  KZ_ZUSTAND="$DATEN/zustand.json" KZ_WEBSITE="$REPO" KZ_SUCHEN_PRO_LAUF=3 \
  exec /usr/bin/python3 -I "$REPO/waechter/kennzeichen_bot.py" "$@"
