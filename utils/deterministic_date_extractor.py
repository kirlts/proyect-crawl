"""
Extracción determinística de datos desde markdown/HTML antes de usar LLM.

Intenta extraer fechas, nombre y otros datos usando patrones comunes
encontrados en las páginas de concursos (especialmente ANID).
"""

import re
from typing import Optional, Dict, Tuple
from datetime import datetime
from bs4 import BeautifulSoup


def extract_dates_deterministically(markdown: str) -> Dict[str, Optional[str]]:
    """
    Extrae fechas de apertura y cierre determinísticamente desde markdown.
    
    Busca patrones comunes como:
    - "Inicio: " o "Apertura: " seguido de fecha
    - "Cierre: " o "Fecha de cierre: " seguido de fecha
    
    Args:
        markdown: Contenido markdown a analizar
        
    Returns:
        Diccionario con:
        - "fecha_apertura": Texto original de fecha de apertura o None
        - "fecha_cierre": Texto original de fecha de cierre o None
        - "is_suspendido": True si se detecta texto "suspendido" en el contenido
    """
    if not markdown:
        return {
            "fecha_apertura": None,
            "fecha_cierre": None,
            "is_suspendido": False
        }
    
    markdown_lower = markdown.lower()
    
    # Detectar si está suspendido
    suspendido_keywords = [
        "concurso suspendido",
        "suspendido",
        "concurso adjudicado"  # Los adjudicados también están cerrados
    ]
    is_suspendido = any(keyword in markdown_lower for keyword in suspendido_keywords)
    
    fecha_apertura = None
    fecha_cierre = None
    
    # Patrones para fecha de apertura/inicio
    apertura_patterns = [
        r'(?:inicio|apertura|desde):\s*([^\n\r]+?)(?:\n|$|cierre|hasta|vence)',
        r'(?:inicio|apertura|desde)\s*[:\-]\s*([^\n\r]+?)(?:\n|$|cierre|hasta|vence)',
        r'\*\*inicio\*\*[:\s]*([^\n\r]+?)(?:\n|$|cierre|hasta|vence)',
        r'\*\*apertura\*\*[:\s]*([^\n\r]+?)(?:\n|$|cierre|hasta|vence)',
    ]
    
    # Patrones para fecha de cierre
    cierre_patterns = [
        r'(?:cierre|hasta|vence|fecha\s+de\s+cierre):\s*([^\n\r]+?)(?:\n|$|\.|,|;|apertura|inicio)',
        r'(?:cierre|hasta|vence|fecha\s+de\s+cierre)\s*[:\-]\s*([^\n\r]+?)(?:\n|$|\.|,|;|apertura|inicio)',
        r'\*\*cierre\*\*[:\s]*([^\n\r]+?)(?:\n|$|\.|,|;|apertura|inicio)',
        r'\*\*fecha\s+de\s+cierre\*\*[:\s]*([^\n\r]+?)(?:\n|$|\.|,|;|apertura|inicio)',
    ]
    
    # Validadores auxiliares
    def _has_valid_year(text: str) -> bool:
        """Retorna True si el texto contiene un año de 4 dígitos (20xx)."""
        return bool(re.search(r"\b20\d{2}\b", text))

    def _is_placeholder(text: str) -> bool:
        """Detecta valores incompletos como '**' que indican contenido faltante."""
        return "**" in text

    def _normalize_fecha(text: str) -> Optional[str]:
        """Limpia y valida una fecha en texto; exige año y sin placeholders."""
        if not text:
            return None
        cleaned = re.sub(r"[^\w\s\d\-\/\.:,]+$", "", text).strip()
        if not cleaned or len(cleaned) <= 5:
            return None
        if _is_placeholder(cleaned):
            return None
        if not _has_valid_year(cleaned):
            return None
        return cleaned

    # Buscar fecha de apertura
    for pattern in apertura_patterns:
        match = re.search(pattern, markdown, re.IGNORECASE | re.MULTILINE)
        if match:
            fecha_texto = _normalize_fecha(match.group(1).strip())
            if fecha_texto:
                fecha_apertura = fecha_texto
                break
    
    # Buscar fecha de cierre
    for pattern in cierre_patterns:
        match = re.search(pattern, markdown, re.IGNORECASE | re.MULTILINE)
        if match:
            fecha_texto = _normalize_fecha(match.group(1).strip())
            if fecha_texto:
                fecha_cierre = fecha_texto
                break

    # Si el concurso está suspendido, ignorar fechas
    if is_suspendido:
        fecha_apertura = None
        fecha_cierre = None
        
    return {
        "fecha_apertura": fecha_apertura,
        "fecha_cierre": fecha_cierre,
        "is_suspendido": is_suspendido
    }


def extract_concurso_data_deterministically(
    markdown: str,
    concurso_url: Optional[str] = None
) -> Optional[Dict[str, any]]:
    """
    Intenta extraer datos básicos de un concurso determinísticamente desde markdown.
    
    Solo extrae fechas y detecta si está suspendido. NO extrae nombre, organismo, etc.
    (esos requieren LLM o parsing HTML más complejo).
    
    Args:
        markdown: Contenido markdown de la página del concurso
        concurso_url: URL del concurso (para detectar "concurso-suspendido")
        
    Returns:
        Diccionario con datos extraídos o None si no se pudo extraer suficiente información.
        Contiene:
        - fecha_apertura: Texto original
        - fecha_cierre: Texto original
        - is_suspendido: bool
    """
    if not markdown:
        return None
    
    # Detectar suspendido por URL
    is_suspendido_by_url = False
    if concurso_url and "concurso-suspendido" in concurso_url.lower():
        is_suspendido_by_url = True
    
    # Extraer fechas determinísticamente
    dates_result = extract_dates_deterministically(markdown)
    
    # Si no se encontraron fechas y no está suspendido, retornar None
    # (indicando que se debe usar LLM)
    if not dates_result["fecha_apertura"] and not dates_result["fecha_cierre"]:
        if not dates_result["is_suspendido"] and not is_suspendido_by_url:
            return None
    
    return {
        "fecha_apertura": dates_result["fecha_apertura"],
        "fecha_cierre": dates_result["fecha_cierre"],
        "is_suspendido": dates_result["is_suspendido"] or is_suspendido_by_url
    }


def extract_nombre_deterministically(html: str, markdown: str) -> Optional[str]:
    """
    Extrae el nombre del concurso determinísticamente desde HTML/markdown.
    
    El nombre del concurso generalmente aparece:
    - En el tag <title> (sin el sufijo " - ANID")
    - Como el primer <h1> o heading principal en el contenido
    - En meta tags og:title
    
    Args:
        html: Contenido HTML de la página
        markdown: Contenido markdown de la página
        
    Returns:
        Nombre del concurso extraído o None si no se pudo determinar
    """
    nombre = None
    
    # Método 1: Extraer desde <title> tag en HTML
    if html:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            title_tag = soup.find('title')
            if title_tag:
                title_text = title_tag.get_text().strip()
                # Remover sufijos comunes como " - ANID"
                title_text = re.sub(r'\s*-\s*ANID\s*$', '', title_text, flags=re.IGNORECASE)
                title_text = re.sub(r'\s*-\s*anid\.cl\s*$', '', title_text, flags=re.IGNORECASE)
                if title_text and len(title_text) > 5:
                    nombre = title_text.strip()
        except Exception:
            pass
    
    # Método 2: Buscar en meta tag og:title
    if not nombre and html:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                og_text = og_title.get('content').strip()
                # Remover sufijos comunes
                og_text = re.sub(r'\s*-\s*ANID\s*$', '', og_text, flags=re.IGNORECASE)
                if og_text and len(og_text) > 5:
                    nombre = og_text.strip()
        except Exception:
            pass
    
    # Método 3: Buscar el primer h1 en el contenido principal
    if not nombre and html:
        try:
            soup = BeautifulSoup(html, 'html.parser')
            # Buscar h1 que no esté en header/nav
            h1_tags = soup.find_all('h1')
            for h1 in h1_tags:
                # Ignorar h1 en header/nav
                parent = h1.find_parent(['header', 'nav'])
                if not parent:
                    h1_text = h1.get_text().strip()
                    if h1_text and len(h1_text) > 5:
                        # Filtrar textos genéricos
                        if h1_text.lower() not in ['anid', 'concursos', 'concurso']:
                            nombre = h1_text.strip()
                            break
        except Exception:
            pass
    
    # Método 4: Buscar en markdown (primer heading grande)
    if not nombre and markdown:
        # Buscar el primer # o ## heading que no sea genérico
        heading_pattern = r'^#+\s+(.+)$'
        for line in markdown.split('\n'):
            match = re.match(heading_pattern, line.strip())
            if match:
                heading_text = match.group(1).strip()
                if heading_text and len(heading_text) > 5:
                    # Filtrar textos genéricos
                    if heading_text.lower() not in ['anid', 'concursos', 'concurso', 'presentación']:
                        nombre = heading_text.strip()
                        break
    
    return nombre


def extract_concurso_data_deterministically(
    markdown: str,
    concurso_url: Optional[str] = None,
    html: Optional[str] = None
) -> Optional[Dict[str, any]]:
    """
    Intenta extraer datos básicos de un concurso determinísticamente desde markdown/HTML.
    
    Extrae:
    - Nombre del concurso (desde <title>, og:title, o h1)
    - Fechas de apertura y cierre (desde patrones "Inicio:", "Cierre:")
    - Estado suspendido (desde contenido o URL)
    
    Args:
        markdown: Contenido markdown de la página del concurso
        concurso_url: URL del concurso (para detectar "concurso-suspendido")
        html: Contenido HTML de la página (opcional, para extraer nombre)
        
    Returns:
        Diccionario con datos extraídos o None si no se pudo extraer suficiente información.
        Contiene:
        - nombre: Nombre del concurso
        - fecha_apertura: Texto original
        - fecha_cierre: Texto original
        - is_suspendido: bool
    """
    if not markdown and not html:
        return None
    
    # Detectar suspendido por URL
    is_suspendido_by_url = False
    if concurso_url and "concurso-suspendido" in concurso_url.lower():
        is_suspendido_by_url = True
    
    # Extraer nombre determinísticamente
    nombre = None
    if html or markdown:
        nombre = extract_nombre_deterministically(html or "", markdown or "")
    
    # Extraer fechas determinísticamente
    dates_result = extract_dates_deterministically(markdown or "")
    
    # Si está suspendido, eliminar fechas determinísticas para no contaminar el estado
    if dates_result["is_suspendido"] or is_suspendido_by_url:
        dates_result["fecha_apertura"] = None
        dates_result["fecha_cierre"] = None
    
    # Si no se encontró nada útil, retornar None (indicando que se debe usar LLM)
    if not nombre and not dates_result["fecha_apertura"] and not dates_result["fecha_cierre"]:
        if not dates_result["is_suspendido"] and not is_suspendido_by_url:
            return None
    
    return {
        "nombre": nombre,
        "fecha_apertura": dates_result["fecha_apertura"],
        "fecha_cierre": dates_result["fecha_cierre"],
        "is_suspendido": dates_result["is_suspendido"] or is_suspendido_by_url
    }

