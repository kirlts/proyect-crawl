# Arquitectura del Sistema - Guía Maestra (v4.4)

> Este documento es la **fuente de verdad** sobre cómo funciona internamente el sistema:
> scraping, extracción con LLM, predicciones, manejo de historial, depuración, UI y
> rotación de API keys. Se mantiene alineado con el estado actual del código.

## 📋 Tabla de Contenidos

1. [Visión General](#visión-general)
2. [Estructura Modular](#estructura-modular)
3. [Modelos de Datos](#modelos-de-datos)
4. [Flujos de Procesamiento](#flujos-de-procesamiento)
5. [Extracción Determinística vs LLM](#extracción-determinística-vs-llm)
6. [Manejo de Errores y Reintentos](#manejo-de-errores-y-reintentos)
7. [Manejo de Múltiples Versiones de Predicciones](#manejo-de-múltiples-versiones-de-predicciones)
8. [Decisiones de Diseño](#decisiones-de-diseño)
9. [Guía para Desarrollo](#guía-para-desarrollo)
10. [Extensibilidad](#extensibilidad)
11. [Convenciones de Código](#convenciones-de-código)
12. [Recursos y Referencias](#recursos-y-referencias)

---

## Visión General

Sistema para **extraer, limpiar, historizar y predecir** información de concursos de financiamiento (principalmente ANID y otros sitios públicos).

### Objetivos Principales

- **Modular**: Servicios separados para scraping/extracción y predicción
- **Escalable**: Fácil agregar nuevos sitios, modelos LLM o flujos
- **Mantenible**: Código estructurado con separación clara UI/negocio
- **Auditable**: Archivos de debug detallados para cada proceso
- **Robusto**: Manejo explícito de rate limits, timeouts, errores HTTP y reintentos automáticos
- **Reutilizable**: Datos críticos (como `previous_concursos`) se guardan en historial y se reutilizan

### Stack Tecnológico

- **Python 3.10+**: Lenguaje principal
- **Streamlit**: Framework para UI web
- **Crawl4AI**: Framework de web scraping con Playwright
- **Google Gemini API** (`gemini-2.5-flash-lite` por defecto): LLM para extracción y predicción
- **Pydantic**: Modelos fuertemente tipados y validación
- **Asyncio**: Scraping asíncrono de páginas individuales
- **Requests**: Llamadas REST directas a Gemini con Structured Outputs
- **BeautifulSoup**: Parsing fino de ANID para "Concursos anteriores"

---

## Estructura Modular

```
proyect-crawl/
├── models/                    # Modelos de datos centralizados
│   ├── concurso.py            # Modelo Pydantic Concurso
│   └── prediccion.py         # Modelos de predicción
│
├── services/                  # Servicios de negocio (orquestación)
│   ├── extraction_service.py  # Scraping + extracción LLM + historial
│   └── prediction_service.py  # Generación de predicciones
│
├── crawler/                   # Módulo de scraping web
│   ├── scraper.py             # WebScraper principal (usa estrategias)
│   ├── strategies/            # NUEVO: Estrategias por sitio
│   │   ├── __init__.py        # Registro de estrategias
│   │   ├── base_strategy.py   # Clase base abstracta
│   │   ├── anid_strategy.py   # Estrategia específica ANID
│   │   └── generic_strategy.py # Estrategia genérica (fallback)
│   ├── pagination/            # NUEVO: Módulo de paginación
│   │   ├── __init__.py
│   │   ├── base_pagination.py # Clase base
│   │   ├── anid_pagination.py # Paginación dinámica ANID
│   │   └── generic_pagination.py # Paginación tradicional
│   ├── markdown_processor.py  # Limpieza y optimización
│   ├── batch_processor.py     # Agrupación en batches
│   └── pagination.py          # Detección de paginación (legacy, mantenido para compatibilidad)
│
├── llm/                       # Integración con LLM
│   ├── gemini_client.py       # Gestión de API keys y rotación
│   ├── prompts.py             # Templates de prompts
│   ├── predictor.py           # Lógica de predicción
│   └── extractors/
│       └── llm_extractor.py   # Extracción con LLM (REST API)
│
├── utils/                     # Utilidades generales
│   ├── extractors/            # NUEVO: Extractores específicos por sitio
│   │   ├── __init__.py
│   │   ├── base_extractor.py  # Clase base
│   │   ├── anid_extractor.py  # Extracción "Concursos anteriores" ANID
│   │   └── generic_extractor.py # Extracción genérica
│   ├── api_key_manager.py     # Gestión y rotación de API keys
│   ├── file_manager.py        # Guardado, carga y debug
│   ├── history_manager.py     # Gestión de historial
│   ├── date_parser.py         # Parsing de fechas
│   ├── deterministic_date_extractor.py # Extracción determinística (nombre, fechas)
│   └── anid_previous_concursos.py # Extracción "Concursos anteriores" (legacy, mantenido para compatibilidad)
│
├── data/                      # Datos (se crea automáticamente)
│   ├── history/               # Historial por sitio
│   ├── predictions/           # Predicciones y no-predecibles
│   ├── raw_pages/             # Cache persistente de HTML/Markdown por sitio/URL (sin compresión)
│   └── debug/                 # Archivos de debug
│       ├── scraping/          # Debug de scraping
│       ├── predictions/       # Debug de predicciones
│       └── repair/            # Debug de reparación
│
├── config/                    # NUEVO: Módulo de configuración
│   ├── __init__.py            # Exporta configuraciones
│   ├── global_config.py       # Configuración global
│   └── sites.py               # Configuración por sitio
├── main.py                    # UI Streamlit
├── config.py                  # Configuración centralizada (wrapper para compatibilidad)
└── docs/                      # Documentación
```

### Descripción de Módulos Clave

#### `crawler/scraper.py`

**Responsabilidad**: Scraping web con Crawl4AI usando estrategias por sitio.

- `scrape_url_with_pagination()`: Scraping con paginación usando estrategias (reemplaza `scrape_url_with_dynamic_pagination()`)
- Obtiene la estrategia apropiada según la URL usando `get_strategy_for_url()`
- Delega la lógica de paginación a la estrategia específica del sitio
- `scrape_url_simple()`: Scraping simple para sitios sin paginación
- `scrape_url()`: Scraping básico de una URL

#### `crawler/strategies/`

**Responsabilidad**: Sistema de estrategias para manejar diferentes sitios (lógica específica aislada del código genérico).

- **`base_strategy.py`**: Clase abstracta `ScrapingStrategy` que define la interfaz:
  - `site_name`, `site_display_name`: Propiedades del sitio
  - `get_crawler_config()`: Configuración específica de Crawl4AI
  - `supports_dynamic_pagination()`: Indica si requiere paginación dinámica
  - `scrape_with_pagination()`: Método principal de scraping con paginación (o bypass si el sitio es de una sola página)
  - `extract_previous_concursos()`: Extracción de "concursos anteriores" (opcional)
  - `get_organismo_name()`: Nombre del organismo
  - `get_known_subdirecciones()`: Subdirecciones conocidas (opcional)

- **`anid_strategy.py`** (ANID):
  - Usa `AnidPagination` para paginación dinámica JetEngine.
  - Usa `AnidExtractor` para extraer "Concursos anteriores".
  - Configuración específica: `wait_for: "css:.jet-listing-grid__item"`, waits y JS para contenido AJAX.
  - Organismo: "ANID". Subdirecciones conocidas: Capital Humano, Investigación Aplicada, etc.

- **`centro_estudios_strategy.py`** (Centro Estudios MINEDUC / FONIDE):
  - Sitio estático de una sola página; sin paginación y sin LLM.
  - Bypass de waits pesados/JS: timeout corto (≈15s), JS trivial, sin esperas JetEngine.
  - Extracción determinista del bloque “Convocatoria actual (FONIDE NN)”: nombre adaptable (FONIDE 16/17/…), fecha de consultas (apertura) y fecha de postulaciones (cierre).
  - Guarda HTML/Markdown completos en cache e historial; no hay `previous_concursos` externos.

- **`generic_strategy.py`**:
  - Fallback para sitios estándar sin lógica específica.
  - Usa paginación tradicional (enlaces HTML).
  - Configuración básica de Crawl4AI; no extrae "concursos anteriores".

- **`__init__.py`**: Registro de estrategias:
  - `STRATEGY_REGISTRY`: Diccionario que mapea dominios a clases de estrategia
  - `register_strategy()`: Registra una estrategia para un dominio
  - `get_strategy_for_url()`: Obtiene estrategia según URL (retorna GenericStrategy si no hay específica)
  - `get_strategy_for_site()`: Obtiene estrategia según nombre de sitio

#### `crawler/pagination/`

**Responsabilidad**: Módulo de paginación para diferentes tipos de sitios.

- **`base_pagination.py`**: Clase abstracta `BasePagination` con método `scrape_pages()`
- **`anid_pagination.py`**: Implementación de paginación dinámica para ANID:
  - Maneja clicks en botones JavaScript de JetEngine
  - Espera inteligente de contenido AJAX
  - Detección robusta de última página (verifica botón ">")
  - Hooks específicos de Playwright para esperar carga de contenido
- **`generic_pagination.py`**: Paginación tradicional usando enlaces HTML:
  - Busca enlaces de paginación en el HTML
  - Scrapea cada página individualmente

#### `utils/extractors/`

**Responsabilidad**: Extractores de datos específicos por sitio.

- **`base_extractor.py`**: Clase abstracta `BaseExtractor` con método `extract_previous_concursos()`
- **`anid_extractor.py`**: Extractor específico para ANID:
  - Extrae "Concursos anteriores" usando selectores JetEngine
  - Lógica mejorada de extracción de nombres y años
  - Filtra subdirecciones conocidas
- **`generic_extractor.py`**: Extractor genérico que retorna lista vacía

#### `config/sites.py`

**Responsabilidad**: Configuración específica por sitio.

- `SITE_CONFIGS`: Diccionario con configuración por dominio
- `get_site_config()`: Obtiene configuración para un dominio
- `get_site_name_for_history()`: Convierte nombre de sitio a nombre para historial
- `SEED_URLS`: URLs semilla por sitio
- `SITE_NAME_MAPPING`: Mapeo de nombres de sitio a nombres para historial

#### `services/extraction_service.py`

**Responsabilidad**: Orquestar scraping, extracción con LLM y actualización de historial.

- **Usa estrategias**: Obtiene estrategia apropiada con `get_strategy_for_url()`
- **Sin lógica hardcodeada**: Todas las decisiones específicas de sitio se delegan a estrategias
- Scraping de URLs de listado (con o sin paginación, según estrategia)
- Extracción con LLM desde markdown combinado
- Scraping de páginas individuales de concursos
- Enriquecimiento por segunda pasada con LLM
- Extracción de "concursos anteriores" usando `strategy.extract_previous_concursos()`
- Detección y recuperación automática de pérdida de datos
- Actualización del historial (incluyendo `previous_concursos`)
- Reparación de concursos incompletos
- Generación de debug de scraping

#### `services/prediction_service.py`

**Responsabilidad**: Generar predicciones usando datos del historial (sin scraping adicional).

- Trabaja **solo** con datos ya presentes en el historial
- Usa `previous_concursos` para alimentar el LLM
- **Batching optimizado**: 
  - Filtra casos de `self_reference` ANTES de crear batches
  - Agrupa concursos predecibles en batches de exactamente 10
  - Garantiza que cada batch tenga 10 concursos (excepto el último si hay menos)
- Predicciones en lote (con filtros) e individuales (desde UI)
- Maneja concursos no predecibles (`self_reference`, `llm_rejected`)
- Persiste predicciones y no-predecibles en archivos JSON
- Emite debug de predicciones (masivas e individuales)

#### `llm/predictor.py`

**Responsabilidad**: Construir prompts y llamar al LLM para predicciones.

- **Métodos de predicción**:
  - `predict_from_previous_concursos()`: Predicción individual (2000 tokens)
  - `predict_from_previous_concursos_batch()`: Predicción en batch (12000 tokens)
- Usa Structured Outputs con `PrediccionConcurso` y `PrediccionBatchResponse`
- **Reintentos automáticos**: Hasta 3 intentos para errores de parsing JSON
- **Límites de tokens dinámicos**:
  - Predicciones individuales: 2000 tokens
  - Predicciones en batch: 12000 tokens (para acomodar 10 concursos con justificaciones)
- Manejo detallado de errores (HTTP, JSON, Pydantic, red)
- Prompts optimizados con lenguaje afirmativo y ejemplos conceptuales

#### `llm/extractors/llm_extractor.py`

**Responsabilidad**: Extraer información estructurada usando LLM.

- `extract_from_batch()`: Extrae concursos desde markdown combinado
- Construye prompts con Structured Outputs
- Ajusta `maxOutputTokens` dinámicamente según tamaño del batch
- **Reintento automático con aumento de tokens**: Si el JSON está truncado, aumenta `maxOutputTokens` y reintenta automáticamente (hasta 3 veces, hasta 32000 tokens)
- Maneja rate limits, timeouts y errores de conexión
- Rotación automática de API keys
- Validación de pérdida de datos y re-extracción con modelo más potente

#### `utils/history_manager.py`

**Responsabilidad**: Gestionar historial de concursos.

- `load_history()`, `save_history()`: Persistencia por sitio
- `update_history()`: Actualiza o crea entradas, maneja versiones
- Guarda `latest_page_content` y `previous_concursos` por URL
- `find_incomplete_concurso_urls()`: Identifica concursos con datos faltantes
- `fix_suspended_concursos_by_url()`: Corrige concursos suspendidos por URL
- `delete_concurso()`, `clear_history()`: Gestión de eliminación

#### `utils/deterministic_date_extractor.py`

**Responsabilidad**: Extracción determinística de datos antes de usar LLM (optimización).

- `extract_nombre_deterministically()`: Extrae nombre desde `<title>`, `og:title`, `<h1>`, headings
- `extract_dates_deterministically()`: Extrae fechas desde patrones "Inicio:", "Cierre:"
- `extract_concurso_data_deterministically()`: Función principal que combina ambas extracciones
- **Objetivo**: Reducir llamadas al LLM cuando los datos están en formato estándar

#### `utils/anid_previous_concursos.py`

**Responsabilidad**: Extraer información de "Concursos anteriores" de páginas ANID.

- `extract_previous_concursos_from_html()`: Extrae nombres, fechas, URLs y años de concursos anteriores
- **Extracción mejorada de nombres**: 
  - Prioriza texto del link, luego atributos `title` y `data-*`, luego slug de URL
  - Filtra textos genéricos como "Ver más", "Leer más"
  - Filtra subdirecciones conocidas para evitar identificarlas como nombres de concursos
- **Extracción mejorada de años**: Extrae desde múltiples fuentes (nombre, fecha_apertura, fecha_cierre, URL)
- Deduplicación por nombre + fechas para evitar duplicados

#### `utils/file_manager.py`

**Responsabilidad**: Guardado, carga y debug de resultados.

- `save_debug_info_scraping()`: Debug de scraping
- `save_debug_info_predictions()`: Debug de predicciones en lote
- `save_debug_info_individual_prediction()`: Debug de predicciones individuales
- `save_debug_info_repair()`: Debug de reparación
- `save_predictions()`/`load_predictions()`: Predicciones (evita duplicados)
- `save_unpredictable_concursos()`/`load_unpredictable_concursos()`: No-predecibles
- `delete_prediction()`, `clear_predictions()`: Gestión de eliminación
- **Cache de páginas individuales (sin compresión)**:
  - `save_page_cache(site, url, html, markdown)`: Guarda HTML/MD completos y actualiza índice por URL (sobrescribe si ya existía)
  - `load_page_cache(site, url)`: Recupera HTML/MD desde cache para reparaciones/predicciones antes de re-scrapear
  - Índice por sitio en `data/raw_pages/index_<site>.json`; archivos en `data/raw_pages/<site>/<slug>.html/.md`

---

## Sistema Multi-Sitio y Estrategias

### Arquitectura de Estrategias

El sistema utiliza el **Strategy Pattern** para manejar diferentes sitios de forma modular y extensible. Cada sitio puede tener su propia estrategia que encapsula toda la lógica específica, permitiendo que el código genérico funcione con cualquier sitio.

### Cómo Funciona

1. **Registro de Estrategias**: Las estrategias se registran en `crawler/strategies/__init__.py` mapeando dominios a clases de estrategia.

2. **Selección Automática**: Cuando se necesita scrapear una URL, el sistema:
   - Obtiene el dominio de la URL
   - Busca una estrategia específica en el registro
   - Si no encuentra, usa `GenericStrategy` como fallback

3. **Delegación**: El código genérico (como `WebScraper` y `ExtractionService`) delega toda la lógica específica a la estrategia:
   - Configuración de Crawl4AI
   - Tipo de paginación (dinámica vs tradicional)
   - Extracción de datos específicos (ej: "concursos anteriores")
   - Nombre del organismo

### Flujo de Selección de Estrategia

```
URL ingresada
    ↓
get_strategy_for_url(url)
    ↓
Extraer dominio (ej: "anid.cl")
    ↓
Buscar en STRATEGY_REGISTRY
    ↓
¿Encontrada? → Usar estrategia específica
    ↓
¿No encontrada? → Usar GenericStrategy
```

### Separación de Responsabilidades

- **Código Genérico**: `WebScraper`, `ExtractionService`, `GenericStrategy`
  - No contiene lógica específica de ningún sitio
  - Funciona con cualquier estrategia que implemente la interfaz

- **Código Específico**: `ANIDStrategy`, `AnidPagination`, `AnidExtractor`
  - Contiene TODA la lógica específica de ANID
  - Está completamente aislado en módulos específicos
  - No interfiere con otros sitios

---

## Cómo Agregar un Nuevo Sitio

Esta sección explica paso a paso cómo agregar soporte para un nuevo sitio al sistema.

### Paso 1: Analizar Estructura del Sitio

Antes de implementar, analiza el sitio objetivo:

1. **Tipo de Paginación**:
   - ¿Usa paginación dinámica (JavaScript/AJAX)? → Requiere estrategia específica
   - ¿Usa enlaces HTML tradicionales? → Puede usar `GenericStrategy`

2. **Estructura de Listado**:
   - ¿Cómo se muestran los concursos en la página principal?
   - ¿Qué selectores CSS se usan?
   - ¿Requiere espera especial para contenido dinámico?

3. **Estructura de Páginas Individuales**:
   - ¿Dónde está el nombre del concurso? (`<title>`, `<h1>`, etc.)
   - ¿Dónde están las fechas? (patrones específicos, clases CSS)
   - ¿Hay información adicional estructurada?

4. **"Concursos Anteriores" o Similar**:
   - ¿El sitio tiene una sección de versiones anteriores?
   - ¿Cómo se estructura esta información?
   - ¿Qué selectores CSS se usan?

### Paso 2: Decidir si Necesitas Estrategia Específica

**Usa `GenericStrategy` (sin crear estrategia específica) si**:
- El sitio usa paginación tradicional (enlaces HTML)
- La estructura es estándar (HTML común)
- No tiene sección de "concursos anteriores"
- La configuración básica de Crawl4AI es suficiente

**Crea estrategia específica si**:
- El sitio requiere paginación dinámica (JavaScript)
- Tiene estructura única que requiere lógica especial
- Tiene sección de "concursos anteriores" o similar
- Requiere configuración específica de Crawl4AI

### Paso 3: Crear Estrategia Específica (si es necesario)

Si necesitas una estrategia específica, crea `crawler/strategies/tu_sitio_strategy.py`:

```python
from crawler.strategies.base_strategy import ScrapingStrategy
from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler

class TuSitioStrategy(ScrapingStrategy):
    """Estrategia específica para tu sitio."""
    
    @property
    def site_name(self) -> str:
        return "tu-sitio.cl"
    
    @property
    def site_display_name(self) -> str:
        return "Tu Sitio"
    
    def get_crawler_config(self) -> Dict[str, Any]:
        """Configuración específica de Crawl4AI."""
        return {
            "wait_for": "css:.selector-especifico",  # Ajustar según el sitio
            "wait_until": "domcontentloaded",
            "scan_full_page": True,
        }
    
    def supports_dynamic_pagination(self) -> bool:
        return True  # o False según el tipo de paginación
    
    async def scrape_with_pagination(
        self,
        url: str,
        max_pages: int,
        crawler: AsyncWebCrawler,
        base_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Implementar lógica de scraping con paginación."""
        # Si usa paginación dinámica, crear TuSitioPagination
        # Si usa paginación tradicional, usar GenericPagination
        pass
    
    def extract_previous_concursos(
        self,
        html: str,
        url: str
    ) -> List[Dict[str, Any]]:
        """Extraer 'concursos anteriores' si el sitio los tiene."""
        # Si el sitio no tiene esta funcionalidad, retornar []
        return []
    
    def get_organismo_name(self, url: str) -> str:
        return "Tu Organismo"
    
    def get_known_subdirecciones(self) -> Set[str]:
        """Retornar subdirecciones conocidas si aplica."""
        return set()
```

### Paso 4: Crear Paginación Específica (si es necesario)

Si el sitio requiere paginación dinámica, crea `crawler/pagination/tu_sitio_pagination.py`:

```python
from crawler.pagination.base_pagination import BasePagination
from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler

class TuSitioPagination(BasePagination):
    """Paginación dinámica específica para tu sitio."""
    
    async def scrape_pages(
        self,
        url: str,
        max_pages: int,
        crawler: AsyncWebCrawler,
        config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Implementar lógica de paginación dinámica."""
        # Ver ejemplo completo en crawler/pagination/anid_pagination.py
        pass
```

### Paso 5: Crear Extractor Específico (si es necesario)

Si el sitio tiene "concursos anteriores" o información similar, crea `utils/extractors/tu_sitio_extractor.py`:

```python
from utils.extractors.base_extractor import BaseExtractor
from typing import List, Dict, Any
from bs4 import BeautifulSoup

class TuSitioExtractor(BaseExtractor):
    """Extractor específico para tu sitio."""
    
    def extract_previous_concursos(
        self,
        html: str,
        url: str
    ) -> List[Dict[str, Any]]:
        """Extraer información de concursos anteriores."""
        # Ver ejemplo completo en utils/extractors/anid_extractor.py
        soup = BeautifulSoup(html, 'html.parser')
        # Implementar lógica de extracción
        return []
```

### Paso 6: Registrar la Estrategia

En `crawler/strategies/__init__.py`, actualiza la función `_register_all_strategies()`:

```python
def _register_all_strategies():
    try:
        from crawler.strategies.anid_strategy import ANIDStrategy
        register_strategy("anid.cl", ANIDStrategy)
        register_strategy("www.anid.cl", ANIDStrategy)
        
        # Agregar tu nueva estrategia
        from crawler.strategies.tu_sitio_strategy import TuSitioStrategy
        register_strategy("tu-sitio.cl", TuSitioStrategy)
        register_strategy("www.tu-sitio.cl", TuSitioStrategy)
    except ImportError:
        pass
```

### Paso 7: Configurar Sitio en `config/sites.py`

Agrega la configuración del sitio en `SITE_CONFIGS`:

```python
SITE_CONFIGS = {
    # ... configuraciones existentes ...
    "tu-sitio.cl": {
        "display_name": "Tu Sitio",
        "organismo": "Tu Organismo",
        "crawler_config": {
            "wait_for": "css:.selector-especifico",
            "wait_until": "domcontentloaded",
            "scan_full_page": True,
        },
        "features": {
            "dynamic_pagination": True,  # o False
            "has_previous_concursos": True,  # o False
        },
        "known_subdirecciones": set()  # o conjunto de subdirecciones
    },
}
```

También actualiza `SEED_URLS` y `SITE_NAME_MAPPING`:

```python
SEED_URLS = {
    # ... URLs existentes ...
    "Tu Sitio": [
        "https://tu-sitio.cl/concursos/",
    ],
}

SITE_NAME_MAPPING = {
    # ... mapeos existentes ...
    "Tu Sitio": "tu-sitio.cl",
}
```

### Checklist de Implementación

Al agregar un nuevo sitio, verifica:

- [ ] Estrategia creada e implementada (si es necesaria)
- [ ] Paginación específica creada (si requiere paginación dinámica)
- [ ] Extractor específico creado (si tiene "concursos anteriores")
- [ ] Estrategia registrada en `crawler/strategies/__init__.py`
- [ ] Configuración agregada en `config/sites.py`
- [ ] URLs semilla agregadas en `SEED_URLS`
- [ ] Mapeo de nombre agregado en `SITE_NAME_MAPPING`
- [ ] Verificado funcionamiento básico (scraping de primera página)
- [ ] Verificado paginación (si aplica)
- [ ] Verificado extracción de "concursos anteriores" (si aplica)
- [ ] Documentación actualizada (esta sección)

### Ejemplo Completo: ANID

ANID es un ejemplo completo de estrategia específica:

- **Estrategia**: `crawler/strategies/anid_strategy.py`
  - Usa `AnidPagination` para paginación dinámica
  - Usa `AnidExtractor` para "concursos anteriores"
  - Configuración específica de Crawl4AI

- **Paginación**: `crawler/pagination/anid_pagination.py`
  - Maneja clicks en botones JavaScript
  - Espera inteligente de contenido AJAX
  - Detección robusta de última página

- **Extractor**: `utils/extractors/anid_extractor.py`
  - Extrae "Concursos anteriores" usando selectores JetEngine
  - Lógica mejorada de nombres y años

- **Configuración**: `config/sites.py`
  - Configuración específica de ANID
  - Subdirecciones conocidas

---

## Modelos de Datos

### Modelo `Concurso`

Estructura estándar de un concurso.

**Campos Requeridos**:
```python
nombre: str                    # Nombre completo del concurso
organismo: str                 # ANID, MINEDUC, CNA, etc.
url: str                       # URL de origen
```

**Campos Opcionales Principales**:
```python
fecha_apertura: Optional[str]  # Texto original (ej: "10 de diciembre, 2025")
fecha_cierre: Optional[str]     # Texto original (ej: "19 de marzo, 2026 - 17:00")
financiamiento: Optional[str]  # Monto o tipo
estado: Optional[str]          # "Abierto" o "Cerrado" (calculado)
descripcion: Optional[str]      # Resumen breve
subdireccion: Optional[str]    # Subdirección o área
```

### Modelo `PrediccionConcurso`

Estructura de una predicción.

```python
es_mismo_concurso: bool        # Siempre True cuando se usa previous_concursos
fecha_predicha: Optional[str] # Fecha en formato YYYY-MM-DD o texto descriptivo
justificacion: str             # Párrafo conciso (sin razones_similitud/diferencias)
```

### Modelos de Batch

**`PrediccionConcursoBatchItem`**:
```python
concurso_url: str              # Identificador único del concurso
prediccion: PrediccionConcurso # Predicción individual
```

**`PrediccionBatchResponse`**:
```python
items: List[PrediccionConcursoBatchItem]  # Lista de predicciones
```

---

## Flujos de Procesamiento

### Flujo Principal: Extracción de Concursos

```
1. Usuario ingresa URLs en UI (main.py) — ahora se selecciona **un solo sitio** por corrida para evitar mezclas de dominios.
   ↓
2. ExtractionService.extract_from_urls()
   ├─→ Fase 1: Scraping de páginas principales (delegado a estrategia)
   │   ├─→ ANID: `scrape_url_with_dynamic_pagination()` (JetEngine), detección robusta de última página por ausencia de botón ">".
   │   ├─→ CentroEstudios: bypass de paginación, una sola página, timeout corto y sin JS pesado.
   │   └─→ Otros sitios: paginación tradicional o scraping simple.
   │   ├─→ Limpiar markdown (clean_markdown_for_llm)
   │   └─→ Agrupar en batches (hasta 250,000 caracteres)
   │
   ├─→ Fase 2: Extracción inicial con LLM
   │   ├─→ Para cada batch:
   │   │   ├─→ LLMExtractor.extract_from_batch()
   │   │   │   ├─→ Construir prompt con Structured Outputs
   │   │   │   ├─→ Ajustar maxOutputTokens dinámicamente (mínimo 12000 para batches grandes)
   │   │   │   ├─→ Llamar a Gemini API REST
   │   │   │   ├─→ **Si JSON truncado**: Aumentar tokens y reintentar automáticamente (hasta 3 veces, hasta 32000)
   │   │   │   └─→ Validar con Pydantic
   │   │   │
   │   │   ├─→ Validar cantidad extraída
   │   │   │   └─→ Si pérdida detectada: re-extraer con modelo más potente
   │   │   │
   │   │   └─→ Convertir a objetos Concurso
   │   │
   │   └─→ Validación final de pérdida total
   │
   ├─→ Fase 3: Scraping de páginas individuales
   │   ├─→ Extraer URLs únicas de concursos
   │   ├─→ WebScraper.scrape_url_simple() (concurrente; o bypass si la estrategia ya entrega el concurso único, p. ej. CentroEstudios)
   │   ├─→ Guardar HTML/Markdown completos en cache local `data/raw_pages/<site>/...` (sin compresión) y actualizar índice por URL
   │   ├─→ **OPTIMIZACIÓN: Extracción determinística** (antes de LLM; 100% determinista si la estrategia lo define)
   │   │   ├─→ Extraer nombre desde <title>, og:title, <h1>
   │   │   ├─→ Extraer fechas desde patrones "Inicio:", "Cierre:"
   │   │   └─→ Detectar suspendido desde URL o contenido
   │   └─→ Guardar en enriched_content (con deterministic_data)
   │
   ├─→ Fase 4: Enriquecimiento con LLM
   │   ├─→ Para cada batch de contenido enriquecido:
   │   │   ├─→ LLMExtractor.extract_from_batch()
   │   │   │   └─→ **Preferir datos determinísticos sobre LLM**
   │   │   │       ├─→ Si nombre determinístico existe → usarlo
   │   │   │       ├─→ Si fechas determinísticas existen → usarlas
   │   │   │       └─→ LLM solo completa campos faltantes
   │   │   └─→ Actualizar: financiamiento, descripcion
   │
   ├─→ Fase 5: Reintento de fechas (si faltan)
   │   ├─→ Solo para concursos sin fecha_cierre
   │   └─→ Segundo intento focalizado con LLM
   │
   ├─→ Fase 6: Post-procesamiento
   │   ├─→ Normalizar fechas (parse_date)
   │   ├─→ Calcular estado determinísticamente (Abierto/Cerrado/Suspendido)
   │   │   └─→ **NO se calcula por LLM, siempre determinístico**
   │   └─→ Agregar metadatos (extraido_en, fuente)
   │
   └─→ Fase 7: Actualización de historial
       ├─→ Extraer previous_concursos de páginas individuales
       │   ├─→ strategy.extract_previous_concursos(html, url)
       │   │   ├─→ ANID: AnidExtractor (selectores JetEngine)
       │   │   ├─→ CentroEstudios: no hay previous_concursos externos (lista vacía)
       │   │   └─→ Otros: GenericExtractor (retorna [])
       │   ├─→ Extracción mejorada de nombres (filtra "Ver más", busca en atributos)
       │   └─→ Extracción mejorada de años (desde nombre, fechas o URL)
       ├─→ update_history() con enriched_content
       └─→ Guardar debug (save_debug_info_scraping)
       └─→ **Reparación automática post-scrape**: si el historial queda con concursos incompletos, se ejecuta `repair_incomplete_concursos` sobre esas URLs usando cache HTML/MD (sin re-scrapear si no es necesario), marcando suspendidos por patrón y completando nombre/fechas con LLM solo donde falte.
```

### Flujo de Predicción Masiva (con Batching y Reintentos)

```
1. Usuario ejecuta "Realizar Predicciones" en UI
   ↓
2. PredictionService.generate_predictions()
   ├─→ Cargar concursos del historial
   ├─→ Filtrar: cerrados + con previous_concursos (no vacío)
   └─→ Aplicar filtros adicionales (subdirección, búsqueda)
   ↓
3. Filtrar concursos no predecibles ANTES de crear batches
   ├─→ Filtrar self_reference (marcar como no predecible con justificación automática)
   ├─→ Filtrar concursos sin previous_concursos (salvo sitios habilitados explícitamente, e.g. CentroEstudios, que se predice de forma determinista)
   └─→ Resultado: lista de concursos predecibles
   ↓
4. Agrupar en batches de exactamente 10 concursos predecibles
   ├─→ Para cada batch:
   │   ├─→ Preparar datos (concurso_dict + previous_concursos_info)
   │   │
   │   ├─→ ConcursoPredictor.predict_from_previous_concursos_batch()
   │   │   ├─→ Construir prompt batch (lenguaje afirmativo, ejemplos)
   │   │   ├─→ Usar Structured Outputs (PrediccionBatchResponse)
   │   │   ├─→ maxOutputTokens: 12000 (para 10 concursos)
   │   │   │
   │   │   └─→ **Reintentos automáticos** (hasta 3 intentos):
   │   │       ├─→ Si error de parsing JSON:
   │   │       │   ├─→ Log detallado con posición del error
   │   │       │   ├─→ Delay incremental (2s, 4s, 6s)
   │   │       │   └─→ Reintentar con mismo prompt
   │   │       │
   │   │       └─→ Si agotados 3 intentos:
   │   │           ├─→ Registrar error crítico en debug
   │   │           ├─→ Guardar debug inmediatamente
   │   │           └─→ Detener ejecución de predicciones
   │   │
   │   └─→ Procesar cada predicción del batch:
   │       ├─→ Si fecha_predicha es None → llm_rejected
   │       ├─→ Validar fecha (no pasada, no >1 año futuro)
   │       └─→ Guardar predicción válida
   │
   └─→ Continuar con siguiente batch
   ↓
5. Guardar resultados
   ├─→ save_predictions() → predictions_{site}.json
   ├─→ save_unpredictable_concursos() → unpredictable_{site}.json
   └─→ save_debug_info_predictions() → debug/predictions/
```

### Cache de páginas individuales (HTML/Markdown)

- Se guarda el HTML crudo y el Markdown limpio de cada concurso en `data/raw_pages/<site>/<slug>.html/.md` (sin compresión).
- Índice por sitio en `data/raw_pages/index_<site>.json` mapea URL → rutas, tamaños y timestamp.
- Escritura: solo en el scraping inicial y en cualquier re-scrape explícito; siempre sobrescribe la entrada previa para esa URL. Estrategias deterministas (ej. CentroEstudios) escriben siempre el HTML/MD completo del concurso único.
- Lectura prioritaria: procesos de reparación/predicciones consultan primero el cache; solo si falta (o se decide rescrapear) se vuelve a scrapear y se sobreescribe.
- Clave de deduplicación: combinación sitio + URL, manteniendo la lógica multi-sitio intacta.

### Flujo de Rotación de API Keys

```
1. LLMExtractor/Predictor llama a Gemini API REST
   ↓
2. requests.post() a Gemini API endpoint
   ↓
3. Si error 429 (quota exceeded):
   ├─→ _handle_quota_error()
   │   ├─→ Marcar key actual como agotada
   │   ├─→ Extraer retry_after del error response
   │   └─→ Rotar a siguiente key disponible
   │
   └─→ Reintentar llamada (hasta max_retries)
```

---

## Manejo de Errores y Reintentos

### Estrategia General

1. **Capturar errores específicos**: No usar `except Exception` genérico
2. **Logging detallado**: Incluir contexto y stack trace
3. **Reintentos automáticos**: Para errores recuperables (parsing JSON, timeouts)
4. **Recuperación cuando sea posible**: Rotación de API keys, re-extracción
5. **Propagación apropiada**: Dejar que errores críticos se propaguen

### Reintentos Automáticos en Predicciones

**Implementación**: `llm/predictor.py` → `predict_from_previous_concursos_batch()`

**Cuándo se activa**:
- Error de parsing JSON (`json.JSONDecodeError`, `ValueError`)
- Errores de conexión o timeout (no críticos)

**Comportamiento**:
- **Máximo 3 intentos** por batch
- **Delay incremental**: 2s, 4s, 6s entre intentos
- **Logging detallado**: Posición exacta del error JSON, primeros 500 chars de respuesta
- **Si agotados 3 intentos**:
  - Error marcado como crítico
  - Debug guardado inmediatamente
  - Ejecución de predicciones detenida
  - Mensaje claro indicando posible causa (truncamiento por tokens)

**Límites de tokens dinámicos**:
- Predicciones individuales: 2000 tokens
- Predicciones en batch: 12000 tokens (para 10 concursos con justificaciones completas)
- Extracción de batches: Ajuste dinámico según tamaño (mínimo 12000, máximo 32000)
  - Cálculo: ~800 tokens por concurso × factor de seguridad 1.5
  - Si se detecta truncamiento: aumenta automáticamente (duplica, hasta 32000) y reintenta

### Errores Comunes y Manejo

#### Error 429 (Quota Exceeded)

**Dónde**: `llm/gemini_client.py`, `llm/extractors/llm_extractor.py`, `llm/predictor.py`

**Manejo**:
- Detectar tipo de rate limit (temporal vs diario)
- Rotar a siguiente API key disponible
- Esperar `retry_after` si es rate limit temporal
- Logging sin exponer API keys completas

#### Error de Parsing JSON / JSON Truncado

**Dónde**: 
- `llm/predictor.py` → `_parse_prediction_batch_response()` (predicciones)
- `llm/extractors/llm_extractor.py` → `_call_llm_with_retry()` (extracción)

**Manejo en Predicciones**:
- **Reintentos automáticos** (hasta 3 intentos)
- Log detallado con posición exacta del error
- Mensaje sugerente si es truncamiento por tokens
- Si persiste: error crítico, guardar debug, detener ejecución

**Manejo en Extracción (NUEVO)**:
- **Detección automática de truncamiento**: Verifica `finishReason == "MAX_TOKENS"` o JSON incompleto
- **Aumento automático de tokens**: Duplica `maxOutputTokens` (hasta 32000, límite máximo)
- **Reintento automático**: Hasta 3 aumentos de tokens con reintentos automáticos
- **Sin pérdida de datos**: El sistema garantiza que no se aceptan respuestas truncadas
- Si se alcanza el límite máximo (32000) y aún está truncado: excepción clara indicando que el batch es demasiado grande

**Nota**: Con Structured Outputs de Gemini, estos errores son raros pero posibles si el límite de tokens es insuficiente. El sistema ahora los maneja automáticamente.

#### Error de Scraping

**Dónde**: `crawler/scraper.py`, `services/extraction_service.py`

**Manejo**:
- Continuar con siguiente URL
- Registrar error en debug
- No detener el proceso completo

### Logging de Errores

Siempre incluir:
- Contexto (URL, página, batch, etc.)
- Stack trace para errores inesperados
- Información de recuperación si aplica
- Emojis para identificación rápida (❌ errores, ⚠️ warnings)

**Tracking de Errores**:
- Errores registrados en `debug_info` con información completa
- Incluyen timestamp, contexto, tipo, mensaje y traceback
- Se guardan en archivos de debug para análisis posterior

---

## Extracción Determinística vs LLM

### Estrategia: "Determinístico Primero, LLM como Fallback"

El sistema utiliza una estrategia de **extracción determinística primero, LLM como fallback** para optimizar costos y tiempo. Solo se llama al LLM cuando la extracción determinística no puede obtener los datos necesarios.

### ¿Cuándo se llama al LLM?

El LLM (Gemini API) se llama **SOLO** en los siguientes casos:

#### 1. Extracción Inicial desde Listados (Fase 2)
- **Cuándo**: Al procesar batches de páginas de listado de concursos
- **Por qué**: Necesita extraer múltiples concursos de una sola página
- **Qué extrae**: Nombre, fechas, organismo, financiamiento, descripción, subdirección
- **Optimización**: Si se encontraron datos determinísticos, se prefieren sobre los del LLM

#### 2. Enriquecimiento de Páginas Individuales (Fase 4)
- **Cuándo**: Después de scrapear páginas individuales de cada concurso
- **Por qué**: Completar información faltante (nombre, fechas, descripción, etc.)
- **Optimización**: 
  - Si el nombre se extrajo determinísticamente, se usa ese
  - Si las fechas se extrajeron determinísticamente, se usan esas
  - El LLM solo completa campos que faltan

#### 3. Reintento de Fechas (Fase 5)
- **Cuándo**: Si un concurso aún no tiene `fecha_cierre` después del enriquecimiento
- **Por qué**: Segundo intento focalizado solo en fechas
- **Optimización**: Solo se llama si realmente faltan fechas

#### 4. Reparación de Concursos Incompletos
- **Cuándo**: Al usar el botón "Revisar y reparar concursos incompletos"
- **Por qué**: Intentar completar datos faltantes de concursos problemáticos
- **Optimización**: Usa extracción determinística primero

### ¿Cuándo NO se llama al LLM?

1. **Si se extrajeron nombre y fechas determinísticamente**: El LLM se llama pero sus resultados se complementan con los determinísticos
2. **Si el concurso está suspendido y se detectó por URL**: No se scrapea ni se llama al LLM
3. **Si el concurso está suspendido y se detectó por contenido**: Se marca como suspendido sin necesidad de LLM

### Extracción Determinística Implementada

#### Nombre del Concurso
- Se extrae desde:
  - Tag `<title>` del HTML (removiendo sufijos como " - ANID")
  - Meta tag `og:title`
  - Primer `<h1>` en el contenido principal
  - Primer heading en Markdown (`#` o `##`)

#### Fechas de Apertura y Cierre
- Se buscan patrones en el Markdown:
  - "Inicio: " o "Apertura: " seguido de fecha
  - "Cierre: " o "Fecha de cierre: " seguido de fecha
  - Variaciones con `**` (markdown bold)

#### Estado Suspendido
- Se detecta desde:
  - URL que contiene "concurso-suspendido"
  - Texto "concurso suspendido" en HTML/Markdown

### Estadísticas de Optimización

En un scraping típico de ANID con ~400 concursos:
- **Sin optimización**: ~400-800 llamadas al LLM (dependiendo de batches)
- **Con optimización**: ~200-400 llamadas al LLM (reducción del 50% aproximadamente)

La reducción real depende de:
- Cuántos concursos tienen fechas en formato estándar
- Cuántos concursos tienen nombre en `<title>` o `og:title`
- Cuántos concursos están suspendidos

### Nota sobre Crawl4AI

**Crawl4AI NO es una llamada al LLM**. Es un proceso de scraping web tradicional que:
- Obtiene el contenido HTML de las páginas
- Lo convierte a Markdown
- No realiza llamadas a APIs de LLM

---

## Decisiones de Diseño

### 1. ¿Por qué Pydantic?

- Validación automática de tipos
- Serialización/deserialización JSON
- Documentación integrada
- Facilita desarrollo y debugging

### 2. ¿Por qué Separar Cliente API de Extractor?

- **Cliente API**: Solo comunicación, fácil de testear con mocks
- **Extractor**: Lógica de negocio, puede cambiar sin afectar cliente
- Permite cambiar de LLM sin reescribir toda la lógica

### 3. ¿Por qué Service Layer?

- Separa UI de lógica de negocio
- Facilita testing
- Permite reutilizar lógica en otros contextos (CLI, API, etc.)

### 4. ¿Por qué Batches en Extracción?

- Reduce número de llamadas al LLM
- Optimiza costo y tiempo
- Mejora contexto para el LLM (ve múltiples páginas juntas)
- Límite de 250,000 caracteres por batch (configurable)

### 5. ¿Por qué Batching en Predicciones?

**Problema**: Procesar 300 concursos individualmente = 300 llamadas al LLM (lento, costoso, riesgo de rate limits).

**Solución**: Agrupar en batches de 10 concursos por llamada.

- **Eficiencia**: Reduce ~10x el número de requests (300 → ~30 llamadas)
- **Precisión**: Cada concurso se analiza de forma independiente dentro del batch
- **Tokens optimizados**: 12000 tokens para batches (vs 2000 para individuales)
- **Batches consistentes**: Filtrado previo garantiza batches de exactamente 10 concursos
- **Reintentos automáticos**: Hasta 3 intentos para errores de parsing JSON
- **Filtrado previo**: Detecta `self_reference` antes del LLM (ahorra tokens)

**Balance tokens/precisión**:
- 10 concursos por batch es punto óptimo entre eficiencia y capacidad de análisis
- Cada concurso mantiene su bloque propio con datos completos
- LLM recibe instrucciones explícitas para análisis independiente

### 6. ¿Por qué Rotación de API Keys?

- Maneja límites de cuota automáticamente
- Permite escalar sin intervención manual
- Tracking de uso por key con estadísticas detalladas
- Persistencia en archivo JSON seguro

### 7. ¿Por qué Structured Outputs?

- Garantiza JSON válido (elimina necesidad de reparar JSON)
- Fuerza al LLM a usar nombres de campos exactos del schema
- Reduce errores de parsing y mapeo
- Permite excluir campos calculados del schema enviado al LLM

### 8. ¿Por qué Archivos de Debug?

- Facilita debugging rápido de ejecuciones
- Incluye toda la información relevante en un solo archivo
- Permite revisar contenido raw y procesado
- Útil para auditoría y mejora continua

### 9. ¿Por qué Detección y Recuperación Automática de Pérdida de Datos?

- **Problema**: El LLM puede omitir concursos en batches grandes
- **Solución**: Sistema de detección multi-nivel
  - Por batch: Detecta cuando se extraen menos de 4-5 concursos por página
  - Total: Valida al final que el promedio sea razonable
- **Recuperación automática**: Re-extrae con modelo más potente cuando se detecta pérdida
- **Beneficios**: Mayor confiabilidad sin intervención manual, usa modelo más potente solo cuando es necesario

### 10. ¿Por qué Extracción Determinística?

- **Problema**: Llamar al LLM para cada concurso es costoso y lento
- **Solución**: Extraer datos determinísticamente cuando están en formato estándar
  - Nombre desde `<title>` o `og:title` (muy común)
  - Fechas desde patrones "Inicio:", "Cierre:" (estándar en ANID)
  - Estado suspendido desde URL o contenido
- **Beneficios**: 
  - Reducción del ~50% en llamadas al LLM
  - Menor costo y tiempo de procesamiento
  - Mayor precisión (datos determinísticos son más confiables)
- **Fallback**: Si no se pueden extraer determinísticamente, se usa LLM

### 11. ¿Por qué el Estado NO se calcula por LLM?

- **Razón**: El estado ("Abierto", "Cerrado", "Suspendido", "Próximo") se puede calcular determinísticamente
- **Cálculo determinístico**:
  - Si `fecha_cierre < hoy` → "Cerrado"
  - Si `fecha_cierre >= hoy` → "Abierto"
  - Si `fecha_apertura > hoy` → "Próximo"
  - Si URL contiene "concurso-suspendido" o contenido dice "suspendido" → "Suspendido"
- **Beneficios**: 
  - Elimina carga cognitiva innecesaria del LLM
  - Reduce costos (no se envía campo `estado` en el schema)
  - Mayor precisión (siempre actualizado según fecha actual)
- **Implementación**: El campo `estado` se elimina del schema JSON enviado al LLM

---

## Manejo de Múltiples Versiones de Predicciones

### Comportamiento Actual

El sistema actualmente guarda predicciones en `utils/file_manager.py` mediante la función `save_predictions()`.

**Lógica de deduplicación:**
- Las predicciones se identifican únicamente por `concurso_url`
- Si ya existe una predicción para una URL, **NO se agrega una nueva predicción**
- El sistema evita duplicados basándose únicamente en la URL del concurso

### Escenario: Predicción 2026 vs Versión 2024

**Pregunta:** ¿Qué pasaría si ya se predijo una versión "2026" para un concurso de fecha "2025", y el sistema encuentra la versión "2024" y trata de realizar una predicción?

**Respuesta:**

1. **Si la versión 2024 y 2026 comparten la misma URL:**
   - El sistema **NO creará una nueva predicción** para la versión 2024
   - La predicción existente para 2026 se mantendrá
   - La versión 2024 será ignorada en el proceso de guardado

2. **Si la versión 2024 y 2026 tienen URLs diferentes:**
   - El sistema **SÍ creará una nueva predicción** para la versión 2024
   - Ambas predicciones coexistirán en el archivo de predicciones
   - Esto podría resultar en múltiples predicciones para el mismo concurso (diferentes versiones)

### Limitaciones Actuales

1. **No hay detección de versiones del mismo concurso:**
   - El sistema no identifica que "Concurso X 2024" y "Concurso X 2026" son versiones del mismo concurso
   - Solo se basa en la URL para evitar duplicados

2. **No hay gestión de versiones múltiples:**
   - Si un concurso tiene múltiples versiones con URLs diferentes, se crearán múltiples predicciones
   - No hay lógica para mantener solo la predicción más reciente o relevante

3. **No hay validación de coherencia temporal:**
   - El sistema no valida si una predicción para 2024 tiene sentido cuando ya existe una para 2026
   - No hay lógica para priorizar predicciones más recientes

### Mejoras Futuras Sugeridas

#### Opción 1: Detección de Versiones por Nombre
- Implementar lógica de similitud de nombres (ya existe en `utils/concurso_similarity.py`)
- Si dos concursos tienen nombres similares (>80% similitud), tratarlos como versiones del mismo concurso
- Mantener solo la predicción más reciente

#### Opción 2: Identificador de Concurso Base
- Agregar un campo `concurso_base_id` que identifique el concurso independientemente del año
- Normalizar nombres removiendo años (ej: "Concurso X 2024" → "Concurso X")
- Agregar lógica para mantener solo una predicción activa por `concurso_base_id`

#### Opción 3: Sistema de Versiones Explícito
- Agregar un campo `version` o `año` a las predicciones
- Permitir múltiples predicciones para el mismo concurso, pero marcadas con su versión
- Implementar UI para mostrar todas las versiones de un concurso

#### Opción 4: Validación Temporal
- Antes de guardar una nueva predicción, verificar si existe una predicción más reciente
- Si existe una predicción para un año futuro (ej: 2026), no permitir predicciones para años anteriores (ej: 2024)
- O permitir ambas pero marcar la más antigua como "obsoleta"

**Ubicación de lógica actual:** `utils/file_manager.py`, función `save_predictions()`, líneas 561-567

---

## Guía para Desarrollo

### Al Modificar Código

1. **Leer el archivo completo** antes de modificar
2. **Entender el contexto** del cambio
3. **Mantener el estilo** existente
4. **Actualizar documentación** si es necesario
5. **Verificar imports** y dependencias
6. **Eliminar código obsoleto** en lugar de comentarlo

### Al Agregar Funcionalidad

1. **Identificar el módulo correcto** según responsabilidad
2. **Seguir patrones existentes** en ese módulo
3. **Agregar logging** apropiado
4. **Manejar errores** robustamente
5. **Implementar reintentos** si es apropiado
6. **Actualizar esta documentación** si es necesario

### Cómo Agregar un Nuevo Campo al Modelo Concurso

1. **Editar `models/concurso.py`**: Agregar campo al modelo
2. **Actualizar `llm/prompts.py`**: Agregar instrucciones para extraer el campo
3. **Actualizar validación** si es necesario
4. **Actualizar mapeo** en `llm/extractors/llm_extractor.py`

### Cómo Agregar Soporte para un Nuevo Sitio Web

1. **Identificar tipo de paginación**: Dinámica, tradicional, o sin paginación
2. **Si es paginación dinámica**: Agregar lógica en `scraper.py`
3. **Identificar estructura de páginas individuales**
4. **Actualizar prompts** si el sitio tiene formato diferente
5. **Agregar URL semilla** en `config.py`

---

## Extensibilidad

### Agregar un Nuevo LLM

1. Crear nuevo cliente en `llm/`
2. Crear extractor que use el nuevo cliente
3. Modificar `ExtractionService` para aceptar tipo de extractor

### Agregar un Nuevo Formato de Exportación

1. Agregar función en `utils/file_manager.py`
2. Exportar en `utils/__init__.py`
3. Agregar opción en UI (`main.py`)

---

## Convenciones de Código

### Nombres

- **snake_case** para archivos, módulos, funciones y variables
- **PascalCase** para clases
- **UPPER_CASE** para constantes

### Imports

Orden:
1. Standard library
2. Third-party
3. Local application

### Documentación

- **Docstrings** en todas las clases y funciones públicas
- Formato Google style

### Logging

- Usar `logging.getLogger(__name__)` en cada módulo
- Niveles apropiados: DEBUG, INFO, WARNING, ERROR

---

## Recursos y Referencias

- **Configuración**: Ver `config.py` para todas las opciones
- **Logs**: Revisar logs para debugging
- **Ejemplos**: Ver `models/concurso.py` para ejemplos de datos

---

## Preparación para Despliegue en AWS (modo práctica)

- Objetivo: despliegue rápido en EC2 con Docker y tareas programadas simples (cron) para ANID.
- Separación de vistas (recomendado):
  - **Vista Pública/Visualización**: lista unificada de todos los concursos (todas las fuentes), con filtros avanzados (estado, organismo, subdirección, búsqueda, fecha de apertura/cierre, fuente, “incompletos”), sin acciones destructivas.
  - **Vista Administración**: ejecutar scraping manual, ejecutar predicciones manuales, limpiar historiales/predicciones, agregar concursos manuales. Acceso único (sin hardening estricto para este caso de práctica).
- Automatizaciones mínimas:
  - Cron diario en EC2: script `scripts/run_daily_anid.sh` (usa `scripts/daily_anid.py`) que hace scrape ANID (máx 2 páginas) y luego predicciones ANID.
  - Se encapsula en un único script diario (scrape→repair implícito→predict).
- Dockerización básica:
  - `Dockerfile` multietapa simple (builder + runtime slim), instala dependencias y expone `streamlit run main.py --server.port 8501 --server.address 0.0.0.0`.
  - `docker-compose.yml` (opcional) para desarrollo local (servicio app + volumen `data/` persistente).
  - Variables mínimas por entorno: `API_KEYS_PATH`, `DATA_DIR` (montada en volumen), `PORT`.
- Despliegue en EC2 (mínimo):
  - Instalar Docker + docker-compose.
  - Copiar `.env` (claves Gemini), montar `data/` en volumen persistente.
  - Abrir puerto 8501 o mapear a 80/443 detrás de un ALB/Nginx (opcional).
- Consideraciones de estabilidad:
  - Locks por sitio ya existen; validar limpieza de locks en cron (stale 5 min).
  - Backups simples: snapshot periódico de `data/` (history, predictions, raw_pages, debug).
  - Monitoreo ligero: logs stdout de Docker + rotación (log-driver json-file con `max-size`/`max-file`).
- Pendientes para futura producción (no crítico para la práctica):
  - Autenticación básica en vista de administración.
  - HTTPS (ALB o Nginx con cert).
  - Healthcheck simple (`streamlit` no expone; añadir endpoint lightweight en el futuro).
  - Métricas (Prometheus/OpenTelemetry) opcional.

## Cambios Recientes

### v4.6 - Preparación AWS y separación de vistas (2025-12-17)

- Añadida sección de despliegue básico en AWS (EC2 + Docker + cron diario ANID con predicción automática).
- Recomendación de separar vistas: una de visualización (solo lectura, todos los concursos con filtros) y otra de administración (scraping, predicciones manuales, limpieza, alta manual).
- Notas operativas mínimas: cron diario, backup de `data/`, uso de locks existentes, logging sencillo.

### v4.5 - Concursos Manuales en pestaña dedicada (2025-12-17)

- Nueva pestaña de UI “📝 Concursos Manuales”: lista todos los concursos guardados en `manual.local` y sus predicciones deterministas.
- Formulario con validación estricta (YYYY-MM-DD) y regla de negocio: la fecha de cierre debe ser posterior a la de apertura; no se restringe pasado/futuro.
- Cada alta manual guarda en historial `manual.local`, cachea el contenido (markdown/html básico del formulario) y asigna predicción automática (+1 año desde la fecha de apertura), sin usar el flujo de predicciones ni el LLM.
- El flujo general de predicciones excluye los concursos manuales; su predicción se genera al momento de crearlos.

### v4.4 - Estrategia CentroEstudios y predicción habilitada (2025-12-17)

- Estrategia específica `centro_estudios_strategy.py`: sin paginación, sin LLM, timeout corto y JS trivial; extracción determinista del bloque “Convocatoria actual (FONIDE NN)” con nombre adaptable (FONIDE 16/17/…), fecha de consultas (apertura) y fecha de postulaciones (cierre).
- Scraper salta waits pesados cuando la estrategia es CentroEstudios (evita demoras >1 min).
- Predicciones: UI permite concursos cerrados sin `previous_concursos` cuando el dominio está habilitado (CentroEstudios) para permitir predicción anual determinista.
- Cache e historial guardan siempre HTML/MD completos del concurso único de CentroEstudios.
- UI de scraping ahora forza **un solo sitio por corrida** y el servicio filtra URLs de dominios distintos para evitar mezclas.
- Locks de scraping: se consideran obsoletos a los 5 minutos (`stale_seconds=300`) para limpiar locks viejos automáticamente.
- **Reparación automática post-scrape**: tras cada extracción, si quedan concursos incompletos en el historial, se ejecuta `repair_incomplete_concursos` sobre esas URLs, usando cache HTML/MD y evitando re-scrapear cuando es posible.

### v4.3 - Resiliencia ante concurrencia scraping/predicción (2025-12-16)

- **Locks por sitio/operación**: `utils/lock_manager.py` con lockfiles en `data/locks`.
- **Scraping**: `ExtractionService.extract_from_urls` adquiere lock `scrape` por sitio; limpia locks obsoletos y evita ejecuciones simultáneas.
- **Predicciones**: `PredictionService.generate_predictions` detecta lock `scrape` activo y devuelve mensaje de espera en lugar de fallar.
- **Objetivo**: Evitar crashes cuando se lanzan predicciones mientras hay scraping en curso.

### v4.2 - Predicciones sin campo de confianza (2025-12-16)

- **Eliminado**: Campo y lógica de `confianza` en predicciones.
- **Simplificado**: Prompts y modelos (`PrediccionConcurso`, batches) sin confianza.
- **Servicios**: `prediction_service` ya no calcula ni solicita confianza a LLM; solo valida fechas y justificaciones.
- **Documentación**: ARQUITECTURA.md actualizado para reflejar flujo sin confianza.

### v4.1 - Cache completo de páginas individuales (2025-12-16)

- Guardado de HTML/Markdown sin compresión para cada URL de concurso (`data/raw_pages/<site>/<slug>.html/.md`) con índice por sitio.
- Reparaciones/predicciones leen primero desde cache; cualquier re-scrape sobrescribe la entrada de esa URL.
- Mantiene claves por sitio+URL para respetar la arquitectura multi-sitio.
- Documentación auditada y actualizada con la nueva política de cache.

### v4.0 - Sistema Multi-Sitio con Estrategias (2025-12-16)

- **Refactorización completa**: Sistema transformado de "extractor específico de ANID" a "extractor genérico de concursos gubernamentales"
- **Implementación de Strategy Pattern**: 
  - Nuevo módulo `crawler/strategies/` con clases base y estrategias específicas
  - `ANIDStrategy`: Encapsula toda la lógica específica de ANID
  - `GenericStrategy`: Fallback para sitios estándar sin lógica específica
- **Módulo de paginación**: 
  - Nuevo módulo `crawler/pagination/` con clases base y específicas
  - `AnidPagination`: Paginación dinámica específica de ANID
  - `GenericPagination`: Paginación tradicional para sitios estándar
- **Módulo de extractores**: 
  - Nuevo módulo `utils/extractors/` con clases base y específicas
  - `AnidExtractor`: Extracción de "Concursos anteriores" específica de ANID
  - `GenericExtractor`: Extractor genérico (retorna lista vacía)
- **Configuración por sitio**: 
  - Nuevo módulo `config/` con `global_config.py` y `sites.py`
  - Configuración centralizada por sitio en `config/sites.py`
  - `config.py` mantiene compatibilidad como wrapper
- **Refactorización de módulos existentes**:
  - `WebScraper`: Ahora usa estrategias, método `scrape_url_with_pagination()` genérico
  - `ExtractionService`: Eliminada toda lógica hardcodeada de ANID, usa estrategias
  - `main.py`: Usa configuración centralizada para mapeo de sitios
- **Separación total**: Código específico de ANID completamente aislado en módulos específicos
- **Documentación completa**: Sección detallada "Cómo Agregar un Nuevo Sitio" en ARQUITECTURA.md
- **Extensibilidad**: Agregar nuevo sitio ahora requiere solo crear nueva clase Strategy

### v3.5 - Reintento Automático con Aumento de Tokens y Mejoras en Extracción (2025-12-16)

- **Agregado**: Reintento automático con aumento de tokens en extracción
  - Si el JSON está truncado, detecta automáticamente (`finishReason == "MAX_TOKENS"` o JSON incompleto)
  - Aumenta `maxOutputTokens` automáticamente (duplica, hasta 32000)
  - Reintenta hasta 3 veces con tokens aumentados
  - **Garantiza cero pérdida de datos**: No acepta respuestas truncadas
- **Mejorado**: Extracción de nombres en `previous_concursos`
  - Filtra textos genéricos como "Ver más", "Leer más"
  - Busca en atributos `title` y `data-*` del link cuando el texto es genérico
  - Prioriza múltiples fuentes para obtener el nombre real del concurso
- **Mejorado**: Extracción de años en `previous_concursos`
  - Extrae años desde múltiples fuentes: nombre, fecha_apertura, fecha_cierre, URL
  - Reduce casos de años `null`
- **Mejorado**: Manejo de errores de estructura inválida
  - Justificaciones detalladas que incluyen valores específicos del LLM
  - Información completa en debug para diagnóstico
- **Agregado**: Detección automática de última página en paginación
  - Verifica existencia del botón ">" (siguiente) antes y después de cada página
  - Si no existe, detecta automáticamente la última página y detiene el scraping
  - Evita intentos innecesarios de procesar páginas inexistentes
- **Aumentado**: `maxOutputTokens` inicial para batches grandes
  - Cálculo mejorado: ~800 tokens por concurso × factor 1.5
  - Mínimo de 12000 para batches grandes (antes 8000)
  - Mínimo de 20000 para batches >150000 caracteres

### v3.4 - Optimización de Batches y Aumento de Tokens (2025-12-16)

- **Mejorado**: Filtrado previo de concursos no predecibles antes de crear batches
  - Los self_reference se filtran ANTES de crear batches
  - Garantiza que cada batch tenga exactamente 10 concursos (excepto el último)
  - Mejora la optimización al mantener batches de tamaño consistente
- **Aumentado**: `maxOutputTokens` para predicciones batch de 8000 a 12000
  - Proporciona más espacio para justificaciones completas
  - Reduce riesgo de truncamiento en batches grandes
- **Documentado**: Actualizado flujo de predicción masiva en ARQUITECTURA.md

### v3.3 - Extracción Determinística y Optimizaciones (2025-12-16)

- **Agregado**: Extracción determinística de nombre del concurso
  - Desde `<title>`, `og:title`, `<h1>`, o primer heading en Markdown
  - Reduce llamadas al LLM cuando el nombre está disponible en metadatos
- **Agregado**: Extracción determinística de fechas
  - Desde patrones "Inicio:", "Cierre:" en Markdown
  - Reduce llamadas al LLM cuando las fechas están en formato estándar
- **Mejorado**: Preferencia de datos determinísticos sobre LLM
  - Si se extrajeron determinísticamente, se prefieren sobre los del LLM
  - El LLM solo completa campos faltantes
- **Eliminado**: Cálculo de estado por LLM
  - El estado ahora se calcula siempre determinísticamente desde fechas
  - Campo `estado` eliminado del schema JSON enviado al LLM
- **Documentado**: Sección completa sobre "Extracción Determinística vs LLM"
  - Explica cuándo se llama al LLM y cuándo no
  - Estadísticas de optimización (~50% reducción en llamadas)
- **Documentado**: Manejo de múltiples versiones de predicciones
  - Comportamiento actual y limitaciones
  - Sugerencias para mejoras futuras

### v3.2 - Optimización de Tokens y Reintentos Automáticos (2025-12-16)

- **Agregado**: Límites de tokens dinámicos para predicciones
  - Predicciones individuales: 2000 tokens
  - Predicciones en batch: 8000 tokens (para acomodar 10 concursos con justificaciones completas)
- **Agregado**: Reintentos automáticos en predicciones batch
  - Hasta 3 intentos para errores de parsing JSON
  - Delay incremental (2s, 4s, 6s) entre intentos
  - Logging detallado con posición exacta del error
  - Detención controlada si agotados 3 intentos (guarda debug antes de detener)
- **Mejorado**: Mensajes de error más informativos
  - Sugerencias cuando el error es por truncamiento de tokens
  - Contexto completo para debugging

### v3.1 - Batching de Predicciones (2025-12-16)

- **Agregado**: Sistema de batching para predicciones masivas
  - `PredictionService.generate_predictions()` agrupa concursos en batches de 10
  - Nuevo método `ConcursoPredictor.predict_from_previous_concursos_batch()`
  - Nuevos modelos: `PrediccionConcursoBatchItem` y `PrediccionBatchResponse`
  - Reduce ~10x el número de llamadas al LLM
- **Optimizado**: Prompts de predicción batch
  - Lenguaje afirmativo/declarativo
  - Ejemplos conceptuales sin overfitting
- **Mejorado**: Filtrado de casos no predecibles
  - Detección de `self_reference` antes del LLM

### v2.3 - Limpieza Completa de Código

- **Eliminado**: Funciones obsoletas de gestión de API keys individuales
- **Sistema unificado**: `APIKeyManager` es la única forma de gestionar API keys
- **Limpieza**: Eliminados archivos temporales y código duplicado

---

**Última actualización**: 2025-12-17  
**Versión de arquitectura**: 4.4
