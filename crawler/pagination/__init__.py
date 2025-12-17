"""
Módulo de paginación para diferentes tipos de sitios.

Proporciona clases base y implementaciones específicas para manejar
paginación dinámica (JavaScript) y tradicional (enlaces HTML).
"""

from crawler.pagination.base_pagination import BasePagination
from crawler.pagination.generic_pagination import GenericPagination

# Importar función legacy desde el módulo raíz para compatibilidad
# La función find_pagination_links está en crawler/pagination.py (archivo raíz del módulo crawler)
import sys
import os
# Agregar el directorio padre al path para importar el módulo legacy
parent_dir = os.path.dirname(os.path.dirname(__file__))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Importar directamente desde el módulo legacy
try:
    from crawler.pagination import find_pagination_links
except ImportError:
    # Si falla, intentar importar desde el archivo directamente
    import importlib.util
    pagination_file = os.path.join(parent_dir, "crawler", "pagination.py")
    if os.path.exists(pagination_file):
        spec = importlib.util.spec_from_file_location("crawler.pagination_legacy", pagination_file)
        pagination_legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pagination_legacy)
        find_pagination_links = pagination_legacy.find_pagination_links
    else:
        # Fallback: definir función vacía si no se encuentra
        def find_pagination_links(html: str, base_url: str):
            return []

__all__ = [
    "BasePagination",
    "GenericPagination",
    "find_pagination_links",
]

