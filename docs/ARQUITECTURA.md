# Arquitectura del Sistema

Sistema de extracción, almacenamiento y predicción de concursos de financiamiento para investigación académica en Chile.

## Stack Tecnológico

| Componente | Tecnología | Función |
|------------|------------|---------|
| Lenguaje | Python 3.12 | Base del sistema |
| UI | Streamlit | Aplicación web |
| Scraping | Crawl4AI + Playwright | Obtención de contenido web |
| LLM | Google Gemini API | Extracción estructurada y predicciones |
| Validación | Pydantic | Modelos de datos tipados |
| Contenedorización | Docker | Empaquetado y despliegue |
| Infraestructura | AWS EC2 + ECR | Alojamiento en producción |
| CI/CD | GitHub Actions | Build y deploy automático |

---

## Estructura de Archivos

```
proyect-crawl/
├── main.py                      # Aplicación Streamlit (UI)
├── config.py                    # Wrapper de configuración
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
│
├── config/
│   ├── global_config.py         # Parámetros globales (timeouts, modelos, rutas)
│   └── sites.py                 # Configuración por sitio (URLs, dominios, features)
│
├── models/
│   ├── concurso.py              # Modelo Concurso
│   └── prediccion.py            # Modelos de predicción
│
├── services/
│   ├── extraction_service.py    # Orquestación de scraping + extracción
│   └── prediction_service.py    # Generación de predicciones
│
├── crawler/
│   ├── scraper.py               # WebScraper principal
│   ├── markdown_processor.py    # Limpieza de markdown
│   ├── batch_processor.py       # Agrupación de contenido
│   ├── strategies/              # Estrategias por sitio
│   │   ├── base_strategy.py
│   │   ├── anid_strategy.py
│   │   ├── centro_estudios_strategy.py
│   │   └── generic_strategy.py
│   └── pagination/              # Lógica de paginación
│       ├── base_pagination.py
│       ├── anid_pagination.py
│       └── generic_pagination.py
│
├── llm/
│   ├── gemini_client.py         # Cliente REST para Gemini
│   ├── prompts.py               # Templates de prompts
│   ├── predictor.py             # Lógica de predicción
│   └── extractors/
│       └── llm_extractor.py     # Extracción con LLM
│
├── utils/
│   ├── api_key_manager.py       # Rotación de API keys
│   ├── history_manager.py       # Gestión de historial por sitio
│   ├── file_manager.py          # Persistencia (cache, predicciones, debug)
│   ├── lock_manager.py          # Locks para operaciones concurrentes
│   ├── scraping_state.py        # Estado persistente de scraping
│   ├── date_parser.py           # Parsing de fechas
│   ├── deterministic_date_extractor.py  # Extracción sin LLM
│   ├── anid_previous_concursos.py       # Parser de "Concursos anteriores"
│   └── extractors/              # Extractores específicos
│       ├── base_extractor.py
│       ├── anid_extractor.py
│       └── generic_extractor.py
│
├── scripts/
│   ├── daily_anid.py            # Script de scraping diario
│   └── run_daily_anid.sh        # Wrapper para cron
│
└── data/                        # Generado en runtime
    ├── history/                 # Historial por sitio (JSON)
    ├── predictions/             # Predicciones por sitio
    ├── raw_pages/               # Cache de HTML/Markdown
    ├── scraping_state/          # Estado de scraping en curso
    ├── locks/                   # Archivos de lock
    └── debug/                   # Logs de ejecución
        ├── scraping/
        └── predictions/
```

---

## Modelos de Datos

### Concurso

```python
class Concurso(BaseModel):
    nombre: str                        # Nombre del concurso
    organismo: str                     # ANID, MINEDUC, etc.
    url: str                           # URL de origen
    fecha_apertura: Optional[str]      # Texto original de fecha
    fecha_cierre: Optional[str]        # Texto original de fecha
    estado: Optional[str]              # Abierto, Cerrado, Suspendido (calculado)
    financiamiento: Optional[str]      # Monto o tipo
    descripcion: Optional[str]         # Resumen breve
    subdireccion: Optional[str]        # Área dentro del organismo
    extraido_en: Optional[str]         # Timestamp ISO
    fuente: Optional[str]              # Dominio de origen
```

El campo `estado` no lo genera el LLM. Se calcula comparando `fecha_cierre` con la fecha actual:
- Si `fecha_cierre` es pasada → "Cerrado"
- Si `fecha_apertura` es futura → "Próximo"
- Si la URL contiene "concurso-suspendido" → "Suspendido"
- Caso contrario → "Abierto"

### Predicción

```python
class PrediccionConcurso(BaseModel):
    es_mismo_concurso: bool            # Siempre True cuando hay versiones anteriores
    fecha_predicha: Optional[str]      # YYYY-MM-DD o null si no es predecible
    justificacion: str                 # Explicación de la predicción
```

Las predicciones se procesan en lotes de 10 concursos para reducir llamadas al LLM.

---

## Sistema de Estrategias

El sistema usa el patrón Strategy para manejar sitios con estructuras diferentes. Cada estrategia encapsula:

- Configuración de Crawl4AI (timeouts, selectores, waits)
- Tipo de paginación (dinámica o tradicional)
- Extracción de "concursos anteriores" (si el sitio los tiene)
- Nombre del organismo

### Estrategias Implementadas

| Estrategia | Dominio | Paginación | Concursos Anteriores |
|------------|---------|------------|----------------------|
| ANIDStrategy | anid.cl | Dinámica (JetEngine) | Sí |
| CentroEstudiosStrategy | centroestudios.mineduc.cl | Sin paginación | No |
| GenericStrategy | Otros | Tradicional (enlaces HTML) | No |

### Selección de Estrategia

```python
# En crawler/strategies/__init__.py
STRATEGY_REGISTRY = {
    "anid.cl": ANIDStrategy,
    "www.anid.cl": ANIDStrategy,
    "centroestudios.mineduc.cl": CentroEstudiosStrategy,
}

def get_strategy_for_url(url: str) -> ScrapingStrategy:
    domain = urlparse(url).netloc.replace("www.", "")
    strategy_class = STRATEGY_REGISTRY.get(domain, GenericStrategy)
    return strategy_class()
```

El código genérico (`WebScraper`, `ExtractionService`) nunca contiene lógica específica de sitio. Toda personalización está en las estrategias.

---

## Flujo de Extracción

El proceso completo de extracción sigue estas fases:

### Fase 1: Scraping de listados

1. El usuario selecciona un sitio en la UI
2. `ExtractionService` obtiene la estrategia correspondiente
3. Se ejecuta scraping con paginación según el tipo de sitio:
   - ANID: Clicks JavaScript en botones de JetEngine
   - Centro Estudios: Página única, sin paginación
   - Otros: Seguimiento de enlaces HTML

### Fase 2: Extracción inicial con LLM

1. El markdown obtenido se limpia (elimina navegación, footers, etc.)
2. Se agrupa en batches de hasta 250,000 caracteres
3. Cada batch se envía a Gemini con el schema de `ConcursoResponse`
4. Se valida la cantidad extraída; si hay pérdida, se reintenta con modelo más potente

### Fase 3: Scraping de páginas individuales

1. Se extraen las URLs únicas de cada concurso
2. Se scrapea cada página individual en paralelo
3. Se guarda HTML y Markdown en `data/raw_pages/`

### Fase 4: Extracción determinística

Antes de usar el LLM, se intenta extraer datos de forma determinística:

```python
# utils/deterministic_date_extractor.py
def extract_concurso_data_deterministically(markdown, url, html):
    # Nombre: desde <title>, og:title, o primer <h1>
    # Fechas: patrones "Inicio:", "Cierre:" en el texto
    # Suspendido: URL contiene "concurso-suspendido" o texto lo menciona
```

Si la extracción determinística obtiene nombre y fechas, el LLM solo completa campos faltantes.

### Fase 5: Enriquecimiento con LLM

Los concursos sin datos determinísticos se envían al LLM para completar:
- Nombre (si falta)
- Fechas (si faltan)
- Financiamiento
- Descripción
- Subdirección

### Fase 6: Post-procesamiento

1. Se normalizan fechas con `date_parser.parse_date()`
2. Se calcula `estado` según fechas
3. Se extraen "concursos anteriores" usando `strategy.extract_previous_concursos()`
4. Se actualiza el historial

### Fase 7: Reparación automática

Si quedan concursos incompletos (sin nombre o fechas), se ejecuta reparación:
1. Se consulta el cache de `raw_pages/`
2. Se reintenta extracción con LLM
3. Se marca como suspendido si no se puede completar

---

## Flujo de Predicción

### Predicciones por lotes

1. Se cargan concursos cerrados del historial
2. Se filtran aquellos con `previous_concursos` no vacío
3. Se detectan casos de "auto-referencia" (el concurso actual aparece en sus propios anteriores)
4. Los concursos predecibles se agrupan en batches de 10
5. Cada batch se envía al LLM con el prompt de predicción
6. Se guardan predicciones válidas y se marcan los no predecibles

### Predicción determinística para concursos manuales

Los concursos agregados manualmente reciben predicción automática:
```python
fecha_predicha = fecha_apertura + 1 año
```

No pasan por el flujo de predicción con LLM.

### Validación de predicciones

Una predicción es válida si:
- `fecha_predicha` está en el futuro
- `fecha_predicha` no supera 18 meses desde hoy
- La justificación tiene al menos 30 caracteres

---

## Persistencia

### Historial

Archivo: `data/history/history_{site}.json`

```json
{
  "site": "anid.cl",
  "created_at": "2025-01-01T00:00:00",
  "last_updated": "2025-12-18T10:00:00",
  "concursos": [
    {
      "nombre": "FONDECYT Regular 2025",
      "url": "https://anid.cl/...",
      "organismo": "ANID",
      "subdireccion": "Proyectos de Investigación",
      "first_seen": "2025-01-01T00:00:00",
      "last_seen": "2025-12-18T10:00:00",
      "versions": [
        {
          "fecha_apertura": "2025-03-01",
          "fecha_cierre": "2025-05-15",
          "estado": "Cerrado",
          "extraido_en": "2025-03-01T10:00:00"
        }
      ],
      "previous_concursos": [
        {"nombre": "FONDECYT Regular 2024", "año": 2024, "fecha_apertura": "2024-03-01"},
        {"nombre": "FONDECYT Regular 2023", "año": 2023, "fecha_apertura": "2023-03-01"}
      ]
    }
  ]
}
```

### Cache de páginas

Directorio: `data/raw_pages/{site}/`

- `{slug}.html` - HTML completo
- `{slug}.md` - Markdown limpio
- `index_{site}.json` - Índice URL → rutas

El cache permite reparar concursos sin re-scrapear.

### Predicciones

Archivo: `data/predictions/predictions_{site}.json`

```json
[
  {
    "concurso_url": "https://anid.cl/...",
    "concurso_nombre": "FONDECYT Regular 2025",
    "fecha_predicha": "2026-03-01",
    "justificacion": "El concurso ha abierto en marzo durante los últimos 3 años.",
    "created_at": "2025-12-18T10:00:00"
  }
]
```

### Estado de scraping

Archivo: `data/scraping_state/current_scraping.json`

```json
{
  "site": "anid.cl",
  "in_progress": true,
  "should_stop": false,
  "timestamp": 1734520800
}
```

El estado persistente permite:
- Cancelar scraping desde otra sesión de Streamlit
- Detectar scraping abandonado (estados >30 minutos se limpian)
- Sincronizar UI con proceso en background

---

## Locks y Concurrencia

El sistema usa locks basados en archivos para evitar operaciones concurrentes sobre el mismo sitio.

```python
# utils/lock_manager.py
with site_operation_lock("anid.cl", "scrape", timeout_seconds=60, stale_seconds=300):
    # Operación protegida
```

- `timeout_seconds`: Tiempo máximo de espera para adquirir lock
- `stale_seconds`: Locks más antiguos se consideran abandonados y se eliminan

El servicio de predicción verifica si hay scraping en curso antes de ejecutar:
```python
if is_operation_locked(site, "scrape"):
    return {"error": "Hay scraping en curso"}
```

---

## Interfaz de Usuario

La UI tiene 5 pestañas:

### Visualización

Vista de solo lectura con todos los concursos de todos los sitios. Filtros:
- Estado (Abierto, Cerrado, Suspendido)
- Organismo
- Subdirección
- Fuente (sitio de origen)
- Búsqueda por texto
- Con/Sin predicción

Muestra predicciones cercanas (próximos 30 días) en un panel destacado.

### Explorar Concursos

Permite:
- Ver concursos por sitio
- Filtrar por estado y subdirección
- Revisar y reparar concursos incompletos
- Eliminar concursos individuales
- Limpiar historial completo

### Predicciones

Permite:
- Generar predicciones masivas por sitio
- Aplicar filtros antes de predecir
- Ver predicciones existentes
- Eliminar predicciones individuales o por lote

### Scraping y Configuración

Permite:
- Seleccionar sitio a scrapear
- Configurar número de páginas
- Cancelar scraping en curso
- Gestionar API keys de Gemini
- Seleccionar modelo LLM

### Concursos Manuales

Permite:
- Agregar concursos que no provienen de scraping
- Validación de fechas (cierre > apertura)
- Predicción automática (+1 año)
- Eliminar concursos manuales

---

## Despliegue

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app

# Dependencias de sistema
RUN apt-get update && apt-get install -y build-essential curl git

# Dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright (para Crawl4AI)
RUN python -m playwright install-deps chromium && \
    python -m playwright install chromium

COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "main.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
```

### Docker Compose (desarrollo local)

```yaml
services:
  app:
    build: .
    container_name: proyect-crawl
    ports:
      - "${PORT:-8501}:8501"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

### AWS EC2

Requisitos:
- Instancia t3.medium o superior (Playwright consume memoria)
- Security group: puerto 8501 abierto
- IP elástica asignada

El workflow de GitHub Actions:
1. Build de imagen Docker
2. Push a ECR
3. SSH a EC2
4. Pull de imagen
5. Reinicio de contenedor

---

## Automatización

### Cron diario

Script: `scripts/daily_anid.py`

```python
def main():
    # Scraping ANID (máximo 2 páginas para concursos recientes)
    concursos = extraction_service.extract_from_urls(
        urls=SEED_URLS["ANID"],
        max_pages=2
    )
    
    # Predicciones para nuevos concursos
    prediction_service.generate_predictions(site="anid.cl")
```

Wrapper bash: `scripts/run_daily_anid.sh`

```bash
#!/bin/bash
cd /home/ubuntu/proyect-crawl
python -m scripts.daily_anid >> data/logs/daily_anid.log 2>&1
```

Cron entry (EC2):
```
0 6 * * * /home/ubuntu/proyect-crawl/scripts/run_daily_anid.sh
```

---

## Rotación de API Keys

El sistema soporta múltiples API keys de Gemini para manejar límites de cuota.

```python
class APIKeyManager:
    def get_active_key(self) -> str:
        # Retorna key activa
    
    def mark_key_exhausted(self, key: str):
        # Marca key como agotada
    
    def rotate_key(self) -> str:
        # Cambia a siguiente key disponible
```

Ante error 429 (quota exceeded):
1. Se marca la key actual como agotada
2. Se rota a la siguiente key disponible
3. Se reintenta la operación

---

## Agregar un Nuevo Sitio

1. **Crear estrategia** en `crawler/strategies/`:

```python
class NuevoSitioStrategy(ScrapingStrategy):
    @property
    def site_name(self) -> str:
        return "nuevo-sitio.cl"
    
    @property
    def site_display_name(self) -> str:
        return "Nuevo Sitio"
    
    def get_crawler_config(self) -> Dict[str, Any]:
        return {
            "wait_for": "css:.contenedor-concursos",
            "page_timeout": 30000,
        }
    
    def supports_dynamic_pagination(self) -> bool:
        return False  # o True si usa JavaScript
    
    async def scrape_with_pagination(self, url, max_pages, crawler, config):
        # Implementar lógica de scraping
```

2. **Registrar estrategia** en `crawler/strategies/__init__.py`:

```python
from .nuevo_sitio_strategy import NuevoSitioStrategy
register_strategy("nuevo-sitio.cl", NuevoSitioStrategy)
```

3. **Agregar configuración** en `config/sites.py`:

```python
SEED_URLS["Nuevo Sitio"] = ["https://nuevo-sitio.cl/concursos/"]
SITE_DOMAINS["nuevo-sitio.cl"] = "Nuevo Sitio"
SITE_NAME_MAPPING["Nuevo Sitio"] = "nuevo-sitio.cl"
```

4. Si el sitio tiene paginación dinámica, crear clase en `crawler/pagination/`.

5. Si tiene "concursos anteriores", crear extractor en `utils/extractors/`.

---

## Manejo de Errores

### En extracción

- Timeout de página: Se continúa con siguiente URL
- Error de LLM: Hasta 3 reintentos con delay exponencial
- JSON truncado: Se aumenta `maxOutputTokens` y se reintenta
- Pérdida de datos: Re-extracción con modelo más potente

### En predicción

- Scraping en curso: Se retorna mensaje de espera
- Concurso sin previous_concursos: Se marca como no predecible
- Auto-referencia: Se marca como no predecible con justificación automática
- Error de parsing JSON: Hasta 3 reintentos

### En scraping

- Lock en uso: RuntimeError con mensaje claro
- Estado abandonado: Limpieza automática después de 30 minutos
- Cancelación por usuario: Se verifica `should_stop` cada 500ms

---

## Configuración

### Variables de entorno

```bash
# .env
API_KEYS_PATH=data/.api_keys.json   # Ruta a archivo de API keys
DATA_DIR=data                        # Directorio de datos
PORT=8501                            # Puerto de Streamlit
```

### API Keys

Archivo: `data/.api_keys.json`

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

### Parámetros globales

Archivo: `config/global_config.py`

```python
CRAWLER_CONFIG = {
    "headless": True,
    "page_timeout": 120000,
    "scan_full_page": True,
}

GEMINI_CONFIG = {
    "model": "gemini-2.5-flash-lite",
    "temperature": 0.1,
    "max_output_tokens": 8000,
}

EXTRACTION_CONFIG = {
    "batch_size": 250000,
    "max_retries": 3,
    "api_timeout": 60,
}
```
