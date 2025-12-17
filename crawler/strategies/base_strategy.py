"""
Clase base abstracta para estrategias de scraping por sitio.

Cada sitio puede tener su propia estrategia que implementa esta interfaz,
permitiendo lógica específica para paginación, extracción de datos, etc.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Set, Optional
from crawl4ai import AsyncWebCrawler


class ScrapingStrategy(ABC):
    """
    Clase base abstracta para estrategias de scraping.
    
    Cada sitio implementa esta interfaz para proporcionar:
    - Configuración específica de Crawl4AI
    - Lógica de paginación (dinámica o tradicional)
    - Extracción de datos específicos (ej: "concursos anteriores")
    - Información del organismo
    """
    
    @property
    @abstractmethod
    def site_name(self) -> str:
        """
        Nombre del sitio (dominio normalizado).
        
        Returns:
            Nombre del sitio (ej: "anid.cl")
        """
        pass
    
    @property
    @abstractmethod
    def site_display_name(self) -> str:
        """
        Nombre para mostrar del sitio.
        
        Returns:
            Nombre para mostrar (ej: "ANID")
        """
        pass
    
    @abstractmethod
    def get_crawler_config(self) -> Dict[str, Any]:
        """
        Retorna configuración específica de Crawl4AI para este sitio.
        
        Esta configuración se combina con la configuración global.
        Los valores aquí retornados sobrescriben los valores globales.
        
        Returns:
            Diccionario con configuración de Crawl4AI
        """
        pass
    
    @abstractmethod
    def supports_dynamic_pagination(self) -> bool:
        """
        Indica si este sitio requiere paginación dinámica (JavaScript).
        
        Returns:
            True si requiere paginación dinámica, False si usa paginación tradicional
        """
        pass
    
    @abstractmethod
    async def scrape_with_pagination(
        self,
        url: str,
        max_pages: int,
        crawler: AsyncWebCrawler,
        base_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Scrapea una URL con paginación (dinámica o tradicional según el sitio).
        
        Args:
            url: URL inicial a scrapear
            max_pages: Número máximo de páginas a procesar
            crawler: Instancia de AsyncWebCrawler a usar
            base_config: Configuración base de Crawl4AI (se combina con get_crawler_config())
            
        Returns:
            Lista de diccionarios con el resultado de cada página
        """
        pass
    
    def extract_previous_concursos(
        self,
        html: str,
        url: str
    ) -> List[Dict[str, Any]]:
        """
        Extrae información de concursos anteriores (opcional).
        
        Por defecto retorna lista vacía. Las estrategias específicas pueden
        sobrescribir este método si el sitio tiene una sección de "concursos anteriores".
        
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
        return []
    
    def get_organismo_name(self, url: str) -> str:
        """
        Retorna el nombre del organismo basándose en la URL.
        
        Args:
            url: URL del concurso
            
        Returns:
            Nombre del organismo (ej: "ANID", "MINEDUC")
        """
        return self.site_display_name
    
    def get_known_subdirecciones(self) -> Set[str]:
        """
        Retorna conjunto de subdirecciones conocidas para este sitio.
        
        Útil para evitar confundir nombres de concursos con nombres de subdirecciones
        al enriquecer o corregir datos.
        
        Returns:
            Conjunto de nombres de subdirecciones conocidas
        """
        return set()

