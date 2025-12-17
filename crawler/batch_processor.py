"""
Procesador de batches inteligente para agrupar contenido de múltiples páginas
hasta un límite de caracteres antes de enviar al LLM
"""

from typing import List, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)


def create_batches(
    page_contents: List[Dict[str, Any]], 
    batch_size: int = 500000
) -> List[Tuple[List[Dict[str, Any]], str]]:
    """
    Agrupa contenido de múltiples páginas en batches hasta el límite de caracteres
    
    Args:
        page_contents: Lista de diccionarios con contenido de páginas. Cada dict debe tener:
            - "markdown_cleaned": markdown limpio de la página
            - "url": URL de origen
            - Cualquier otro metadata necesario
        batch_size: Tamaño máximo por batch en caracteres (default: 500,000)
        
    Returns:
        Lista de tuplas (pages_in_batch, combined_markdown) donde:
            - pages_in_batch: Lista de dicts de páginas incluidas en este batch
            - combined_markdown: Markdown combinado de todas las páginas del batch
    """
    if not page_contents:
        return []
    
    batches = []
    current_batch_pages = []
    current_batch_size = 0
    separator = "\n\n---\n\n"  # Separador entre páginas
    
    for page_data in page_contents:
        markdown = page_data.get("markdown_cleaned", "")
        if not markdown:
            logger.warning(f"Página {page_data.get('url', 'unknown')} no tiene markdown_cleaned, omitiendo")
            continue
        
        markdown_size = len(markdown)
        separator_size = len(separator) if current_batch_pages else 0
        
        # Si agregar esta página excede el límite, crear un nuevo batch
        if current_batch_pages and (current_batch_size + separator_size + markdown_size > batch_size):
            # Crear batch con las páginas acumuladas
            combined_markdown = separator.join([
                page.get("markdown_cleaned", "") 
                for page in current_batch_pages
            ])
            batches.append((current_batch_pages.copy(), combined_markdown))
            
            # Iniciar nuevo batch con esta página
            current_batch_pages = [page_data]
            current_batch_size = markdown_size
        else:
            # Agregar página al batch actual
            current_batch_pages.append(page_data)
            current_batch_size += separator_size + markdown_size
    
    # Agregar último batch si tiene contenido
    if current_batch_pages:
        combined_markdown = separator.join([
            page.get("markdown_cleaned", "") 
            for page in current_batch_pages
        ])
        batches.append((current_batch_pages, combined_markdown))
    
    logger.info(f"📦 Creados {len(batches)} batches desde {len(page_contents)} páginas")
    for i, (pages, markdown) in enumerate(batches):
        logger.info(f"  Batch {i+1}: {len(pages)} páginas, {len(markdown):,} caracteres")
    
    return batches


def extract_urls_from_batch(pages_in_batch: List[Dict[str, Any]]) -> str:
    """
    Extrae las URLs de las páginas en un batch para usar como contexto
    
    Args:
        pages_in_batch: Lista de diccionarios de páginas
        
    Returns:
        String con las URLs separadas por comas o la URL principal
    """
    urls = [page.get("url", "") for page in pages_in_batch if page.get("url")]
    if len(urls) == 1:
        return urls[0]
    elif len(urls) > 1:
        return f"{urls[0]} (+{len(urls)-1} páginas más)"
    return ""

