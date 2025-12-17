"""
Clase base abstracta para extractores de datos específicos por sitio.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any


class BaseExtractor(ABC):
    """
    Clase base para extractores de datos específicos.
    
    Cada sitio puede tener su propio extractor para datos como
    "concursos anteriores" u otra información estructurada.
    """
    
    @abstractmethod
    def extract_previous_concursos(
        self,
        html: str,
        url: str
    ) -> List[Dict[str, Any]]:
        """
        Extrae información de concursos anteriores.
        
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
        pass

