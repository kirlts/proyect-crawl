"""
Estrategia específica para ANID con paginación dinámica JetEngine.

Esta estrategia encapsula toda la lógica específica de ANID:
- Paginación dinámica con JavaScript
- Extracción de "Concursos anteriores"
- Configuración específica de Crawl4AI
"""

from typing import List, Dict, Any, Set
from crawl4ai import AsyncWebCrawler

from crawler.strategies.base_strategy import ScrapingStrategy
from crawler.pagination.anid_pagination import AnidPagination
from utils.extractors.anid_extractor import AnidExtractor
from config.sites import get_site_config

# Subdirecciones conocidas en ANID
KNOWN_SUBDIRECCIONES = {
    "capital humano",
    "centros e investigación asociativa",
    "investigación aplicada",
    "proyectos de investigación",
    "redes, estrategia y conocimiento",
    "redes estrategia y conocimiento",
}


class ANIDStrategy(ScrapingStrategy):
    """
    Estrategia específica para ANID.
    
    Maneja la paginación dinámica de JetEngine/Elementor y la extracción
    de "Concursos anteriores" específica de ANID.
    """
    
    def __init__(self):
        """Inicializa la estrategia ANID."""
        self._pagination = AnidPagination()
        self._extractor = AnidExtractor()
        self._site_config = get_site_config("anid.cl")
    
    @property
    def site_name(self) -> str:
        return "anid.cl"
    
    @property
    def site_display_name(self) -> str:
        return "ANID"
    
    def get_crawler_config(self) -> Dict[str, Any]:
        """
        Retorna configuración específica de Crawl4AI para ANID.
        
        Returns:
            Diccionario con configuración específica de ANID
        """
        # Obtener configuración desde config/sites.py
        site_config = get_site_config("anid.cl")
        crawler_config = site_config.get("crawler_config", {})
        
        # Configuración base para ANID
        base_config = {
            "wait_for": "css:.jet-listing-grid__item",  # Esperar contenedores JetEngine
            "wait_until": "domcontentloaded",  # Cargar DOM, luego hook espera AJAX
            "scan_full_page": True,  # Scroll completo para lazy loading
        }
        
        # Combinar con configuración del sitio
        return {**base_config, **crawler_config}
    
    def supports_dynamic_pagination(self) -> bool:
        return True
    
    async def scrape_with_pagination(
        self,
        url: str,
        max_pages: int,
        crawler: AsyncWebCrawler,
        base_config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Scrapea URL con paginación dinámica de ANID.
        
        Args:
            url: URL inicial a scrapear
            max_pages: Número máximo de páginas a procesar
            crawler: Instancia de AsyncWebCrawler
            base_config: Configuración base de Crawl4AI
            
        Returns:
            Lista de resultados de scraping (una entrada por página)
        """
        # Combinar configuración base con configuración específica de ANID
        combined_config = {**base_config, **self.get_crawler_config()}
        
        # Usar AnidPagination para manejar la paginación dinámica
        return await self._pagination.scrape_pages(url, max_pages, crawler, combined_config)
    
    def extract_previous_concursos(
        self,
        html: str,
        url: str
    ) -> List[Dict[str, Any]]:
        """
        Extrae información de "Concursos anteriores" de ANID.
        
        Args:
            html: Contenido HTML de la página del concurso
            url: URL de la página (para logging)
            
        Returns:
            Lista de diccionarios con información de concursos anteriores
        """
        return self._extractor.extract_previous_concursos(html, url)
    
    def get_organismo_name(self, url: str) -> str:
        return "ANID"
    
    def get_known_subdirecciones(self) -> Set[str]:
        """
        Retorna conjunto de subdirecciones conocidas de ANID.
        
        Returns:
            Conjunto de nombres de subdirecciones conocidas
        """
        return KNOWN_SUBDIRECCIONES

