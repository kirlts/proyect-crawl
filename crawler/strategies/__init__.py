"""
Sistema de registro de estrategias de scraping por sitio.

Permite registrar estrategias específicas para diferentes sitios y
obtener la estrategia apropiada según la URL o nombre del sitio.
"""

from typing import Dict, Type, Optional
from urllib.parse import urlparse
import logging

from crawler.strategies.base_strategy import ScrapingStrategy
from crawler.strategies.generic_strategy import GenericStrategy

logger = logging.getLogger(__name__)

# Registro de estrategias por dominio
STRATEGY_REGISTRY: Dict[str, Type[ScrapingStrategy]] = {
    # Las estrategias específicas se registran aquí cuando se importan
    # Ejemplo: "anid.cl": ANIDStrategy
}


def register_strategy(domain: str, strategy_class: Type[ScrapingStrategy]) -> None:
    """
    Registra una estrategia para un dominio específico.
    
    Args:
        domain: Dominio del sitio (ej: "anid.cl")
        strategy_class: Clase de estrategia que implementa ScrapingStrategy
    """
    STRATEGY_REGISTRY[domain] = strategy_class
    logger.info(f"✅ Estrategia registrada: {domain} -> {strategy_class.__name__}")


def get_strategy_for_url(url: str) -> ScrapingStrategy:
    """
    Retorna la estrategia apropiada para una URL.
    
    Busca en el registro por dominio. Si no encuentra una estrategia específica,
    retorna GenericStrategy.
    
    Args:
        url: URL del sitio
        
    Returns:
        Instancia de ScrapingStrategy (específica o GenericStrategy)
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        
        # Buscar estrategia específica
        if domain in STRATEGY_REGISTRY:
            strategy_class = STRATEGY_REGISTRY[domain]
            return strategy_class()
        
        # Fallback a GenericStrategy
        logger.debug(f"No se encontró estrategia específica para {domain}, usando GenericStrategy")
        return GenericStrategy()
        
    except Exception as e:
        logger.error(f"Error al obtener estrategia para URL {url}: {e}")
        return GenericStrategy()


def get_strategy_for_site(site_name: str) -> ScrapingStrategy:
    """
    Retorna la estrategia apropiada para un nombre de sitio.
    
    Busca en el registro por nombre de sitio. Si no encuentra una estrategia específica,
    retorna GenericStrategy.
    
    Args:
        site_name: Nombre del sitio (ej: "anid.cl")
        
    Returns:
        Instancia de ScrapingStrategy (específica o GenericStrategy)
    """
    # Normalizar nombre (remover www. si existe)
    normalized = site_name.replace("www.", "")
    
    # Buscar estrategia específica
    if normalized in STRATEGY_REGISTRY:
        strategy_class = STRATEGY_REGISTRY[normalized]
        return strategy_class()
    
    # Fallback a GenericStrategy
    logger.debug(f"No se encontró estrategia específica para {site_name}, usando GenericStrategy")
    return GenericStrategy()


# Importar estrategias específicas para que se registren automáticamente
# Esto se hace al final del archivo para evitar importaciones circulares
def _register_all_strategies():
    """
    Registra todas las estrategias específicas disponibles.
    
    Esta función se llama cuando se importa el módulo para registrar
    automáticamente las estrategias.
    """
    try:
        # Importar ANIDStrategy
        from crawler.strategies.anid_strategy import ANIDStrategy
        register_strategy("anid.cl", ANIDStrategy)
        register_strategy("www.anid.cl", ANIDStrategy)
        # Importar Centro Estudios MINEDUC
        from crawler.strategies.centro_estudios_strategy import CentroEstudiosStrategy
        register_strategy("centroestudios.mineduc.cl", CentroEstudiosStrategy)
        register_strategy("www.centroestudios.mineduc.cl", CentroEstudiosStrategy)
    except ImportError:
        # Las estrategias específicas pueden no estar disponibles aún
        pass

# Llamar a la función de registro al importar el módulo
_register_all_strategies()

__all__ = [
    "ScrapingStrategy",
    "GenericStrategy",
    "STRATEGY_REGISTRY",
    "register_strategy",
    "get_strategy_for_url",
    "get_strategy_for_site",
]

