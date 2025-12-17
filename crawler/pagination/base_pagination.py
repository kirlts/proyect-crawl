"""
Clase base abstracta para manejo de paginación.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler


class BasePagination(ABC):
    """
    Clase base para implementaciones de paginación.
    
    Cada tipo de paginación (dinámica, tradicional) implementa esta interfaz.
    """
    
    @abstractmethod
    async def scrape_pages(
        self,
        url: str,
        max_pages: int,
        crawler: AsyncWebCrawler,
        config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Scrapea múltiples páginas usando la estrategia de paginación.
        
        Args:
            url: URL inicial
            max_pages: Número máximo de páginas
            crawler: Instancia de AsyncWebCrawler
            config: Configuración de Crawl4AI
            
        Returns:
            Lista de resultados (una entrada por página)
        """
        pass

