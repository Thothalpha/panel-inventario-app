#!/usr/bin/env bash
# Arranca el panel en http://127.0.0.1:8765 (HOST=0.0.0.0 para abrirlo a la red local)
cd "$(dirname "$0")"
exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8765}"
