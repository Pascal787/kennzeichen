#!/bin/bash
# Richtet den Wächter auf diesem Mac ein: Einstellungen in den Schlüsselbund, Zustand übernehmen,
# launchd-Auftrag alle 30 Minuten. Mehrfach aufrufen schadet nicht; vorhandene Einträge bleiben,
# außer man gibt im Fenster einen neuen Wert ein.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATEN="$HOME/Library/Application Support/kennzeichen"
NAME=de.pascal.kennzeichen
PLIST="$HOME/Library/LaunchAgents/$NAME.plist"

frage() { osascript -e "text returned of (display dialog \"$1\" default answer \"\" with hidden answer)" 2>/dev/null || true; }
k() { security find-generic-password -a pascal -s "kennzeichen-$1" -w >/dev/null 2>&1; }
setzen() { security add-generic-password -U -a pascal -s "kennzeichen-$1" -T /usr/bin/security -w "$2"; }

if ! k config; then
  wert="$(frage "Kennzeichen-Wächter: Inhalt von KZ_CONFIG (JSON in einer Zeile, wie im GitHub-Secret)")"
  [ -n "$wert" ] || { echo "Keine Einstellungen eingegeben, Abbruch"; exit 1; }
  printf '%s' "$wert" | /usr/bin/python3 -I -c "import json,sys; c=json.load(sys.stdin); c['person']; c['zahlen']" \
    || { echo "Das ist kein gültiges KZ_CONFIG (JSON mit person und zahlen), Abbruch"; exit 1; }
  setzen config "$wert"
fi
if ! k gmail; then
  wert="$(frage "Kennzeichen-Wächter: Gmail-App-Passwort (leer lassen, wenn keine E-Mails)")"
  [ -z "$wert" ] || setzen gmail "$wert"
fi
unset wert

mkdir -p "$DATEN"
[ -f "$DATEN/zustand.json" ] || cp "$REPO/waechter/zustand.json" "$DATEN/zustand.json"
chmod +x "$REPO/waechter/air.sh"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$NAME</string>
  <key>ProgramArguments</key><array><string>$REPO/waechter/air.sh</string></array>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/kennzeichen.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/kennzeichen.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$NAME" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Eingerichtet. Protokoll: ~/Library/Logs/kennzeichen.log"
