"""
Configuración centralizada para el MVP de Buscador de Oportunidades de Financiamiento

NOTA: Este archivo mantiene compatibilidad hacia atrás. La configuración real
está en el módulo config/ (config/global_config.py y config/sites.py).
"""

# Importar desde el nuevo módulo de configuración para mantener compatibilidad
from config import (
    CRAWLER_CONFIG,
    AVAILABLE_MODELS,
    GEMINI_CONFIG,
    DATA_DIR,
    RAW_DIR,
    PROCESSED_DIR,
    CACHE_DIR,
    HISTORY_DIR,
    PREDICTIONS_DIR,
    DEBUG_SCRAPING_DIR,
    DEBUG_PREDICTIONS_DIR,
    DEBUG_INDIVIDUAL_PREDICTIONS_DIR,
    RAW_PAGES_DIR,
    RAW_PAGES_INDEX_DIR,
    EXTRACTION_CONFIG,
    SEED_URLS,
)

__all__ = [
    "CRAWLER_CONFIG",
    "AVAILABLE_MODELS",
    "GEMINI_CONFIG",
    "DATA_DIR",
    "RAW_DIR",
    "PROCESSED_DIR",
    "CACHE_DIR",
    "HISTORY_DIR",
    "PREDICTIONS_DIR",
    "DEBUG_SCRAPING_DIR",
    "DEBUG_PREDICTIONS_DIR",
    "DEBUG_INDIVIDUAL_PREDICTIONS_DIR",
    "RAW_PAGES_DIR",
    "RAW_PAGES_INDEX_DIR",
    "EXTRACTION_CONFIG",
    "SEED_URLS",
]

