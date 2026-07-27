#!/bin/bash
# Buduje dystrybucję BZP Analyst gotową do sprzedaży.
# Użycie: bash build/build.sh [wersja]
# Przykład: bash build/build.sh 1.0.0

set -e

VERSION="${1:-1.0.0}"
DIST_NAME="bzp-analyst-v${VERSION}-mac"
DIST_DIR="/tmp/${DIST_NAME}"
ZIP_OUT="$(pwd)/dist/${DIST_NAME}.zip"

echo "=== BZP Analyst Build v${VERSION} ==="

# Czyść poprzedni build
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR" "$(pwd)/dist"

# Kopiuj źródła (bez venv, git, cache, danych wyjściowych)
rsync -a \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='data/*.json' \
  --exclude='reports/output/*' \
  --exclude='dist/' \
  --exclude='.env' \
  --exclude='*.key' \
  . "$DIST_DIR/"

# Dodaj plik wersji
echo "$VERSION" > "$DIST_DIR/VERSION"

# Upewnij się że install.sh ma uprawnienia
chmod +x "$DIST_DIR/build/install.sh"

# Spakuj ZIP
cd /tmp
zip -r "$ZIP_OUT" "$DIST_NAME/" -x "*.DS_Store"
cd - > /dev/null

echo ""
echo "✅ Gotowe: $ZIP_OUT"
echo "   Rozmiar: $(du -sh "$ZIP_OUT" | cut -f1)"
echo ""
echo "Następne kroki:"
echo "  1. Prześlij $ZIP_OUT na Gumroad jako plik do pobrania"
echo "  2. Po zakupie wyślij klucz wygenerowany przez: python3 license/generator.py --email BUYER@EMAIL.COM --plan pro --days 365"
