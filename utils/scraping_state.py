"""
Gestor de estado persistente para scraping, permitiendo cancelación
desde cualquier sesión de Streamlit.
"""

import os
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any

from config import DATA_DIR

STATE_DIR = Path(DATA_DIR) / "scraping_state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "current_scraping.json"


def save_scraping_state(site: str, in_progress: bool, should_stop: bool = False) -> None:
    """
    Guarda el estado de scraping en disco.
    
    Args:
        site: Nombre del sitio siendo scrapeado
        in_progress: Si el scraping está en progreso
        should_stop: Si se solicitó cancelación
    """
    state = {
        "site": site,
        "in_progress": in_progress,
        "should_stop": should_stop,
        "timestamp": time.time()
    }
    
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error al guardar estado de scraping: {e}")


def load_scraping_state() -> Optional[Dict[str, Any]]:
    """
    Carga el estado de scraping desde disco.
    
    Returns:
        Diccionario con el estado o None si no existe
    """
    if not STATE_FILE.exists():
        return None
    
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        
        # Verificar si el estado es muy antiguo (> 30 minutos) y limpiarlo
        timestamp = state.get("timestamp", 0)
        if time.time() - timestamp > 1800:  # 30 minutos
            clear_scraping_state()
            return None
        
        return state
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error al cargar estado de scraping: {e}")
        return None


def clear_scraping_state() -> None:
    """
    Limpia el estado de scraping del disco.
    """
    try:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error al limpiar estado de scraping: {e}")


def set_should_stop(should_stop: bool = True) -> None:
    """
    Establece el flag de cancelación en el estado persistente.
    
    Args:
        should_stop: Si se debe detener el scraping
    """
    state = load_scraping_state()
    if state:
        state["should_stop"] = should_stop
        state["timestamp"] = time.time()
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error al actualizar estado de scraping: {e}")


def get_should_stop() -> bool:
    """
    Obtiene el flag de cancelación desde el estado persistente.
    
    Returns:
        True si se debe detener el scraping
    """
    state = load_scraping_state()
    if state:
        return state.get("should_stop", False)
    return False


def is_scraping_in_progress() -> bool:
    """
    Verifica si hay un scraping en progreso según el estado persistente.
    
    Returns:
        True si hay un scraping en progreso
    """
    state = load_scraping_state()
    if state:
        return state.get("in_progress", False)
    return False

