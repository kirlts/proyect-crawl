"""
Utilidades para detectar y navegar paginación en sitios web
"""

import re
from typing import List, Optional, Dict, Any
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)


def find_pagination_links(html: str, base_url: str) -> List[str]:
    """
    Encuentra enlaces de paginación en el HTML
    Optimizado específicamente para ANID que usa JetEngine
    
    Args:
        html: HTML de la página
        base_url: URL base para construir URLs absolutas
        
    Returns:
        Lista de URLs de páginas ordenadas (sin duplicados)
    """
    if not html:
        return []
    
    try:
        from urllib.parse import urljoin, urlparse, parse_qs, urlencode
        soup = BeautifulSoup(html, 'html.parser')
        page_urls = set()
        
        # ESPECÍFICO PARA ANID: Buscar enlaces en .jet-filters-pagination
        # ANID usa JetEngine con estructura: .jet-filters-pagination__item > .jet-filters-pagination__link
        pagination_container = soup.select_one('.jet-filters-pagination, .jet-smart-filters-pagination')
        if pagination_container:
            # Buscar todos los enlaces dentro del contenedor de paginación
            pagination_links = pagination_container.select('.jet-filters-pagination__link, a')
            
            for link in pagination_links:
                href = link.get('href', '')
                text = link.get_text(strip=True)
                
                # Ignorar enlaces vacíos, "..." y ">" sin href
                if not href or text in ['…', '...', '>', '<']:
                    continue
                
                # Construir URL absoluta
                if href.startswith('/'):
                    full_url = urljoin(base_url, href)
                elif href.startswith('http'):
                    full_url = href
                else:
                    full_url = urljoin(base_url, href)
                
                # Verificar que sea del mismo dominio
                if urlparse(full_url).netloc == urlparse(base_url).netloc:
                    # Verificar que tenga parámetro page= o sea un número
                    if re.search(r'[?&]page=\d+', full_url) or text.isdigit():
                        page_urls.add(full_url)
        
        # También buscar enlaces de paginación comunes como fallback
        pagination_selectors = [
            '.pagination a',
            '.pager a',
            '.page-numbers a',
            '[class*="pagination"] a',
            'a[href*="page="]',
            'a[href*="/page/"]',
        ]
        
        for selector in pagination_selectors:
            links = soup.select(selector)
            for link in links:
                href = link.get('href', '')
                text = link.get_text(strip=True).lower()
                
                if href and (
                    text.isdigit() or
                    re.search(r'[?&]page=\d+', href) or
                    re.search(r'/page/\d+', href)
                ):
                    if href.startswith('/'):
                        full_url = urljoin(base_url, href)
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        full_url = urljoin(base_url, href)
                    
                    if urlparse(full_url).netloc == urlparse(base_url).netloc:
                        page_urls.add(full_url)
        
        # Ordenar URLs por número de página
        def get_page_number(url: str) -> int:
            match = re.search(r'[?&]page=(\d+)', url)
            if match:
                return int(match.group(1))
            match = re.search(r'/page/(\d+)', url)
            if match:
                return int(match.group(1))
            return 0
        
        sorted_urls = sorted(list(page_urls), key=get_page_number)
        
        # Filtrar la URL base si está presente
        sorted_urls = [u for u in sorted_urls if u != base_url and u.rstrip('/') != base_url.rstrip('/')]
        
        logger.info(f"Encontrados {len(sorted_urls)} enlaces de paginación únicos")
        return sorted_urls
        
    except Exception as e:
        logger.error(f"Error al buscar enlaces de paginación: {e}", exc_info=True)
        return []


def get_next_page_url(html: str, current_url: str) -> Optional[str]:
    """
    Obtiene la URL de la siguiente página si existe
    
    Args:
        html: HTML de la página actual
        current_url: URL actual
        
    Returns:
        URL de la siguiente página o None
    """
    links = find_pagination_links(html, current_url)
    
    # Buscar específicamente el enlace "siguiente" o el siguiente número
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Buscar enlace "Siguiente" o "Next"
        next_link = soup.find('a', string=re.compile(r'siguiente|next', re.I))
        if not next_link:
            next_link = soup.find('a', {'aria-label': re.compile(r'siguiente|next', re.I)})
        
        if next_link and next_link.get('href'):
            href = next_link.get('href')
            from urllib.parse import urljoin
            return urljoin(current_url, href)
        
        # Si no hay enlace "siguiente", buscar el siguiente número de página
        # Esto requiere más lógica específica del sitio
        # Por ahora, retornar None y dejar que el usuario maneje múltiples URLs
        
    except Exception as e:
        logger.error(f"Error al obtener siguiente página: {e}")
    
    return None


def extract_page_number_from_url(url: str) -> Optional[int]:
    """
    Extrae el número de página de una URL
    
    Args:
        url: URL a analizar
        
    Returns:
        Número de página o None
    """
    # Buscar patrones comunes: ?page=2, /page/2, /p/2, etc.
    patterns = [
        r'[?&]page=(\d+)',
        r'/page/(\d+)',
        r'/p/(\d+)',
        r'/pagina-(\d+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return int(match.group(1))
    
    return None

