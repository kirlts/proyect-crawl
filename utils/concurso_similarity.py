"""
Utilidades para detectar concursos similares

Detecta cuando dos concursos son esencialmente el mismo pero de diferentes años
o con variaciones menores en el nombre.
"""

import re
from typing import Tuple, Optional
from difflib import SequenceMatcher
import logging

logger = logging.getLogger(__name__)


def normalize_concurso_name(nombre: str) -> str:
    """
    Normaliza el nombre de un concurso para comparación.
    
    Elimina años, números de versión, y normaliza espacios.
    
    Args:
        nombre: Nombre del concurso
        
    Returns:
        Nombre normalizado
    """
    if not nombre:
        return ""
    
    # Convertir a minúsculas
    normalized = nombre.lower().strip()
    
    # Eliminar años (2024, 2025, etc.)
    normalized = re.sub(r'\b20\d{2}\b', '', normalized)
    
    # Eliminar números de versión comunes (v1, v2, versión 1, etc.)
    normalized = re.sub(r'\b(v|versi[oó]n|version)\s*\d+\b', '', normalized, flags=re.IGNORECASE)
    
    # Eliminar palabras comunes que no aportan (año académico, etc.)
    normalized = re.sub(r'\b(año académico|año|year)\s*\d*\b', '', normalized, flags=re.IGNORECASE)
    
    # Normalizar espacios múltiples
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Eliminar caracteres especiales al inicio/final
    normalized = normalized.strip('.,;:!?-_()[]{}')
    
    return normalized.strip()


def extract_year_from_name(nombre: str) -> Optional[int]:
    """
    Extrae el año de un nombre de concurso.
    
    Args:
        nombre: Nombre del concurso
        
    Returns:
        Año encontrado o None
    """
    if not nombre:
        return None
    
    # Buscar años de 4 dígitos (2000-2099)
    matches = re.findall(r'\b(20\d{2})\b', nombre)
    if matches:
        try:
            return int(matches[-1])  # Tomar el último año encontrado
        except:
            pass
    
    return None


def calculate_name_similarity(nombre1: str, nombre2: str) -> float:
    """
    Calcula la similitud entre dos nombres de concurso (0.0 a 1.0).
    
    Args:
        nombre1: Primer nombre
        nombre2: Segundo nombre
        
    Returns:
        Score de similitud (0.0 = completamente diferente, 1.0 = idéntico)
    """
    if not nombre1 or not nombre2:
        return 0.0
    
    # Normalizar ambos nombres
    norm1 = normalize_concurso_name(nombre1)
    norm2 = normalize_concurso_name(nombre2)
    
    if not norm1 or not norm2:
        return 0.0
    
    # Si son idénticos después de normalizar, similitud perfecta
    if norm1 == norm2:
        return 1.0
    
    # Calcular similitud usando SequenceMatcher
    similarity = SequenceMatcher(None, norm1, norm2).ratio()
    
    # Bonus si tienen palabras clave importantes en común
    words1 = set(norm1.split())
    words2 = set(norm2.split())
    
    # Filtrar palabras muy cortas (artículos, preposiciones)
    words1 = {w for w in words1 if len(w) > 3}
    words2 = {w for w in words2 if len(w) > 3}
    
    if words1 and words2:
        common_words = words1.intersection(words2)
        if common_words:
            # Bonus proporcional a palabras comunes
            word_bonus = len(common_words) / max(len(words1), len(words2))
            similarity = max(similarity, similarity * 0.7 + word_bonus * 0.3)
    
    return similarity


def are_similar_concursos(
    nombre1: str,
    url1: str,
    nombre2: str,
    url2: str,
    similarity_threshold: float = 0.85
) -> Tuple[bool, float, str]:
    """
    Determina si dos concursos son similares (mismo concurso, posiblemente diferente año).
    
    Args:
        nombre1: Nombre del primer concurso
        url1: URL del primer concurso
        nombre2: Nombre del segundo concurso
        url2: URL del segundo concurso
        similarity_threshold: Umbral mínimo de similitud (default: 0.85)
        
    Returns:
        Tupla (son_similares, score_similitud, razon)
    """
    # Si las URLs son idénticas, son el mismo concurso
    if url1.strip() == url2.strip():
        return (True, 1.0, "URLs idénticas")
    
    # Calcular similitud de nombres
    similarity = calculate_name_similarity(nombre1, nombre2)
    
    # Extraer años de los nombres
    year1 = extract_year_from_name(nombre1)
    year2 = extract_year_from_name(nombre2)
    
    # Si tienen años diferentes pero nombres muy similares, probablemente son el mismo concurso
    if year1 and year2 and year1 != year2:
        if similarity >= similarity_threshold:
            return (True, similarity, f"Mismo concurso, años diferentes ({year1} vs {year2})")
    
    # Si la similitud es muy alta, son similares independientemente del año
    if similarity >= similarity_threshold:
        return (True, similarity, f"Nombres muy similares (similitud: {similarity:.2f})")
    
    # Si la similitud es media-alta y las URLs son del mismo dominio y patrón similar
    if similarity >= 0.70:
        from urllib.parse import urlparse
        parsed1 = urlparse(url1)
        parsed2 = urlparse(url2)
        
        # Mismo dominio
        if parsed1.netloc == parsed2.netloc:
            # URLs similares (mismo patrón de ruta)
            path1 = parsed1.path.rstrip('/')
            path2 = parsed2.path.rstrip('/')
            
            # Si las rutas son muy similares (solo difieren en año o versión)
            path_similarity = SequenceMatcher(None, path1, path2).ratio()
            if path_similarity >= 0.8:
                return (True, similarity, f"Nombres y URLs similares (similitud: {similarity:.2f}, path: {path_similarity:.2f})")
    
    return (False, similarity, f"No son similares (similitud: {similarity:.2f})")


def find_similar_concurso_in_list(
    concurso_nombre: str,
    concurso_url: str,
    concursos_list: list,
    similarity_threshold: float = 0.85
) -> Optional[dict]:
    """
    Busca un concurso similar en una lista.
    
    Excluye concursos con la misma URL, ya que queremos encontrar versiones anteriores
    del mismo concurso, no el mismo concurso.
    
    Args:
        concurso_nombre: Nombre del concurso a buscar
        concurso_url: URL del concurso a buscar
        concursos_list: Lista de diccionarios con concursos (deben tener 'nombre' y 'url')
        similarity_threshold: Umbral mínimo de similitud
        
    Returns:
        Diccionario del concurso similar encontrado o None
    """
    best_match = None
    best_similarity = 0.0
    
    # Normalizar URL de búsqueda
    target_url = concurso_url.strip()
    
    for concurso in concursos_list:
        nombre = concurso.get("nombre", "")
        url = concurso.get("url", "")
        
        if not nombre or not url:
            continue
        
        # EXCLUIR concursos con la misma URL (mismo concurso)
        if url.strip() == target_url:
            continue
        
        is_similar, similarity, reason = are_similar_concursos(
            concurso_nombre,
            concurso_url,
            nombre,
            url,
            similarity_threshold
        )
        
        if is_similar and similarity > best_similarity:
            best_similarity = similarity
            best_match = concurso
            best_match["_similarity_score"] = similarity
            best_match["_similarity_reason"] = reason
    
    return best_match

