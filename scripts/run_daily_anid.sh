#!/usr/bin/env bash
set -e

# Script para ejecutar scraping diario de ANID dentro del contenedor Docker
# Este script debe ejecutarse dentro del contenedor: docker exec proyect-crawl /app/scripts/run_daily_anid.sh

# Ruta base del proyecto (dentro del contenedor)
BASE_DIR="/app"
cd "$BASE_DIR"

# Crear directorio de logs si no existe
mkdir -p "$BASE_DIR/data/logs"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ejecutando scraping + predicciones ANID..."
python -m scripts.daily_anid >> "$BASE_DIR/data/logs/daily_anid.log" 2>&1

