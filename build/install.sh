#!/bin/bash
# Instalator BZP Analyst dla klienta.
# Użycie: bash install.sh

set -e

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

echo "=== Instalacja BZP Analyst ==="
echo "Katalog: $INSTALL_DIR"
echo ""

# Sprawdź Python 3.10+
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
  echo "❌ Wymagany Python 3.10+. Zainstaluj z https://www.python.org/downloads/"
  exit 1
fi

echo "✅ Python $PY_VERSION"

# Utwórz venv
if [ ! -d "$INSTALL_DIR/.venv" ]; then
  echo "Tworzę środowisko wirtualne..."
  "$PYTHON" -m venv "$INSTALL_DIR/.venv"
fi

# Zainstaluj zależności
echo "Instaluję zależności..."
"$INSTALL_DIR/.venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"

# Utwórz alias 'bzp' w /usr/local/bin
BIN_SCRIPT="/usr/local/bin/bzp"
echo "Tworzę skrót 'bzp' w /usr/local/bin..."

sudo tee "$BIN_SCRIPT" > /dev/null << EOF
#!/bin/bash
"$INSTALL_DIR/.venv/bin/python3" "$INSTALL_DIR/launcher.py" "\$@"
EOF
sudo chmod +x "$BIN_SCRIPT"

echo ""
echo "✅ Instalacja zakończona!"
echo ""
echo "Uruchom BZP Analyst wpisując w terminalu:"
echo "  bzp"
echo ""
echo "Lub bezpośrednio:"
echo "  $INSTALL_DIR/.venv/bin/python3 $INSTALL_DIR/launcher.py"
