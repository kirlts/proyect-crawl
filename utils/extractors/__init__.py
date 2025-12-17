"""
Módulo de extractores específicos por sitio.

Cada sitio puede tener su propio extractor para datos específicos
como "concursos anteriores" u otra información estructurada.
"""

from utils.extractors.base_extractor import BaseExtractor
from utils.extractors.generic_extractor import GenericExtractor

__all__ = [
    "BaseExtractor",
    "GenericExtractor",
]

