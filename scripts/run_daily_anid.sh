#!/usr/bin/env bash
set -e

# Ruta base del proyecto
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

# Crear directorio de logs si no existe
mkdir -p "$BASE_DIR/data/logs"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ejecutando scraping + predicciones ANID..."
python -m scripts.daily_anid >> "$BASE_DIR/data/logs/daily_anid.log" 2>&1

