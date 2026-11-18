#!/bin/bash
# Instala o actualiza «Panel Inventario» en un Mac con Apple Silicon desde las releases de GitHub.
#
#   bash instalar-mac.sh            # última versión publicada
#   bash instalar-mac.sh v0.1.0     # una versión concreta
#
# La app no está firmada con una cuenta de Apple Developer: al descargarla con curl (y no con
# el navegador) no recibe la marca de cuarentena, así que macOS la abre sin bloquearla.
#
# Copyright (C) 2026 Thothalpha · Licencia AGPL-3.0
# Sin "set -u": el bash 3.2 de macOS da falsos «unbound variable». Las variables se validan a mano.
set -eo pipefail

REPO="Thothalpha/panel-inventario-app"
TAG=""
TMP=""
APP="Panel Inventario.app"
DEST="/Applications"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "Este instalador es para Mac con Apple Silicon (M1 o posterior)." >&2
  exit 1
fi

if [ $# -gt 0 ]; then TAG="$1"; fi
if [ -z "$TAG" ]; then
  TAG=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi
[ -n "$TAG" ] || { echo "No se encontró ninguna versión publicada." >&2; exit 1; }
V="${TAG#v}"
BASE="https://github.com/$REPO/releases/download/$TAG"
ZIP="PanelInventario-$V-arm64.zip"

TMP=$(mktemp -d)
trap '[ -n "$TMP" ] && rm -rf "$TMP"' EXIT

echo "→ Descargando Panel Inventario $TAG…"
curl -fL --progress-bar -o "$TMP/$ZIP" "$BASE/$ZIP"
curl -fsSL -o "$TMP/sums" "$BASE/PanelInventario-$V-arm64.sha256"

echo "→ Comprobando la integridad del archivo…"
EXPECTED=$(grep " $ZIP\$" "$TMP/sums" | awk '{print $1}')
ACTUAL=$(shasum -a 256 "$TMP/$ZIP" | awk '{print $1}')
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "La suma SHA-256 no coincide: descarga corrupta o alterada. No se instala nada." >&2
  exit 1
fi

if pgrep -xq "Panel Inventario"; then
  echo "→ Cerrando la versión abierta…"
  osascript -e 'quit app "Panel Inventario"' >/dev/null 2>&1 || true
  sleep 2
fi

echo "→ Instalando en $DEST…"
ditto -x -k "$TMP/$ZIP" "$TMP/x"
rm -rf "$DEST/$APP"
mv "$TMP/x/$APP" "$DEST/"
xattr -dr com.apple.quarantine "$DEST/$APP" 2>/dev/null || true

echo "✓ Panel Inventario $TAG instalado. Abriendo…"
open "$DEST/$APP"
