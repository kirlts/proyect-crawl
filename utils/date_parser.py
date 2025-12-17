"""
Utilidades para parsing y procesamiento de fechas
"""

import re
from datetime import datetime, timedelta
from dateutil import parser as date_parser
from typing import Optional, Tuple


def parse_date(date_str: str) -> Optional[datetime]:
    """
    Intenta parsear una fecha en varios formatos comunes en Chile
    
    Args:
        date_str: String con la fecha
        
    Returns:
        datetime object o None si no se puede parsear
    """
    if not date_str or not isinstance(date_str, str):
        return None
    
    # Limpiar el string
    date_str = date_str.strip()
    
    # Si ya está en formato YYYY-MM-DD, parsearlo directamente (no usar dayfirst)
    if re.match(r'^\d{4}-\d{1,2}-\d{1,2}$', date_str):
        try:
            parts = date_str.split('-')
            return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
        except:
            pass
    
    # Intentar con dateutil (muy flexible)
    # Usar dayfirst=True solo si NO parece ser formato YYYY-MM-DD
    try:
        return date_parser.parse(date_str, dayfirst=True)
    except:
        pass
    
    # Patrones comunes en Chile
    # Primero intentar formato con coma: "10 de diciembre, 2025"
    pattern_comma = r"(\d{1,2})\s+de\s+(\w+)\s*,\s*(\d{4})"
    match_comma = re.search(pattern_comma, date_str, re.IGNORECASE)
    if match_comma:
        try:
            day = int(match_comma.group(1))
            month_name = match_comma.group(2).lower()
            year = int(match_comma.group(3))
            
            months_es = {
                "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
                "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
                "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
            }
            
            month = months_es.get(month_name)
            if month:
                return datetime(year, month, day)
        except:
            pass
    
    # Luego intentar otros patrones
    patterns = [
        r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})",  # DD/MM/YYYY o DD-MM-YYYY
        r"(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})",  # YYYY/MM/DD o YYYY-MM-DD
        r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})",  # "15 de marzo de 2024"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, date_str, re.IGNORECASE)
        if match:
            try:
                if "de" in date_str.lower():
                    # Formato "15 de marzo de 2024"
                    day = int(match.group(1))
                    month_name = match.group(2).lower()
                    year = int(match.group(3))
                    
                    months_es = {
                        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
                        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
                        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
                    }
                    
                    month = months_es.get(month_name)
                    if month:
                        return datetime(year, month, day)
                else:
                    # Formato numérico
                    parts = match.groups()
                    if len(parts[0]) == 4:  # YYYY-MM-DD
                        return datetime(int(parts[0]), int(parts[1]), int(parts[2]))
                    else:  # DD-MM-YYYY
                        return datetime(int(parts[2]), int(parts[1]), int(parts[0]))
            except:
                continue
    
    return None


def is_past_date(date_str: str) -> bool:
    """
    Determina si una fecha es pasada
    
    Args:
        date_str: String con la fecha
        
    Returns:
        True si la fecha es pasada, False si es futura o no se puede determinar
    """
    parsed = parse_date(date_str)
    if parsed is None:
        return False
    
    return parsed < datetime.now()


def estimate_next_opening(
    fecha_cierre: str,
    fecha_cierre_original: str,
    current_year: Optional[int] = None
) -> Tuple[Optional[str], str]:
    """
    Estima la próxima apertura de un concurso basándose en la fecha de cierre
    
    Args:
        fecha_cierre: Fecha de cierre parseada o texto
        fecha_cierre_original: Texto original de la fecha
        current_year: Año actual (default: año actual del sistema)
        
    Returns:
        Tupla (fecha_estimada, confianza) donde:
        - fecha_estimada: "YYYY-MM-DD" o None
        - confianza: "Alto", "Medio" o "Bajo"
    """
    if current_year is None:
        current_year = datetime.now().year
    
    # Intentar parsear la fecha
    parsed_date = parse_date(fecha_cierre)
    
    if parsed_date is None:
        # Si no se puede parsear, intentar extraer mes/año del texto original
        month_match = re.search(r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)", 
                                fecha_cierre_original.lower())
        year_match = re.search(r"20\d{2}", fecha_cierre_original)
        
        if month_match and year_match:
            months_es = {
                "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
                "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
                "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
            }
            month = months_es.get(month_match.group(1))
            year = int(year_match.group(0))
            
            if month:
                # Asumir día 1 del mes
                parsed_date = datetime(year, month, 1)
    
    if parsed_date is None:
        return None, "Bajo"
    
    # Si la fecha es futura, no necesita predicción
    if parsed_date >= datetime.now():
        return None, "Bajo"
    
    # Calcular diferencia en años
    years_passed = current_year - parsed_date.year
    
    # Estimar próxima apertura (mismo mes, año siguiente)
    next_year = current_year + 1
    estimated_date = datetime(next_year, parsed_date.month, min(parsed_date.day, 28))
    
    # Ajustar si ya pasó ese mes este año
    if parsed_date.month < datetime.now().month:
        # Ya pasó este año, estimar para el próximo año
        estimated_date = datetime(next_year, parsed_date.month, min(parsed_date.day, 28))
    elif parsed_date.month == datetime.now().month:
        # Mismo mes, verificar si ya pasó el día
        if parsed_date.day < datetime.now().day:
            estimated_date = datetime(next_year, parsed_date.month, min(parsed_date.day, 28))
        else:
            # Aún no ha pasado, podría ser este año
            estimated_date = datetime(current_year, parsed_date.month, parsed_date.day)
    
    # Determinar confianza
    if years_passed == 0:
        confidence = "Alto"  # Muy reciente, patrón claro
    elif years_passed <= 2:
        confidence = "Medio"  # Patrón razonable
    else:
        confidence = "Bajo"  # Muy antiguo, menos confiable
    
    return estimated_date.strftime("%Y-%m-%d"), confidence

