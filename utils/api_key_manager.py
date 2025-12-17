"""
Gestor de múltiples API keys con rotación automática cuando se alcanza el límite de cuota
"""

import json
import os
import time
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from config import DATA_DIR

logger = logging.getLogger(__name__)


class APIKeyManager:
    """Gestiona múltiples API keys con rotación automática"""
    
    def __init__(self, keys_file: Optional[str] = None):
        """
        Inicializa el gestor de API keys
        
        Args:
            keys_file: Ruta al archivo JSON con las API keys (por defecto: data/.api_keys.json)
        """
        if keys_file is None:
            # Asegurar que el directorio existe
            os.makedirs(DATA_DIR, exist_ok=True)
            keys_file = os.path.join(DATA_DIR, ".api_keys.json")
        
        self.keys_file = keys_file
        self.api_keys: List[str] = []
        self.current_key_index = 0
        self.exhausted_keys: Dict[str, Dict[str, Any]] = {}  # key -> {exhausted_at, retry_after}
        # Estadísticas por key: {key -> {"calls": int, "failed": int, "last_used": str}}
        self.key_stats: Dict[str, Dict[str, Any]] = {}
        self.load_keys()
    
    def load_keys(self) -> None:
        """Carga las API keys desde el archivo"""
        try:
            if os.path.exists(self.keys_file):
                with open(self.keys_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.api_keys = data.get("keys", [])
                    self.exhausted_keys = data.get("exhausted_keys", {})
                    self.current_key_index = data.get("current_index", 0)
                    self.key_stats = data.get("key_stats", {})
                    
                    # Validar que el índice esté en rango
                    if self.current_key_index >= len(self.api_keys):
                        self.current_key_index = 0
                    
                    logger.info(f"Cargadas {len(self.api_keys)} API keys desde {self.keys_file}")
            else:
                logger.warning(f"Archivo de API keys no encontrado: {self.keys_file}")
        except Exception as e:
            logger.error(f"Error al cargar API keys: {e}")
            self.api_keys = []
    
    def save_keys(self) -> bool:
        """Guarda las API keys en el archivo"""
        try:
            data = {
                "keys": self.api_keys,
                "exhausted_keys": self.exhausted_keys,
                "current_index": self.current_key_index,
                "key_stats": self.key_stats,
                "last_updated": datetime.now().isoformat()
            }
            
            with open(self.keys_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            
            # Establecer permisos restrictivos
            os.chmod(self.keys_file, 0o600)
            
            return True
        except Exception as e:
            logger.error(f"Error al guardar API keys: {e}")
            return False
    
    def add_key(self, api_key: str) -> bool:
        """
        Agrega una nueva API key a la lista
        
        Args:
            api_key: API key a agregar
            
        Returns:
            True si se agregó correctamente
        """
        if not api_key or not api_key.strip():
            return False
        
        api_key = api_key.strip()
        
        # Evitar duplicados
        if api_key not in self.api_keys:
            self.api_keys.append(api_key)
            self.save_keys()
            logger.info(f"API key agregada (total: {len(self.api_keys)})")
            return True
        else:
            logger.warning("API key ya existe en la lista")
            return False
    
    def add_keys(self, api_keys: List[str]) -> int:
        """
        Agrega múltiples API keys
        
        Args:
            api_keys: Lista de API keys
            
        Returns:
            Número de keys agregadas
        """
        added = 0
        for key in api_keys:
            if self.add_key(key):
                added += 1
        return added
    
    def remove_key(self, api_key: str) -> bool:
        """
        Elimina una API key de la lista
        
        Args:
            api_key: API key a eliminar
            
        Returns:
            True si se eliminó correctamente
        """
        if api_key in self.api_keys:
            self.api_keys.remove(api_key)
            # Si la key agotada está en exhausted_keys, eliminarla también
            if api_key in self.exhausted_keys:
                del self.exhausted_keys[api_key]
            
            # Ajustar índice si es necesario
            if self.current_key_index >= len(self.api_keys):
                self.current_key_index = 0
            
            self.save_keys()
            logger.info(f"API key eliminada (total: {len(self.api_keys)})")
            return True
        return False
    
    def get_current_key(self) -> Optional[str]:
        """
        Obtiene la API key actual
        
        Returns:
            API key actual o None si no hay keys disponibles
        """
        # Limpiar keys agotadas que ya pueden reutilizarse
        self._clean_exhausted_keys()
        
        if not self.api_keys:
            return None
        
        # Buscar una key disponible (no agotada)
        attempts = 0
        while attempts < len(self.api_keys):
            key = self.api_keys[self.current_key_index]
            
            # Si la key no está agotada, usarla
            if key not in self.exhausted_keys:
                return key
            
            # Si está agotada pero ya pasó el tiempo de retry, limpiarla y usarla
            if self._can_retry_key(key):
                logger.info(f"Reutilizando API key que estaba agotada (esperó suficiente tiempo)")
                if key in self.exhausted_keys:
                    del self.exhausted_keys[key]
                self.save_keys()
                return key
            
            # Intentar siguiente key
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            attempts += 1
        
        # Si todas están agotadas, usar la actual de todas formas
        logger.warning("Todas las API keys están agotadas, usando la actual de todas formas")
        return self.api_keys[self.current_key_index]
    
    def mark_key_exhausted(self, api_key: str, retry_after_seconds: Optional[int] = None) -> None:
        """
        Marca una API key como agotada
        
        Args:
            api_key: API key agotada
            retry_after_seconds: Segundos a esperar antes de reintentar (None = 24 horas por defecto)
        """
        if retry_after_seconds is None:
            # Por defecto, esperar 24 horas (límites diarios típicos)
            retry_after_seconds = 24 * 60 * 60
        
        self.exhausted_keys[api_key] = {
            "exhausted_at": datetime.now().isoformat(),
            "retry_after_seconds": retry_after_seconds,
            "retry_after": (datetime.now() + timedelta(seconds=retry_after_seconds)).isoformat()
        }
        self.save_keys()
        
        # Mensaje más descriptivo según el tipo de límite
        if retry_after_seconds < 60:
            logger.warning(f"API key marcada como agotada (rate limit temporal). Reintentará después de {retry_after_seconds}s")
        elif retry_after_seconds < 3600:
            logger.warning(f"API key marcada como agotada (rate limit). Reintentará después de {retry_after_seconds // 60} minutos")
        else:
            logger.warning(f"API key marcada como agotada (límite diario). Reintentará después de {retry_after_seconds // 3600} horas")
    
    def rotate_to_next_key(self) -> Optional[str]:
        """
        Rota a la siguiente API key disponible
        
        Returns:
            Nueva API key o None si no hay más disponibles
        """
        if not self.api_keys:
            return None
        
        # Limpiar keys agotadas
        self._clean_exhausted_keys()
        
        # Rotar al siguiente índice
        original_index = self.current_key_index
        attempts = 0
        
        while attempts < len(self.api_keys):
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            key = self.api_keys[self.current_key_index]
            
            # Si la key no está agotada o puede reutilizarse, usarla
            if key not in self.exhausted_keys or self._can_retry_key(key):
                if key in self.exhausted_keys:
                    del self.exhausted_keys[key]
                    self.save_keys()
                
                # Log eliminado: se registra en gemini_client.py para evitar redundancia
                return key
            
            attempts += 1
        
        # Si todas están agotadas, rotar de todas formas
        self.current_key_index = (original_index + 1) % len(self.api_keys)
        logger.warning(f"Todas las keys están agotadas, rotando a índice {self.current_key_index}")
        return self.api_keys[self.current_key_index]
    
    def _can_retry_key(self, api_key: str) -> bool:
        """Verifica si una key agotada puede reintentarse"""
        if api_key not in self.exhausted_keys:
            return True
        
        exhausted_info = self.exhausted_keys[api_key]
        retry_after_str = exhausted_info.get("retry_after")
        
        if not retry_after_str:
            return True
        
        try:
            retry_after = datetime.fromisoformat(retry_after_str)
            return datetime.now() >= retry_after
        except:
            return True
    
    def _clean_exhausted_keys(self) -> None:
        """Limpia las keys agotadas que ya pueden reutilizarse"""
        keys_to_remove = []
        for key, info in self.exhausted_keys.items():
            if self._can_retry_key(key):
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self.exhausted_keys[key]
        
        if keys_to_remove:
            self.save_keys()
            logger.info(f"Limpiadas {len(keys_to_remove)} API keys que ya pueden reutilizarse")
    
    def record_api_call(self, api_key: str, success: bool = True) -> None:
        """
        Registra una llamada a la API para una key específica
        
        Args:
            api_key: API key utilizada
            success: True si la llamada fue exitosa, False si falló
        """
        if api_key not in self.key_stats:
            self.key_stats[api_key] = {
                "calls": 0,
                "failed": 0,
                "last_used": None
            }
        
        self.key_stats[api_key]["calls"] += 1
        if not success:
            self.key_stats[api_key]["failed"] += 1
        self.key_stats[api_key]["last_used"] = datetime.now().isoformat()
        
        # Guardar automáticamente
        self.save_keys()
    
    def get_key_stats(self, api_key: str) -> Dict[str, Any]:
        """
        Obtiene estadísticas de una API key específica
        
        Args:
            api_key: API key a consultar
            
        Returns:
            Diccionario con estadísticas o None si no existe
        """
        return self.key_stats.get(api_key, {
            "calls": 0,
            "failed": 0,
            "last_used": None
        })
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Obtiene estadísticas de todas las keys
        
        Returns:
            Diccionario con estadísticas por key
        """
        return self.key_stats.copy()
    
    def get_total_stats(self) -> Dict[str, int]:
        """
        Obtiene estadísticas totales de todas las keys
        
        Returns:
            Diccionario con totales: {"total_calls": int, "total_failed": int}
        """
        total_calls = sum(stats.get("calls", 0) for stats in self.key_stats.values())
        total_failed = sum(stats.get("failed", 0) for stats in self.key_stats.values())
        
        return {
            "total_calls": total_calls,
            "total_failed": total_failed,
            "total_success": total_calls - total_failed
        }
    
    def get_status(self) -> Dict[str, Any]:
        """
        Obtiene el estado actual del gestor
        
        Returns:
            Diccionario con información del estado
        """
        self._clean_exhausted_keys()
        
        available_keys = [key for key in self.api_keys if key not in self.exhausted_keys]
        current_key = self.get_current_key()
        current_key_stats = self.get_key_stats(current_key) if current_key else {}
        total_stats = self.get_total_stats()
        
        return {
            "total_keys": len(self.api_keys),
            "available_keys": len(available_keys),
            "exhausted_keys": len(self.exhausted_keys),
            "current_index": self.current_key_index,
            "current_key": current_key[:20] + "..." + current_key[-10:] if current_key else None,
            "current_key_stats": current_key_stats,
            "total_stats": total_stats,
            "exhausted_keys_info": {
                key: {
                    "exhausted_at": info.get("exhausted_at"),
                    "retry_after": info.get("retry_after"),
                    "can_retry": self._can_retry_key(key)
                }
                for key, info in self.exhausted_keys.items()
            }
        }
    
    def clear_all_keys(self) -> None:
        """Elimina todas las API keys"""
        self.api_keys = []
        self.exhausted_keys = {}
        self.current_key_index = 0
        self.save_keys()
        logger.info("Todas las API keys han sido eliminadas")

