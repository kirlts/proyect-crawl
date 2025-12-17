"""
Paginación tradicional usando enlaces HTML.
"""

import logging
from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler

from crawler.pagination.base_pagination import BasePagination
# Importar función legacy desde el módulo raíz
import sys
import os
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

try:
    from crawler.pagination import find_pagination_links
except ImportError:
    # Fallback si no se puede importar
    def find_pagination_links(html: str, base_url: str):
        return []

logger = logging.getLogger(__name__)


class GenericPagination(BasePagination):
    """
    Implementación de paginación tradicional (enlaces HTML).
    
    Busca enlaces de paginación en el HTML y scrapea cada página individualmente.
    """
    
    async def scrape_pages(
        self,
        url: str,
        max_pages: int,
        crawler: AsyncWebCrawler,
        config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Scrapea páginas usando enlaces de paginación tradicional.
        
        Args:
            url: URL inicial
            max_pages: Número máximo de páginas
            crawler: Instancia de AsyncWebCrawler
            config: Configuración de Crawl4AI
            
        Returns:
            Lista de resultados (una entrada por página)
        """
        all_results = []
        
        # Scrapear primera página
        logger.info(f"📄 Procesando página 1 de {max_pages} para {url}")
        first_result = await crawler.arun(url=url)
        
        if not first_result.success:
            logger.warning(f"⚠️ Error al procesar página 1: {first_result.error_message}")
            return all_results
        
        # Procesar primera página
        first_page_result = {
            "success": True,
            "markdown": first_result.markdown.raw_markdown if first_result.markdown else "",
            "html": first_result.html or "",
            "url": url,
            "html_length": len(first_result.html or ""),
            "markdown_length": len(first_result.markdown.raw_markdown if first_result.markdown else "")
        }
        all_results.append(first_page_result)
        logger.info(f"✅ Página 1 procesada correctamente")
        
        # Buscar enlaces de paginación
        html = first_result.html or ""
        pagination_links = find_pagination_links(html, url)
        
        # Limitar número de páginas
        pages_to_scrape = min(len(pagination_links), max_pages - 1)
        
        # Scrapear páginas adicionales
        for i, page_url in enumerate(pagination_links[:pages_to_scrape], start=2):
            logger.info(f"📄 Procesando página {i} de {max_pages} para {page_url}")
            page_result = await crawler.arun(url=page_url)
            
            if page_result.success:
                page_data = {
                    "success": True,
                    "markdown": page_result.markdown.raw_markdown if page_result.markdown else "",
                    "html": page_result.html or "",
                    "url": page_url,
                    "html_length": len(page_result.html or ""),
                    "markdown_length": len(page_result.markdown.raw_markdown if page_result.markdown else "")
                }
                all_results.append(page_data)
                logger.info(f"✅ Página {i} procesada correctamente")
            else:
                logger.warning(f"⚠️ Error al procesar página {i}: {page_result.error_message}")
        
        return all_results

