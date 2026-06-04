#!/bin/bash
# Codzienny runner BZP Analyst (wywoływany przez launchd)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_FILE="$PROJECT_DIR/logs/bzp_analyst.log"

mkdir -p "$PROJECT_DIR/logs"

echo "=== BZP Analyst START: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"

cd "$PROJECT_DIR"

# Użyj venv jeśli istnieje, inaczej system python3
if [ -d "$PROJECT_DIR/.venv" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
elif [ -d "$HOME/bzp-analyst/.venv" ]; then
    PYTHON="$HOME/bzp-analyst/.venv/bin/python3"
else
    PYTHON="/usr/bin/python3"
fi

$PYTHON "$PROJECT_DIR/main.py" \
    --config "$PROJECT_DIR/config.yaml" \
    >> "$LOG_FILE" 2>&1

echo "=== BZP Analyst STOP: $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"
