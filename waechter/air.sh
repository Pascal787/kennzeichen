#!/bin/bash
# Ein Prüflauf auf dem Mac (launchd, alle 30 Minuten). Einstellungen und Gmail-Passwort kommen aus dem
# Schlüsselbund. 25 Suchen pro Lauf (ein Antrag). Vom 21. bis 25.11.2026 war die Portal-Suche gestört
# (Zeitüberschreitung, später 503); das war keine Sperre gegen viele Suchen.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATEN="$HOME/Library/Application Support/kennzeichen"
k() { security find-generic-password -a pascal -s "kennzeichen-$1" -w; }

git -C "$REPO" pull -q --rebase || echo "git pull fehlgeschlagen, prüfe mit altem Stand"
KZ_CONFIG="$(k config)" GMAIL_APP_PASSWORT="$(k gmail 2>/dev/null || true)" \
  KZ_ZUSTAND="$DATEN/zustand.json" KZ_WEBSITE="$REPO" KZ_SUCHEN_PRO_LAUF=25 \
  exec /usr/bin/python3 -I "$REPO/waechter/kennzeichen_bot.py" "$@"
