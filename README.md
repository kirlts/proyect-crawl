# Buscador de Oportunidades de Financiamiento

Sistema de extracción, almacenamiento y predicción de concursos de financiamiento para investigación académica en Chile.

## Características

- **Scraping multi-sitio**: Soporte para ANID, Centro Estudios MINEDUC, CNA y DFI MINEDUC
- **Extracción con LLM**: Usa Google Gemini para extraer información estructurada de páginas web
- **Predicción de fechas**: Estima fechas de próxima apertura basándose en versiones históricas
- **Interfaz web**: Aplicación Streamlit con 5 pestañas organizadas
- **Persistencia**: Historial por sitio, cache de páginas, predicciones
- **Despliegue**: Dockerizado y listo para AWS EC2

## Requisitos

- Python 3.12+
- API Key(s) de Google Gemini
- Docker (opcional, para despliegue)

## Instalación

### Desarrollo Local

1. Clonar el repositorio:
```bash
git clone <repo-url>
cd proyect-crawl
```

2. Crear entorno virtual:
```bash
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
```

3. Instalar dependencias:
```bash
pip install -r requirements.txt
```

4. Instalar navegadores de Playwright:
```bash
python -m playwright install-deps chromium
python -m playwright install chromium
```

5. Configurar API keys:
```bash
# Crear archivo data/.api_keys.json
{
  "gemini": {
    "keys": [
      {"key": "TU_API_KEY_AQUI", "name": "Key 1", "is_active": true}
    ],
    "current_index": 0
  }
}
```

6. Ejecutar aplicación:
```bash
streamlit run main.py
```

### Docker

1. Construir imagen:
```bash
docker build -t proyect-crawl .
```

2. Ejecutar con Docker Compose:
```bash
docker-compose up
```

3. Acceder en `http://localhost:8501`

## Configuración

### Variables de Entorno

Crear archivo `.env`:
```bash
API_KEYS_PATH=data/.api_keys.json
DATA_DIR=data
PORT=8501
```

### API Keys

El sistema soporta múltiples API keys con rotación automática. Agregar keys en `data/.api_keys.json`:

```json
{
  "gemini": {
    "keys": [
      {"key": "AIza...", "name": "Key 1", "is_active": true},
      {"key": "AIza...", "name": "Key 2", "is_active": true}
    ],
    "current_index": 0
  }
}
```

### Modelos LLM

Modelos recomendados para Free Tier:
- `gemini-2.5-flash-lite` (recomendado)
- `gemini-2.5-flash-lite-preview-09-2025`

Seleccionar modelo desde la pestaña "Scraping y Configuración" en la UI.

## Uso

### Interfaz Web

La aplicación tiene 5 pestañas:

1. **Visualización**: Lista unificada de todos los concursos con filtros avanzados
2. **Explorar Concursos**: Ver y gestionar concursos por sitio
3. **Predicciones**: Generar y ver predicciones de fechas de apertura
4. **Scraping y Configuración**: Ejecutar scraping y configurar API keys/modelos
5. **Concursos Manuales**: Agregar concursos que no provienen de scraping

### Scraping

1. Ir a pestaña "Scraping y Configuración"
2. Seleccionar sitio a scrapear
3. Configurar número de páginas (máximo)
4. Presionar "Iniciar Scraping"
5. El sistema procesará las URLs y extraerá concursos

### Predicciones

1. Ir a pestaña "Predicciones"
2. Seleccionar sitio
3. Aplicar filtros (opcional)
4. Presionar "Generar Predicciones"
5. Ver resultados en la tabla

### Concursos Manuales

1. Ir a pestaña "Concursos Manuales"
2. Completar formulario con datos del concurso
3. Validar que fecha de cierre > fecha de apertura
4. Presionar "Agregar Concurso"
5. El sistema asignará predicción automática (+1 año)

## Automatización

### Cron Diario (EC2)

El sistema incluye un script para scraping diario de ANID:

```bash
# Agregar a crontab
0 6 * * * /home/ubuntu/proyect-crawl/scripts/run_daily_anid.sh
```

El script:
1. Hace scraping de ANID (máximo 2 páginas)
2. Genera predicciones para nuevos concursos
3. Registra logs en `data/logs/daily_anid.log`

## Estructura del Proyecto

```
proyect-crawl/
├── main.py                      # Aplicación Streamlit
├── config.py                    # Configuración (wrapper)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
│
├── config/
│   ├── global_config.py         # Parámetros globales
│   └── sites.py                 # Configuración por sitio
│
├── models/
│   ├── concurso.py              # Modelo Concurso
│   └── prediccion.py            # Modelos de predicción
│
├── services/
│   ├── extraction_service.py   # Orquestación scraping + extracción
│   └── prediction_service.py    # Generación de predicciones
│
├── crawler/
│   ├── scraper.py               # WebScraper principal
│   ├── strategies/              # Estrategias por sitio
│   └── pagination/              # Lógica de paginación
│
├── llm/
│   ├── gemini_client.py         # Cliente REST Gemini
│   ├── prompts.py               # Templates de prompts
│   ├── predictor.py             # Lógica de predicción
│   └── extractors/
│       └── llm_extractor.py     # Extracción con LLM
│
├── utils/
│   ├── api_key_manager.py       # Rotación de API keys
│   ├── history_manager.py       # Gestión de historial
│   ├── file_manager.py          # Persistencia
│   ├── lock_manager.py          # Locks para concurrencia
│   ├── scraping_state.py        # Estado persistente
│   └── extractors/              # Extractores específicos
│
├── scripts/
│   ├── daily_anid.py            # Script scraping diario
│   └── run_daily_anid.sh        # Wrapper para cron
│
└── data/                        # Generado en runtime
    ├── history/                 # Historial por sitio
    ├── predictions/             # Predicciones
    ├── raw_pages/               # Cache HTML/Markdown
    └── debug/                   # Logs de ejecución
```

## Despliegue en AWS

### Requisitos

- Instancia EC2 (t3.medium o superior)
- Security group con puerto 8501 abierto
- IP elástica asignada
- Docker y docker-compose instalados

### Pasos

1. Configurar secrets en GitHub:
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_REGION`
   - `ECR_REPOSITORY`
   - `SSH_HOST` (IP de EC2)
   - `SSH_USER` (usualmente `ubuntu`)
   - `SSH_KEY` (clave privada PEM)
   - `SSH_PORT` (usualmente `22`)

2. Push a `main` o `master` activa el workflow de GitHub Actions

3. El workflow:
   - Construye imagen Docker
   - Push a ECR
   - SSH a EC2
   - Pull y reinicio de contenedor

### Configuración Manual en EC2

```bash
# Conectar por SSH
ssh -i key.pem ubuntu@<IP-EC2>

# Crear directorio
mkdir -p ~/proyect-crawl/data

# Crear .env
cat > ~/proyect-crawl/.env <<'EOF'
API_KEYS_PATH=data/.api_keys.json
DATA_DIR=data
PORT=8501
EOF

# Copiar API keys
scp -i key.pem data/.api_keys.json ubuntu@<IP-EC2>:~/proyect-crawl/data/.api_keys.json
```

## Solución de Problemas

### Error: "BrowserType.launch: Executable doesn't exist"

```bash
python -m playwright install-deps chromium
python -m playwright install chromium
```

### Error: "429 Quota Exceeded"

- Verificar que las API keys sean válidas
- Usar modelo compatible con Free Tier (`gemini-2.5-flash-lite`)
- El sistema rota automáticamente a la siguiente key disponible

### Error: "Operación concurrente en curso"

- Esperar a que termine el scraping/predicción actual
- Si persiste, limpiar locks en `data/locks/`

### Error: "no space left on device" (EC2)

```bash
# Limpiar imágenes Docker no usadas
docker system prune -af --volumes
```

### El botón "Cancelar Scraping" no funciona

- Verificar que el estado persistente esté activo
- Limpiar estado en `data/scraping_state/` si está corrupto

## Documentación

- **Arquitectura**: Ver `docs/ARQUITECTURA.md` para detalles técnicos completos
- **Agregar nuevo sitio**: Ver sección "Agregar un Nuevo Sitio" en `docs/ARQUITECTURA.md`

## Licencia

Proyecto de práctica profesional - uso interno.
