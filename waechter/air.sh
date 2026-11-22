#!/bin/bash
# Ein Prüflauf auf dem Mac (launchd, alle 30 Minuten). Das Behördenportal antwortet GitHub seit
# 21.11.2026 nicht mehr, dem Mac schon. Einstellungen und Gmail-Passwort kommen aus dem Schlüsselbund.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATEN="$HOME/Library/Application Support/kennzeichen"
k() { security find-generic-password -a pascal -s "kennzeichen-$1" -w; }

git -C "$REPO" pull -q --rebase || echo "git pull fehlgeschlagen, prüfe mit altem Stand"
KZ_CONFIG="$(k config)" GMAIL_APP_PASSWORT="$(k gmail 2>/dev/null || true)" \
  KZ_ZUSTAND="$DATEN/zustand.json" KZ_WEBSITE="$REPO" \
  exec /usr/bin/python3 -I "$REPO/waechter/kennzeichen_bot.py" "$@"
