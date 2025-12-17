"""
Utilidades para procesar y optimizar markdown antes de enviarlo a Gemini
"""

import re
from typing import List


def chunk_markdown(markdown: str, max_chunk_size: int = 500000) -> List[str]:
    """
    Divide el markdown en chunks si es muy largo
    
    Args:
        markdown: Contenido markdown a dividir
        max_chunk_size: Tamaño máximo por chunk en caracteres
        
    Returns:
        Lista de chunks
    """
    if len(markdown) <= max_chunk_size:
        return [markdown]
    
    chunks = []
    current_chunk = ""
    
    # Intentar dividir por párrafos (doble salto de línea)
    paragraphs = markdown.split("\n\n")
    
    for paragraph in paragraphs:
        # Si agregar este párrafo excede el límite, guardar chunk actual
        if len(current_chunk) + len(paragraph) + 2 > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = paragraph
            else:
                # Si un párrafo individual es muy largo, dividirlo por líneas
                lines = paragraph.split("\n")
                for line in lines:
                    if len(current_chunk) + len(line) + 1 > max_chunk_size:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = line
                    else:
                        current_chunk += "\n" + line if current_chunk else line
        else:
            current_chunk += "\n\n" + paragraph if current_chunk else paragraph
    
    # Agregar último chunk
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks


def clean_markdown_for_llm(markdown: str) -> str:
    """
    Limpia el markdown para optimizar el procesamiento con LLM
    Elimina caracteres innecesarios, espacios excesivos, y contenido no relevante
    
    Args:
        markdown: Markdown a limpiar
        
    Returns:
        Markdown limpio y optimizado
    """
    if not markdown:
        return ""
    
    # 1. Eliminar caracteres de control y no imprimibles (excepto saltos de línea y tabs)
    markdown = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F]', '', markdown)
    
    # 2. Eliminar URLs de imágenes que no aportan información (solo mantener alt text si existe)
    # Patrón: ![alt](url) -> alt text o eliminar si no hay alt
    markdown = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', r'\1', markdown)
    
    # 3. Eliminar enlaces que son solo URLs sin texto descriptivo
    # Patrón: [url](url) -> url
    markdown = re.sub(r'\[([^\]]+)\]\(\1\)', r'\1', markdown)
    
    # 4. Eliminar caracteres especiales repetidos (más de 2 seguidos)
    # Ejemplo: "---" se mantiene, pero "------" se reduce a "---"
    markdown = re.sub(r'([\-_=*#])\1{3,}', r'\1\1\1', markdown)
    
    # 5. Eliminar espacios en blanco múltiples (más de 2 espacios seguidos)
    markdown = re.sub(r' {3,}', ' ', markdown)
    
    # 6. Eliminar tabs múltiples
    markdown = re.sub(r'\t+', ' ', markdown)
    
    # 7. Eliminar líneas que solo contienen caracteres especiales o espacios
    lines = markdown.split("\n")
    cleaned_lines = []
    empty_count = 0
    
    for line in lines:
        stripped = line.strip()
        
        # Eliminar líneas que solo tienen caracteres especiales
        if stripped and not re.match(r'^[\s\-_=*#\.]+$', stripped):
            # Es una línea con contenido real
            empty_count = 0
            cleaned_lines.append(line)
        elif stripped == "":
            # Línea vacía - limitar a máximo 2 seguidas
            empty_count += 1
            if empty_count <= 2:
                cleaned_lines.append("")
            # Si hay más de 2 vacías seguidas, no agregar
        else:
            # Línea con solo caracteres especiales - eliminar
            pass
    
    markdown = "\n".join(cleaned_lines)
    
    # 8. Eliminar bloques de código vacíos o con solo caracteres especiales
    markdown = re.sub(r'```[^\n]*\n[\s\-\_=*#\.]*\n```', '', markdown, flags=re.MULTILINE)
    
    # 9. Limpiar espacios alrededor de saltos de línea
    markdown = re.sub(r' +\n', '\n', markdown)
    markdown = re.sub(r'\n +', '\n', markdown)
    
    # 10. Eliminar líneas que son solo números o fechas sin contexto (muy cortas)
    # Pero mantener fechas que son parte de concursos
    lines = markdown.split("\n")
    final_lines = []
    for line in lines:
        stripped = line.strip()
        # Si la línea es muy corta (menos de 3 caracteres) y solo tiene números/puntuación, eliminar
        if len(stripped) < 3 and re.match(r'^[\d\s\.\-\/]+$', stripped):
            continue
        # Mantener líneas con contenido relevante
        final_lines.append(line)
    
    markdown = "\n".join(final_lines)
    
    # 11. Eliminar múltiples saltos de línea al final
    markdown = markdown.rstrip()
    
    # 12. Normalizar saltos de línea múltiples (máximo 2 seguidos)
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    
    return markdown.strip()

