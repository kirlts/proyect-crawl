"""
Extractor específico para ANID.

Extrae información de "Concursos anteriores" de páginas ANID usando
la lógica específica de JetEngine/Elementor.
"""

from typing import List, Dict, Any
from utils.extractors.base_extractor import BaseExtractor

# Importar función legacy desde anid_previous_concursos.py
# Mantener compatibilidad mientras migramos
from utils.anid_previous_concursos import extract_previous_concursos_from_html


class AnidExtractor(BaseExtractor):
    """
    Extractor específico para ANID.
    
    Extrae información de "Concursos anteriores" usando la lógica
    específica de ANID (selectores JetEngine, estructura Elementor).
    """
    
    def extract_previous_concursos(
        self,
        html: str,
        url: str
    ) -> List[Dict[str, Any]]:
        """
        Extrae información de "Concursos anteriores" de una página HTML de ANID.
        
        Args:
            html: Contenido HTML de la página del concurso
            url: URL de la página (para logging)
            
        Returns:
            Lista de diccionarios con información de concursos anteriores:
            [
                {
                    "nombre": "Nombre del concurso anterior",
                    "fecha_apertura": "YYYY-MM-DD" o None,
                    "fecha_cierre": "YYYY-MM-DD" o None,
                    "fecha_apertura_original": "texto original",
                    "fecha_cierre_original": "texto original",
                    "url": "URL del concurso anterior" o None,
                    "año": año extraído del nombre o None
                },
                ...
            ]
        """
        # Usar la función legacy que ya tiene toda la lógica específica de ANID
        return extract_previous_concursos_from_html(html, url)

