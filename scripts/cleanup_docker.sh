#!/usr/bin/env bash
# Script para limpiar espacio en Docker (ejecutar en EC2)

set -e

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Limpiando Docker..."

# Eliminar contenedores detenidos
docker container prune -f

# Eliminar imágenes sin usar (excepto la actual)
docker image prune -af

# Eliminar volúmenes sin usar
docker volume prune -f

# Limpieza completa del sistema (cuidado: elimina TODO lo no usado)
# docker system prune -af --volumes

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Limpieza completada"
echo "Espacio disponible:"
df -h /

