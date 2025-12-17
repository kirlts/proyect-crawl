from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig

from crawler.strategies.base_strategy import ScrapingStrategy


class CentroEstudiosStrategy(ScrapingStrategy):
    """Estrategia específica para Centro de Estudios MINEDUC (FONIDE)."""

    @property
    def site_name(self) -> str:
        return "centroestudios.mineduc.cl"

    @property
    def site_display_name(self) -> str:
        return "Centro Estudios MINEDUC"

    def get_crawler_config(self) -> Dict[str, Any]:
        return {
            "wait_for": "css:body",
            "wait_until": "domcontentloaded",
            "scan_full_page": True,
            "page_timeout": 30000,  # 30s para evitar esperas largas en un sitio estático
            "wait_for_images": False,
            "word_count_threshold": 5,
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
        """Fuerza una sola página sin paginación."""
        config = base_config.copy()
        config.update(self.get_crawler_config())
        result = await crawler.arun(
            url=url,
            config=CrawlerRunConfig(**config)
        )
        return [result] if result else []

    def extract_previous_concursos(
        self,
        html: str,
        url: str
    ) -> List[Dict[str, Any]]:
        """No hay 'concursos anteriores' disponibles en el sitio."""
        return []

