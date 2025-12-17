"""
Sanitizador de HTML para eliminar código superfluo antes de enviar al LLM
"""

import re
from bs4 import BeautifulSoup
from typing import Optional


def sanitize_html(html: str, preserve_structure: bool = True) -> str:
    """
    Sanitiza HTML eliminando elementos innecesarios para análisis con LLM
    Optimizado para reducir significativamente el tamaño del HTML
    
    Args:
        html: HTML crudo a sanitizar
        preserve_structure: Si True, mantiene la estructura semántica (headers, lists, etc.)
        
    Returns:
        HTML sanitizado
    """
    if not html:
        return ""
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Eliminar TODO el <head> (no necesitamos title para análisis)
        if soup.head:
            soup.head.decompose()
        
        # Eliminar scripts, noscript, iframes (tracking, ads, etc.)
        for element in soup.find_all(['script', 'noscript', 'iframe', 'embed', 'object']):
            element.decompose()
        
        # Eliminar estilos inline y tags style
        for style in soup.find_all('style'):
            style.decompose()
        
        # Eliminar todos los <link> tags (CSS, favicons, etc.)
        for link in soup.find_all('link'):
            link.decompose()
        
        # Eliminar comentarios HTML
        from bs4 import Comment
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()
        
        # Eliminar elementos de tracking y analytics
        for element in soup.find_all(['img']):
            # Eliminar imágenes que son claramente decorativas o de tracking
            src = element.get('src', '').lower()
            alt = element.get('alt', '').lower()
            if any(tracker in src for tracker in ['pixel', 'track', 'analytics', 'beacon', '1x1', 'spacer']) or \
               (not alt and not src) or \
               ('icon' in src and 'logo' not in src):
                element.decompose()
        
        # Eliminar elementos de redes sociales y widgets
        for element in soup.find_all(['aside']):
            # Eliminar sidebars que no tienen contenido relevante
            text = element.get_text().lower()
            if not any(keyword in text for keyword in ['concurso', 'postulacion', 'fecha', 'cierre', 'apertura']):
                element.decompose()
        
        # Eliminar headers y navegación (generalmente no tienen contenido de concursos)
        for header in soup.find_all(['header', 'nav']):
            text = header.get_text().lower()
            links = header.find_all('a', href=True)
            # Solo mantener si tiene contenido específico de concursos
            if not any(keyword in text for keyword in ['concurso', 'postulacion', 'fecha', 'cierre']) and \
               not any('concurso' in link.get('href', '').lower() for link in links):
                header.decompose()
        
        # Eliminar footers completos
        for footer in soup.find_all('footer'):
            links = footer.find_all('a', href=True)
            if not any('concurso' in link.get('href', '').lower() for link in links):
                footer.decompose()
        
        # Eliminar formularios de búsqueda y otros formularios no relevantes
        for form in soup.find_all('form'):
            parent = form.find_parent(['header', 'nav', 'footer'])
            form_text = form.get_text().lower()
            if parent or ('search' in form_text or 'buscar' in form_text):
                form.decompose()
        
        # Eliminar elementos de redes sociales (iconos, botones, etc.)
        for element in soup.find_all(['a', 'div', 'span'], class_=lambda x: x and any(
            social in ' '.join(x).lower() for social in ['social', 'facebook', 'twitter', 'instagram', 'youtube', 'linkedin']
        )):
            # Solo eliminar si no tiene contenido de concursos
            if 'concurso' not in element.get_text().lower():
                element.decompose()
        
        # Eliminar atributos innecesarios (mantener solo los esenciales)
        important_attrs = {'id', 'class', 'href', 'src', 'alt', 'title'}
        
        for tag in soup.find_all():
            if tag.attrs:
                attrs_to_remove = []
                for attr in list(tag.attrs.keys()):
                    # Eliminar TODOS los atributos data-*
                    if attr.startswith('data-'):
                        attrs_to_remove.append(attr)
                    # Eliminar atributos de estilo inline
                    elif attr == 'style':
                        attrs_to_remove.append(attr)
                    # Eliminar otros atributos no importantes
                    elif attr not in important_attrs:
                        attrs_to_remove.append(attr)
                
                for attr in attrs_to_remove:
                    del tag.attrs[attr]
                
                # Limpiar valores de class - mantener SOLO clases muy relevantes
                if 'class' in tag.attrs:
                    classes = tag.attrs['class']
                    if isinstance(classes, list):
                        semantic_classes = []
                        for cls in classes:
                            # Mantener SOLO clases que son claramente relevantes para concursos
                            if any(keyword in cls.lower() for keyword in [
                                'concurso', 'postulacion', 'fecha', 'cierre', 'apertura', 'fallo', 'resultado',
                                'title', 'heading', 'content', 'main', 'article',
                                'card', 'item', 'list', 'date', 'time', 'status',
                                'grid', 'row', 'col'
                            ]):
                                semantic_classes.append(cls)
                        tag.attrs['class'] = semantic_classes if semantic_classes else None
        
        # Eliminar elementos vacíos o con solo whitespace
        for tag in soup.find_all(['div', 'span', 'p', 'li', 'td', 'th']):
            text = tag.get_text(strip=True)
            # Si no tiene texto y no tiene hijos con contenido relevante
            if not text:
                children_with_content = tag.find_all(['img', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'article', 'section', 'strong', 'em', 'b', 'i'])
                if not children_with_content:
                    # Solo eliminar si no tiene atributos importantes
                    if not any(attr in tag.attrs for attr in ['id', 'class']):
                        tag.decompose()
        
        # Eliminar elementos con solo espacios o caracteres especiales
        for tag in soup.find_all(string=True):
            if isinstance(tag, str) and tag.strip() and len(tag.strip()) < 3:
                # Eliminar strings muy cortos que probablemente son solo espacios/puntuación
                if not any(char.isalnum() for char in tag.strip()):
                    tag.extract()
        
        # Limpiar espacios múltiples y saltos de línea excesivos
        result = str(soup)
        
        # Reducir espacios en blanco múltiples (más agresivo)
        result = re.sub(r'[ \t]+', ' ', result)
        
        # Limpiar saltos de línea múltiples
        result = re.sub(r'\n\s*\n+', '\n', result)
        
        # Eliminar espacios al inicio y final de líneas
        result = re.sub(r'\n\s+', '\n', result)
        result = re.sub(r'\s+\n', '\n', result)
        
        return result.strip()
        
    except Exception as e:
        # Si falla el parsing, retornar HTML original
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error al sanitizar HTML: {e}. Retornando HTML original.")
        return html


def extract_text_content(html: str) -> str:
    """
    Extrae solo el contenido de texto del HTML, eliminando todo el markup
    
    Args:
        html: HTML a procesar
        
    Returns:
        Texto plano extraído
    """
    if not html:
        return ""
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Eliminar scripts y styles
        for element in soup.find_all(['script', 'style', 'noscript']):
            element.decompose()
        
        # Extraer texto preservando estructura básica
        text = soup.get_text(separator='\n', strip=True)
        
        # Limpiar espacios múltiples
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n\s*\n+', '\n', text)
        
        return text.strip()
        
    except Exception:
        return html

