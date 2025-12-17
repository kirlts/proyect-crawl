"""
Módulo de configuración centralizada.

Exporta configuraciones globales y por sitio.
"""

from config.global_config import (
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
)

from config.sites import (
    SEED_URLS,
    SITE_DOMAINS,
    SITE_NAME_MAPPING,
    SITE_CONFIGS,
    get_site_config,
    get_site_name_for_history,
)

__all__ = [
    # Global config
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
    # Sites config
    "SEED_URLS",
    "SITE_DOMAINS",
    "SITE_NAME_MAPPING",
    "SITE_CONFIGS",
    "get_site_config",
    "get_site_name_for_history",
]
