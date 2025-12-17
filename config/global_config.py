"""
Configuración global del sistema (no específica de sitio).
"""

# Configuración para Crawl4AI (genérica, no específica de sitio)
# Las estrategias específicas pueden sobrescribir esta configuración
CRAWLER_CONFIG = {
    "headless": True,
    "page_timeout": 120000,  # 120 segundos máximo para navegación (solo como seguridad)
    "wait_for": "css:body",  # Genérico - las estrategias específicas lo sobrescriben
    "word_count_threshold": 5,  # Más bajo para no perder información
    "verbose": False,
    "cache_mode": "BYPASS",  # Cambiar a "ENABLED" para desarrollo
    "scan_full_page": True,  # Hacer scroll completo para cargar contenido lazy
    "wait_until": "domcontentloaded",  # Cargar DOM
    "wait_for_images": False,  # No esperar imágenes para acelerar
}

# Modelos Gemini disponibles con información de Free Tier
AVAILABLE_MODELS = {
    "gemini-2.5-flash-lite": {
        "name": "Gemini 2.5 Flash Lite",
        "description": "Más económico, optimizado para uso a escala",
        "free_tier": True,
        "recommended": True
    },
    "gemini-2.5-flash-lite-preview-09-2025": {
        "name": "Gemini 2.5 Flash Lite Preview",
        "description": "Última versión Flash Lite, alta eficiencia",
        "free_tier": True,
        "recommended": True
    },
    "gemini-2.5-flash": {
        "name": "Gemini 2.5 Flash",
        "description": "Modelo híbrido con razonamiento, ventana de 1M tokens",
        "free_tier": True,
        "recommended": False
    },
    "gemini-2.5-flash-preview-09-2025": {
        "name": "Gemini 2.5 Flash Preview",
        "description": "Última versión Flash, mejor para tareas de alto volumen",
        "free_tier": True,
        "recommended": False
    },
    "gemini-2.0-flash": {
        "name": "Gemini 2.0 Flash",
        "description": "Modelo balanceado, ventana de 1M tokens",
        "free_tier": True,
        "recommended": False
    },
    "gemini-2.5-pro": {
        "name": "Gemini 2.5 Pro",
        "description": "Modelo más potente, excelente para razonamiento complejo",
        "free_tier": True,
        "recommended": False
    }
}

# Configuración para Gemini (modelo por defecto)
GEMINI_CONFIG = {
    "model": "gemini-2.5-flash-lite",  # Modelo recomendado para free tier
    "temperature": 0.1,  # Bajo para consistencia en extracción
    "max_output_tokens": 8000,
}

# Rutas de directorios
DATA_DIR = "data"
RAW_DIR = f"{DATA_DIR}/raw"
PROCESSED_DIR = f"{DATA_DIR}/processed"
CACHE_DIR = f"{DATA_DIR}/cache"
HISTORY_DIR = f"{DATA_DIR}/history"
PREDICTIONS_DIR = f"{DATA_DIR}/predictions"
DEBUG_SCRAPING_DIR = f"{DATA_DIR}/debug/scraping"
DEBUG_PREDICTIONS_DIR = f"{DATA_DIR}/debug/predictions"
DEBUG_INDIVIDUAL_PREDICTIONS_DIR = f"{DATA_DIR}/debug/predictions/individual"
# Almacenamiento completo de páginas individuales (HTML/Markdown) sin compresión
RAW_PAGES_DIR = f"{DATA_DIR}/raw_pages"
RAW_PAGES_INDEX_DIR = RAW_PAGES_DIR  # Índices JSON por sitio se guardan en el mismo directorio raíz

# Configuración de extracción
EXTRACTION_CONFIG = {
    # Reducir tamaño máximo por batch para hacer las llamadas al LLM más robustas
    # y evitar golpear tan rápido los límites de cuota de tokens.
    "batch_size": 250000,  # Caracteres por batch - agrupa múltiples páginas hasta este límite
    "chunk_size": 250000,  # Mantener para compatibilidad, pero usar batch_size
    "max_retries": 3,
    "retry_delay": 2,  # segundos
    "api_timeout": 60,  # Timeout para llamadas a API (segundos) - evita que se quede colgado
    "max_time_per_batch": 300,  # Tiempo máximo por batch (segundos) - 5 minutos
    "max_total_time": None,  # Tiempo máximo total de ejecución (segundos) - None = sin límite
    "continue_on_error": True,  # Continuar procesando aunque falle un batch
    "max_consecutive_failures": 5,  # Máximo de fallos consecutivos antes de abortar
}

