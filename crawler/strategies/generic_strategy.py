"""
Estrategia genérica para sitios estándar sin lógica específica.

Esta estrategia se usa como fallback para sitios que no tienen
una estrategia específica implementada.
"""

import asyncio
import logging
from typing import List, Dict, Any
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler
from crawler.strategies.base_strategy import ScrapingStrategy
from crawler.pagination.generic_pagination import GenericPagination

logger = logging.getLogger(__name__)


class GenericStrategy(ScrapingStrategy):
    """
    Estrategia genérica para sitios estándar.
    
    Usa paginación tradicional (enlaces HTML) y configuración básica de Crawl4AI.
    No extrae "concursos anteriores" ni tiene lógica específica.
    """
    
    @property
    def site_name(self) -> str:
        return "generic"
    
    @property
    def site_display_name(self) -> str:
        return "Generic"
    
    def get_crawler_config(self) -> Dict[str, Any]:
        """
        Retorna configuración genérica de Crawl4AI.
        
        Returns:
            Diccionario con configuración básica
        """
        return {
            "wait_for": "css:body",
            "wait_until": "domcontentloaded",
            "scan_full_page": True,
        }
    
    def supports_dynamic_pagination(self) -> bool:
        return False
    
    async def scrape_with_pagination(
        self,
        url: str,
        max_pages: int,
        crawler: AsyncWebCrawler,
        base_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Scrapea URL con paginación tradicional (enlaces HTML).
        
        Args:
            url: URL inicial a scrapear
            max_pages: Número máximo de páginas a procesar
            crawler: Instancia de AsyncWebCrawler
            base_config: Configuración base de Crawl4AI
            
        Returns:
            Lista de resultados de scraping (una entrada por página)
        """
        # Combinar configuración base con configuración específica
        combined_config = {**base_config, **self.get_crawler_config()}
        
        # Usar GenericPagination para manejar la paginación tradicional
        pagination = GenericPagination()
        return await pagination.scrape_pages(url, max_pages, crawler, combined_config)
    
    def get_organismo_name(self, url: str) -> str:
        """
        Intenta inferir el organismo desde el dominio.
        
        Args:
            url: URL del concurso
            
        Returns:
            Nombre del organismo o "Desconocido"
        """
        try:
            domain = urlparse(url).netloc.replace("www.", "")
            # Mapeo básico de dominios comunes
            domain_mapping = {
                "anid.cl": "ANID",
                "mineduc.cl": "MINEDUC",
                "cnachile.cl": "CNA",
            }
            
            for key, value in domain_mapping.items():
                if key in domain:
                    return value
            
            return "Desconocido"
        except Exception:
            return "Desconocido"

