#!/bin/bash
# Instalacja harmonogramu launchd dla BZP Analyst
# Uruchamia analizę codziennie o 7:00

set -e
PLIST="pl.doomdoja.bzp-analyst.plist"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$PLIST"
DEST="$HOME/Library/LaunchAgents/$PLIST"

if [ ! -f "$SRC" ]; then
  echo "ERROR: Nie znaleziono $SRC"
  exit 1
fi

cp "$SRC" "$DEST"
launchctl load "$DEST"
echo "✅ Zainstalowano. BZP Analyst uruchomi się jutro o 7:00."
echo "   Odinstaluj: launchctl unload '$DEST' && rm '$DEST'"
echo "   Uruchom teraz: launchctl start pl.doomdoja.bzp-analyst"
echo "   Logi: tail -f ~/bzp-analyst/logs/bzp_analyst.log"
