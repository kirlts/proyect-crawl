"""
Extractor genérico (sin lógica específica).
"""

from typing import List, Dict, Any
from utils.extractors.base_extractor import BaseExtractor


class GenericExtractor(BaseExtractor):
    """
    Extractor genérico que no extrae información específica.
    
    Retorna lista vacía por defecto. Los extractores específicos
    sobrescriben este comportamiento.
    """
    
    def extract_previous_concursos(
        self,
        html: str,
        url: str
    ) -> List[Dict[str, Any]]:
        """
        No extrae información (retorna lista vacía).
        
        Args:
            html: Contenido HTML (no usado)
            url: URL (no usado)
            
        Returns:
            Lista vacía
        """
        return []

