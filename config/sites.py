"""
Configuración específica por sitio.

Cada sitio puede tener su propia configuración de Crawl4AI, características
especiales, y otros parámetros específicos.
"""

from typing import Dict, Any, List, Set

# URLs semilla predefinidas para sitios institucionales chilenos
SEED_URLS = {
    "ANID": [
        "https://anid.cl/concursos/",
        # Excluir capital humano según especificación
    ],
    "Centro Estudios MINEDUC": [
        "https://centroestudios.mineduc.cl/",
    ],
    "CNA": [
        "https://www.cnachile.cl/",
    ],
    "DFI MINEDUC": [
        "https://dfi.mineduc.cl/",
    ]
}

# Mapeo de nombres de sitio a dominios
SITE_DOMAINS = {
    "anid.cl": "ANID",
    "www.anid.cl": "ANID",
    "centroestudios.mineduc.cl": "Centro Estudios MINEDUC",
    "cnachile.cl": "www.cnachile.cl",  # Normalizar
    "www.cnachile.cl": "CNA",
    "dfi.mineduc.cl": "DFI MINEDUC",
}

# Mapeo de nombres de sitio a nombres para historial
SITE_NAME_MAPPING = {
    "ANID": "anid.cl",
    "Centro Estudios MINEDUC": "centroestudios.mineduc.cl",
    "CNA": "cnachile.cl",
    "DFI MINEDUC": "dfi.mineduc.cl",
}

# Configuración específica por sitio
SITE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "anid.cl": {
        "display_name": "ANID",
        "organismo": "ANID",
        "crawler_config": {
            # Configuración optimizada para ANID que carga contenido dinámico con JetEngine
            # La espera inteligente se maneja en el hook before_retrieve_html, no con timeouts fijos
            "wait_for": "css:.jet-listing-grid__item",  # Esperar al menos que existan los contenedores
            "wait_until": "domcontentloaded",  # Cargar DOM, luego el hook espera el contenido AJAX
            "scan_full_page": True,  # Hacer scroll completo para cargar contenido lazy
        },
        "features": {
            "dynamic_pagination": True,  # ANID usa paginación dinámica con JavaScript
            "has_previous_concursos": True,  # ANID tiene sección "Concursos anteriores"
        },
        "known_subdirecciones": {
            "capital humano",
            "centros e investigación asociativa",
            "investigación aplicada",
            "proyectos de investigación",
            "redes, estrategia y conocimiento",
            "redes estrategia y conocimiento",
        }
    },
    # Configuraciones genéricas para otros sitios (se pueden sobrescribir con estrategias específicas)
    "centroestudios.mineduc.cl": {
        "display_name": "Centro Estudios MINEDUC",
        "organismo": "MINEDUC",
        "crawler_config": {},
        "features": {
            "dynamic_pagination": False,
            "has_previous_concursos": False,
        },
        "known_subdirecciones": set()
    },
    "cnachile.cl": {
        "display_name": "CNA",
        "organismo": "CNA",
        "crawler_config": {},
        "features": {
            "dynamic_pagination": False,
            "has_previous_concursos": False,
        },
        "known_subdirecciones": set()
    },
    "dfi.mineduc.cl": {
        "display_name": "DFI MINEDUC",
        "organismo": "MINEDUC",
        "crawler_config": {},
        "features": {
            "dynamic_pagination": False,
            "has_previous_concursos": False,
        },
        "known_subdirecciones": set()
    },
}


def get_site_config(domain: str) -> Dict[str, Any]:
    """
    Obtiene la configuración para un dominio específico.
    
    Args:
        domain: Dominio del sitio (ej: "anid.cl")
        
    Returns:
        Diccionario con configuración del sitio o configuración genérica por defecto
    """
    # Normalizar dominio (remover www.)
    normalized_domain = domain.replace("www.", "")
    
    # Buscar configuración específica
    if normalized_domain in SITE_CONFIGS:
        return SITE_CONFIGS[normalized_domain]
    
    # Retornar configuración genérica por defecto
    return {
        "display_name": domain,
        "organismo": "Desconocido",
        "crawler_config": {},
        "features": {
            "dynamic_pagination": False,
            "has_previous_concursos": False,
        },
        "known_subdirecciones": set()
    }


def get_site_name_for_history(display_name: str) -> str:
    """
    Obtiene el nombre del sitio para usar en historial.
    
    Args:
        display_name: Nombre para mostrar (ej: "ANID")
        
    Returns:
        Nombre para historial (ej: "anid.cl")
    """
    return SITE_NAME_MAPPING.get(display_name, display_name.lower())

