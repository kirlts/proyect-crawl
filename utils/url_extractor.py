"""
Utilidades para extraer URLs de concursos desde HTML
"""

import re
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# Subdirecciones/categorías conocidas en ANID que NO son nombres de concursos.
KNOWN_SUBDIRECCIONES = {
    "capital humano",
    "centros e investigación asociativa",
    "centros e investigacion asociativa",
    "investigación aplicada",
    "investigacion aplicada",
    "proyectos de investigación",
    "proyectos de investigacion",
    "redes, estrategia y conocimiento",
}


def extract_concurso_urls_from_html(html: str, base_url: str) -> Dict[str, str]:
    """
    Extrae URLs de concursos desde el HTML de una página de listado.
    
    Específico para ANID que usa JetEngine con estructura:
    - Cada concurso está en .jet-listing-grid__item
    - El enlace "Ver más" o el título del concurso tiene la URL
    
    Args:
        html: HTML de la página de listado
        base_url: URL base para construir URLs absolutas
        
    Returns:
        Diccionario {url_concurso: nombre_concurso} (nombre puede ser cadena vacía si no se pudo extraer).
    """
    if not html:
        return {}
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        # Mapear siempre por URL para evitar colisiones de nombre entre concursos
        # Estructura: { full_url: nombre_concurso_ou_vacio }
        concurso_urls: Dict[str, str] = {}
        
        # Buscar todos los items de concursos
        items = soup.select('.jet-listing-grid__item')
        
        for item in items:
            # Buscar enlace "Ver más" o enlace del título
            # ANID usa: <a> con texto "Ver más" o el título del concurso es un enlace
            link = None
            
            # Opción 1: Buscar botón "Ver más"
            ver_mas_links = item.select('a[href*="/concursos/"]')
            for ver_mas_link in ver_mas_links:
                link_text = ver_mas_link.get_text(strip=True).lower()
                href = ver_mas_link.get('href', '')
                
                # Si el texto dice "ver más" o el href parece ser de un concurso específico
                if 'ver más' in link_text or 'ver' in link_text or (
                    href and '/concursos/' in href and href != base_url and 
                    href != base_url.rstrip('/') and not href.endswith('/concursos/')
                ):
                    link = ver_mas_link
                    break
            
            # Opción 2: Si no hay "Ver más", buscar enlace del título
            if not link:
                title_link = item.select_one('h2 a, h3 a, .elementor-heading-title a, a[href*="/concursos/"]')
                if title_link:
                    link = title_link
            
            # Opción 3: Buscar cualquier enlace que parezca ser de un concurso
            if not link:
                all_links = item.select('a[href*="/concursos/"]')
                for candidate_link in all_links:
                    href = candidate_link.get('href', '')
                    # Excluir URLs genéricas
                    if (href and href != base_url and href != base_url.rstrip('/') and 
                        not href.endswith('/concursos/') and 
                        not href.endswith('/concursos') and
                        '/concursos/' in href):
                        link = candidate_link
                        break
            
            if link:
                href = link.get('href', '')
                if href:
                    # Construir URL absoluta
                    if href.startswith('/'):
                        full_url = urljoin(base_url, href)
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        full_url = urljoin(base_url, href)
                    
                    # Verificar que sea del mismo dominio y sea una URL de concurso específico
                    if (urlparse(full_url).netloc == urlparse(base_url).netloc and
                        '/concursos/' in full_url and
                        full_url != base_url and
                        not full_url.endswith('/concursos/') and
                        not full_url.endswith('/concursos')):
                        
                        # Intentar extraer nombre del concurso para mapeo
                        nombre = None
                        title_elem = item.select_one('h2, h3, .elementor-heading-title, [class*="title"]')
                        if title_elem:
                            nombre = title_elem.get_text(strip=True)
                        
                        # Evitar usar subdirecciones/categorías como "nombre" de concurso
                        if nombre and nombre.strip().lower() in KNOWN_SUBDIRECCIONES:
                            nombre = ""
                        
                        # Guardar usando siempre la URL como clave; nombre puede ser vacío si no se encontró
                        concurso_urls[full_url] = nombre or ""
        
        logger.info(f"📎 Extraídas {len(concurso_urls)} URLs de concursos desde HTML")
        return concurso_urls
        
    except Exception as e:
        logger.error(f"Error al extraer URLs de concursos desde HTML: {e}", exc_info=True)
        return {}


def match_concurso_to_url(concurso_nombre: str, concurso_urls_map: Dict[str, str],
                         default_url: str) -> str:
    """
    Intenta encontrar la URL correcta para un concurso basándose en su nombre.
    
    Args:
        concurso_nombre: Nombre del concurso extraído por el LLM
        concurso_urls_map: Diccionario {url: nombre} de URLs extraídas desde HTML
        default_url: URL por defecto si no se encuentra match
        
    Returns:
        URL del concurso o default_url si no se encuentra
    """
    if not concurso_nombre or not concurso_urls_map:
        return default_url
    
    # Normalizar nombre para comparación
    nombre_normalized = concurso_nombre.lower().strip()
    
    # Buscar match exacto o parcial contra el nombre HTML asociado a cada URL
    for url, nombre_html in concurso_urls_map.items():
        nombre_html_normalized = (nombre_html or "").lower().strip()
        if not nombre_html_normalized:
            continue
        # Match exacto
        if nombre_normalized == nombre_html_normalized:
            return url
        # Match parcial (nombre contiene al HTML o viceversa)
        if (len(nombre_normalized) > 10 and len(nombre_html_normalized) > 10 and
            (nombre_normalized in nombre_html_normalized or nombre_html_normalized in nombre_normalized)):
            return url
    
    # Si no hay match, retornar default_url
    return default_url

