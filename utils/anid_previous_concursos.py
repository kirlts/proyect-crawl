"""
Utilidades para extraer información de "Concursos anteriores" de páginas ANID.

Esta sección aparece al final de cada página de concurso que corresponde a una nueva versión
de concursos anteriores, y contiene el historial completo de versiones anteriores con sus fechas.
"""

import re
import logging
import quopri
from typing import List, Dict, Optional, Any
from bs4 import BeautifulSoup
from datetime import datetime
from utils.date_parser import parse_date

logger = logging.getLogger(__name__)


def extract_previous_concursos_from_html(html: str, url: str) -> List[Dict[str, Any]]:
    """
    Extrae la información de "Concursos anteriores" de una página HTML de ANID.
    
    La sección "Concursos anteriores" contiene un grid de items (jet-listing-grid__item)
    donde cada item representa una versión anterior del mismo concurso, con sus fechas
    de inicio y cierre.
    
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
    try:
        # Decodificar HTML si está en quoted-printable (común en MHTML)
        # Reemplazar =3D por = y =0A por \n antes de parsear
        html_decoded = html.replace('=3D', '=').replace('=0A', '\n').replace('=\n', '')
        
        # Intentar parsear con diferentes parsers si el primero falla
        try:
            soup = BeautifulSoup(html_decoded, 'html.parser')
        except (Exception, AttributeError):
            try:
                soup = BeautifulSoup(html_decoded, 'lxml')
            except Exception:
                # Si ambos parsers fallan, usar html.parser con manejo de errores
                soup = BeautifulSoup(html_decoded, 'html.parser')
        
        # Buscar el título "Concursos anteriores" (puede estar en h2, h3, o dentro de un elemento con ese texto)
        # Buscar por texto que contenga "Concursos anteriores" (case insensitive)
        previous_section = None
        best_grid = None
        max_items = 0
        
        # Buscar en todos los headings
        for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            heading_text = heading.get_text(strip=True).lower()
            # Normalizar texto (remover acentos para búsqueda más robusta)
            heading_text_normalized = heading_text.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
            if 'concursos anteriores' in heading_text_normalized or 'concursos anteriores' in heading_text:
                # Encontrar el contenedor padre que contiene el grid
                parent = heading.find_parent()
                if parent:
                    # Buscar el jet-listing-grid más cercano después de este heading
                    for sibling in parent.find_next_siblings():
                        grid = sibling.find(class_=re.compile(r'jet-listing-grid'))
                        if grid:
                            items_count = len(grid.find_all(class_=re.compile(r'jet-listing-grid__item')))
                            if items_count > max_items:
                                max_items = items_count
                                best_grid = grid
                    
                    # Si no está en siblings, buscar en el mismo contenedor
                    if not best_grid:
                        grid = parent.find(class_=re.compile(r'jet-listing-grid'))
                        if grid:
                            items_count = len(grid.find_all(class_=re.compile(r'jet-listing-grid__item')))
                            if items_count > max_items:
                                max_items = items_count
                                best_grid = grid
                    
                    # Buscar también en elementos siguientes del mismo nivel
                    current = heading
                    for _ in range(10):  # Buscar hasta 10 niveles hacia adelante
                        current = current.find_next_sibling()
                        if not current:
                            break
                        grid = current.find(class_=re.compile(r'jet-listing-grid'))
                        if grid:
                            items_count = len(grid.find_all(class_=re.compile(r'jet-listing-grid__item')))
                            if items_count > max_items:
                                max_items = items_count
                                best_grid = grid
        
        if best_grid:
            previous_section = best_grid
        
        # Si no encontramos por heading, buscar directamente el grid que viene después de "Concursos anteriores"
        if not previous_section:
            # Buscar cualquier elemento con texto "Concursos anteriores" (más flexible)
            for elem in soup.find_all(string=re.compile(r'concursos\s+anteriores', re.I)):
                parent = elem.find_parent()
                if parent:
                    # Buscar el grid más cercano en siblings
                    for next_elem in parent.find_next_siblings():
                        grid = next_elem.find(class_=re.compile(r'jet-listing-grid'))
                        if grid:
                            items_count = len(grid.find_all(class_=re.compile(r'jet-listing-grid__item')))
                            if items_count > max_items:
                                max_items = items_count
                                best_grid = grid
                    
                    # Si no está en siblings, buscar en el contenedor padre
                    if not best_grid:
                        parent_container = parent.find_parent()
                        if parent_container:
                            grid = parent_container.find(class_=re.compile(r'jet-listing-grid'))
                            if grid:
                                items_count = len(grid.find_all(class_=re.compile(r'jet-listing-grid__item')))
                                if items_count > max_items:
                                    max_items = items_count
                                    best_grid = grid
                    
                    # También buscar en el mismo nivel que el elemento
                    if not best_grid:
                        # Buscar en el mismo contenedor padre
                        if parent.parent:
                            grid = parent.parent.find(class_=re.compile(r'jet-listing-grid'))
                            if grid:
                                items_count = len(grid.find_all(class_=re.compile(r'jet-listing-grid__item')))
                                if items_count > max_items:
                                    max_items = items_count
                                    best_grid = grid
                    
                    # Buscar también en elementos siguientes
                    current = parent
                    for _ in range(10):
                        current = current.find_next_sibling()
                        if not current:
                            break
                        grid = current.find(class_=re.compile(r'jet-listing-grid'))
                        if grid:
                            items_count = len(grid.find_all(class_=re.compile(r'jet-listing-grid__item')))
                            if items_count > max_items:
                                max_items = items_count
                                best_grid = grid
            
            if best_grid:
                previous_section = best_grid
            
            # Último recurso: buscar directamente por clase jet-listing-grid cerca del texto
            if not previous_section:
                # Buscar todos los grids y verificar si están cerca de "Concursos anteriores"
                all_grids = soup.find_all(class_=re.compile(r'jet-listing-grid'))
                for grid in all_grids:
                    # Buscar texto "Concursos anteriores" cerca del grid
                    grid_html = str(grid)
                    grid_text = grid.get_text()
                    # Verificar si hay texto relacionado cerca
                    parent_text = ""
                    if grid.parent:
                        parent_text = grid.parent.get_text()
                    if 'concursos anteriores' in grid_text.lower() or 'concursos anteriores' in parent_text.lower():
                        items_count = len(grid.find_all(class_=re.compile(r'jet-listing-grid__item')))
                        if items_count > max_items:
                            max_items = items_count
                            best_grid = grid
                
                if best_grid:
                    previous_section = best_grid
        
        if not previous_section:
            logger.debug(f"No se encontró sección 'Concursos anteriores' en {url}")
            return []
        
        # Extraer items del grid
        items = previous_section.find_all(class_=re.compile(r'jet-listing-grid__item'))
        
        if not items:
            logger.debug(f"No se encontraron items en la sección 'Concursos anteriores' de {url}")
            return []
        
        previous_concursos = []
        seen_concursos = set()  # Para deduplicar por nombre + fechas
        
        # Filtrar items que tienen fechas (más probable que sean concursos anteriores reales)
        items_with_dates = []
        for item in items:
            # Verificar si el item tiene campos de fecha
            dynamic_fields = item.find_all(class_=re.compile(r'jet-listing-dynamic-field'))
            has_dates = False
            for field in dynamic_fields:
                field_text = field.get_text(strip=True).lower()
                if 'inicio' in field_text or 'apertura' in field_text or 'cierre' in field_text:
                    has_dates = True
                    break
            if has_dates:
                items_with_dates.append(item)
        
        # Si encontramos items con fechas, usar esos; si no, usar todos
        items_to_process = items_with_dates if items_with_dates else items
        
        # Lista de subdirecciones conocidas para filtrar
        subdirecciones_conocidas = [
            "capital humano",
            "centros e investigación asociativa",
            "investigación aplicada",
            "proyectos de investigación",
            "redes, estrategia y conocimiento",
            "redes estrategia y conocimiento"
        ]
        
        for item in items_to_process:
            try:
                # Extraer URL y link primero (necesario para extraer nombre del link)
                url_anterior = None
                link_elem = item.find('a', href=True)
                if link_elem:
                    url_anterior = link_elem.get('href')
                    # Normalizar URL relativa a absoluta si es necesario
                    if url_anterior and url_anterior.startswith('/'):
                        from urllib.parse import urlparse, urljoin
                        base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                        url_anterior = urljoin(base_url, url_anterior)
                
                # Extraer nombre del concurso con prioridades
                nombre = None
                
                # PRIORIDAD 1: Texto del link (si hay un link, su texto suele ser el nombre real del concurso)
                # PERO: si el texto es "Ver más" o similar, ignorarlo y buscar en otros lugares
                if link_elem:
                    link_text = link_elem.get_text(strip=True)
                    # Filtrar textos genéricos como "Ver más", "Leer más", "Más información", etc.
                    generic_texts = ["ver más", "leer más", "más información", "más", "ver", "leer", "click aquí", "click aqui"]
                    if link_text and len(link_text) > 5 and link_text.lower().strip() not in generic_texts:
                        # Verificar que no sea una subdirección
                        link_text_lower = link_text.lower().strip()
                        is_subdireccion = any(subdir in link_text_lower for subdir in subdirecciones_conocidas)
                        if not is_subdireccion:
                            nombre = link_text
                
                # PRIORIDAD 1.5: Buscar en atributos data-* o title del link (si el texto del link es genérico)
                if not nombre and link_elem:
                    # Buscar en atributo title
                    title_attr = link_elem.get('title', '').strip()
                    if title_attr and len(title_attr) > 5:
                        title_lower = title_attr.lower().strip()
                        is_subdireccion = any(subdir in title_lower for subdir in subdirecciones_conocidas)
                        if not is_subdireccion and title_attr.lower() not in generic_texts:
                            nombre = title_attr
                    
                    # Buscar en atributos data-*
                    if not nombre:
                        for attr_name, attr_value in link_elem.attrs.items():
                            if attr_name.startswith('data-') and isinstance(attr_value, str) and len(attr_value) > 5:
                                attr_lower = attr_value.lower().strip()
                                is_subdireccion = any(subdir in attr_lower for subdir in subdirecciones_conocidas)
                                if not is_subdireccion and attr_value.lower() not in generic_texts:
                                    nombre = attr_value
                                    break
                
                # PRIORIDAD 2: Extraer nombre de la URL (slug)
                if not nombre and url_anterior:
                    # Extraer el slug de la URL y convertirlo a nombre legible
                    # Ej: "nodos-macrozonales-2025" -> "Nodos Macrozonales 2025"
                    if '/' in url_anterior:
                        slug = url_anterior.rstrip('/').split('/')[-1]
                        if slug and len(slug) > 5:
                            # Convertir slug a nombre legible
                            nombre_from_slug = slug.replace('-', ' ').title()
                            # Verificar que no sea una subdirección
                            slug_lower = nombre_from_slug.lower().strip()
                            is_subdireccion = any(subdir in slug_lower for subdir in subdirecciones_conocidas)
                            if not is_subdireccion:
                                nombre = nombre_from_slug
                
                # PRIORIDAD 3: Buscar en headings con clase específica de título, pero filtrar subdirecciones
                if not nombre:
                    nombre_elem = item.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p'], class_=re.compile(r'heading|title|name'))
                    if nombre_elem:
                        text = nombre_elem.get_text(strip=True)
                        if text and len(text) > 5:
                            text_lower = text.lower().strip()
                            is_subdireccion = any(subdir in text_lower for subdir in subdirecciones_conocidas)
                            if not is_subdireccion:
                                nombre = text
                
                # PRIORIDAD 4: Buscar cualquier heading dentro del item, pero filtrar subdirecciones conocidas
                if not nombre:
                    for heading in item.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                        text = heading.get_text(strip=True)
                        if text and len(text) > 5:  # Evitar textos muy cortos
                            # Verificar que no sea una subdirección conocida
                            text_lower = text.lower().strip()
                            is_subdireccion = any(subdir in text_lower for subdir in subdirecciones_conocidas)
                            if not is_subdireccion:
                                nombre = text
                                break
                
                # PRIORIDAD 5: Si aún no tenemos nombre, buscar en el texto del item completo
                # pero excluyendo subdirecciones
                if not nombre:
                    item_text = item.get_text(separator=' ', strip=True)
                    # Dividir por líneas y buscar el primer fragmento válido
                    lines = [line.strip() for line in item_text.split('\n') if line.strip()]
                    for line in lines:
                        if len(line) > 10:  # Texto significativo
                            line_lower = line.lower().strip()
                            is_subdireccion = any(subdir in line_lower for subdir in subdirecciones_conocidas)
                            if not is_subdireccion and not re.match(r'^(inicio|apertura|cierre|fecha)', line_lower):
                                nombre = line
                                break
                
                # Limpiar nombre de caracteres codificados
                if nombre:
                    # Decodificar quoted-printable si es necesario
                    nombre = nombre.replace('=C3=B3', 'ó').replace('=C3=A1', 'á').replace('=C3=A9', 'é')
                    nombre = nombre.replace('=C3=AD', 'í').replace('=C3=BA', 'ú').replace('=C3=B1', 'ñ')
                    nombre = nombre.replace('=C3=81', 'Á').replace('=C3=89', 'É').replace('=C3=8D', 'Í')
                    nombre = nombre.replace('=C3=93', 'Ó').replace('=C3=9A', 'Ú').replace('=C3=91', 'Ñ')
                    nombre = nombre.strip()
                    
                    # Verificación final: si después de limpiar el nombre parece ser una subdirección, usar el slug de la URL
                    if nombre:
                        nombre_lower = nombre.lower().strip()
                        is_subdireccion = any(subdir in nombre_lower for subdir in subdirecciones_conocidas)
                        if is_subdireccion and url_anterior:
                            # Intentar extraer nombre de la URL como último recurso
                            if '/' in url_anterior:
                                slug = url_anterior.rstrip('/').split('/')[-1]
                                if slug and len(slug) > 5:
                                    nombre_from_slug = slug.replace('-', ' ').title()
                                    nombre = nombre_from_slug
                
                # Extraer fechas usando los campos dinámicos de JetEngine
                # Buscar campos con "Inicio:" y "Cierre:"
                fecha_apertura_raw = None
                fecha_cierre_raw = None
                
                # Buscar todos los campos dinámicos dentro del item
                dynamic_fields = item.find_all(class_=re.compile(r'jet-listing-dynamic-field'))
                
                for field in dynamic_fields:
                    field_text = field.get_text(strip=True)
                    
                    # Buscar "Inicio:" o "Apertura:"
                    if re.search(r'inicio|apertura', field_text, re.I):
                        # Extraer la fecha después de "Inicio:" o "Apertura:"
                        match = re.search(r'(?:inicio|apertura)[:\s]+(.+)', field_text, re.I)
                        if match:
                            fecha_apertura_raw = match.group(1).strip()
                    
                    # Buscar "Cierre:"
                    if re.search(r'cierre', field_text, re.I):
                        match = re.search(r'cierre[:\s]+(.+)', field_text, re.I)
                        if match:
                            fecha_cierre_raw = match.group(1).strip()
                
                # Parsear fechas
                fecha_apertura = None
                fecha_cierre = None
                
                if fecha_apertura_raw:
                    parsed = parse_date(fecha_apertura_raw)
                    if parsed:
                        fecha_apertura = parsed.strftime("%Y-%m-%d")
                
                if fecha_cierre_raw:
                    parsed = parse_date(fecha_cierre_raw)
                    if parsed:
                        fecha_cierre = parsed.strftime("%Y-%m-%d")
                
                
                # Extraer año priorizando fechas (apertura/cierre), luego URL, y SOLO al final el nombre.
                año = None
                # PRIORIDAD 1: De la fecha de apertura
                if fecha_apertura:
                    year_match = re.search(r'^(\d{4})', fecha_apertura)
                    if year_match:
                        año = int(year_match.group(1))
                
                # PRIORIDAD 2: De la fecha de cierre
                if not año and fecha_cierre:
                    year_match = re.search(r'^(\d{4})', fecha_cierre)
                    if year_match:
                        año = int(year_match.group(1))
                
                # PRIORIDAD 3: De la URL (slug)
                if not año and url_anterior:
                    year_match = re.search(r'\b(20\d{2})\b', url_anterior)
                    if year_match:
                        año = int(year_match.group(1))
                
                # PRIORIDAD 4: Del nombre (último recurso, puede ser engañoso como "2030")
                if not año and nombre:
                    year_match = re.search(r'\b(20\d{2})\b', nombre)
                    if year_match:
                        año = int(year_match.group(1))

                # Sanidad de rango para evitar años absurdos
                if año is not None and not (1900 <= año <= 2100):
                    año = None
                
                if nombre:  # Solo agregar si tenemos al menos un nombre
                    # Crear clave única para deduplicación (nombre + fechas)
                    dedup_key = (
                        nombre.lower().strip(),
                        fecha_apertura or "",
                        fecha_cierre or ""
                    )
                    
                    # Solo agregar si no es duplicado
                    if dedup_key not in seen_concursos:
                        seen_concursos.add(dedup_key)
                        previous_concursos.append({
                            "nombre": nombre,
                            "fecha_apertura": fecha_apertura,
                            "fecha_cierre": fecha_cierre,
                            "fecha_apertura_original": fecha_apertura_raw,
                            "fecha_cierre_original": fecha_cierre_raw,
                            "url": url_anterior,
                            "año": año
                        })
                    
            except (AttributeError, KeyError, ValueError, TypeError) as e:
                logger.warning(f"Error al extraer item de concurso anterior: {e}")
                continue
            except Exception as e:
                logger.warning(f"Error inesperado al extraer item de concurso anterior: {e}", exc_info=True)
                continue
        
        logger.info(f"✅ Extraídos {len(previous_concursos)} concursos anteriores de {url}")
        return previous_concursos
        
    except Exception as e:
        logger.error(f"Error al extraer concursos anteriores de {url}: {e}", exc_info=True)
        return []


def format_previous_concursos_for_prediction(previous_concursos: List[Dict[str, Any]]) -> str:
    """
    Formatea la información de concursos anteriores para incluirla en el prompt de predicción.
    
    Args:
        previous_concursos: Lista de diccionarios con información de concursos anteriores
        
    Returns:
        String formateado con la información histórica
    """
    if not previous_concursos:
        return "No hay información de concursos anteriores disponible."
    
    lines = ["CONCURSOS ANTERIORES (información histórica extraída directamente de la página):"]
    
    for i, prev in enumerate(previous_concursos, 1):
        lines.append(f"\nVersión {i}:")
        lines.append(f"- Nombre: {prev.get('nombre', 'N/A')}")
        if prev.get('año'):
            lines.append(f"- Año: {prev['año']}")
        if prev.get('fecha_apertura'):
            lines.append(f"- Fecha apertura: {prev['fecha_apertura']}")
            if prev.get('fecha_apertura_original') and prev['fecha_apertura_original'] != prev['fecha_apertura']:
                lines.append(f"  (texto original: {prev['fecha_apertura_original']})")
        elif prev.get('fecha_apertura_original'):
            lines.append(f"- Fecha apertura (texto): {prev['fecha_apertura_original']}")
        if prev.get('fecha_cierre'):
            lines.append(f"- Fecha cierre: {prev['fecha_cierre']}")
            if prev.get('fecha_cierre_original') and prev['fecha_cierre_original'] != prev['fecha_cierre']:
                lines.append(f"  (texto original: {prev['fecha_cierre_original']})")
        elif prev.get('fecha_cierre_original'):
            lines.append(f"- Fecha cierre (texto): {prev['fecha_cierre_original']}")
        if prev.get('url'):
            lines.append(f"- URL: {prev['url']}")
    
    return "\n".join(lines)

