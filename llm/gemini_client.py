"""
Cliente para interactuar con Gemini API

Solo maneja la gestión de API keys y rotación automática.
La lógica de extracción está en llm.extractors.llm_extractor y llm.predictor.
Todas las llamadas a la API se hacen directamente vía REST para usar Structured Outputs.
"""

import logging
from typing import Optional, Dict, Any
from utils.api_key_manager import APIKeyManager

logger = logging.getLogger(__name__)


class GeminiClient:
    """
    Cliente simplificado para Gemini API.
    
    Solo gestiona API keys y rotación. Las llamadas reales a la API
    se hacen directamente vía REST en llm_extractor.py y predictor.py
    para aprovechar Structured Outputs.
    """
    
    def __init__(self, api_key: Optional[str] = None, api_key_manager=None, config: Optional[Dict[str, Any]] = None):
        """
        Inicializa el cliente de Gemini.
        
        Args:
            api_key: API key única (opcional, si se usa api_key_manager)
            api_key_manager: Instancia de APIKeyManager para rotación automática (opcional)
            config: Configuración adicional (model, temperature, etc.)
        """
        self.config = config or {}
        self.model_name = self.config.get("model", "gemini-2.5-flash-lite")
        self.temperature = self.config.get("temperature", 0.1)
        self.max_output_tokens = self.config.get("max_output_tokens", 8000)
        
        # Usar APIKeyManager si se proporciona, sino usar api_key única
        if api_key_manager is not None:
            self.api_key_manager = api_key_manager
            self.use_key_manager = True
        elif api_key:
            # Crear un manager temporal con una sola key para compatibilidad
            self.api_key_manager = APIKeyManager()
            self.api_key_manager.add_key(api_key)
            self.use_key_manager = True
        else:
            # Intentar cargar desde archivo
            self.api_key_manager = APIKeyManager()
            if len(self.api_key_manager.api_keys) > 0:
                self.use_key_manager = True
            else:
                raise ValueError("Se requiere api_key o api_key_manager con al menos una key")
        
        # Obtener la key actual
        self._update_api_key()
    
    def _update_api_key(self) -> bool:
        """
        Actualiza la API key actual desde el manager.
        
        Returns:
            True si se actualizó correctamente
        """
        current_key = self.api_key_manager.get_current_key()
        if not current_key:
            logger.error("No hay API keys disponibles")
            return False
        
        self.api_key = current_key
        return True
    
    @property
    def api_key(self) -> str:
        """Obtiene la API key actual"""
        return self._api_key
    
    @api_key.setter
    def api_key(self, value: str):
        """Establece la API key actual"""
        self._api_key = value
    
    def _handle_quota_error(self, error: Exception, retry_after_seconds: Optional[int] = None) -> bool:
        """
        Maneja errores de cuota (429) rotando a la siguiente API key.
        
        Nota: los timeouts ya no se tratan como errores de cuota para evitar marcar
        todas las keys como agotadas cuando hay problemas de red o batches muy pesados.
        
        Args:
            error: Excepción recibida
            retry_after_seconds: Segundos a esperar antes de reintentar (extraído del error si es posible)
            
        Returns:
            True si se pudo rotar a otra key, False si no hay más keys disponibles
        """
        error_str = str(error)
        error_type = type(error).__name__
        
        # Detectar error 429 (quota exceeded)
        is_quota_error = (
            "429" in error_str
            or "quota" in error_str.lower()
            or "ResourceExhausted" in error_type
            or (hasattr(error, "status_code") and error.status_code == 429)
        )
        
        if is_quota_error:
            logger.warning(f"⚠️ Cuota excedida con API key actual. Intentando rotar...")
            
            # Intentar extraer retry_after del error si está disponible
            if retry_after_seconds is None:
                # Buscar "retry in X seconds" en el mensaje de error
                import re

                retry_match = re.search(r"retry in ([\d.]+)s", error_str, re.IGNORECASE)
                if retry_match:
                    retry_after_seconds = int(float(retry_match.group(1)))
                else:
                    # Por defecto, esperar 24 horas para límites diarios
                    retry_after_seconds = 24 * 60 * 60
            
            # Marcar la key actual como agotada
            self.api_key_manager.mark_key_exhausted(self.api_key, retry_after_seconds)
            
            # Rotar a la siguiente key
            next_key = self.api_key_manager.rotate_to_next_key()
            if next_key:
                logger.info(f"✅ Rotado a nueva API key ({self.api_key_manager.current_key_index + 1}/{len(self.api_key_manager.api_keys)})")
                return self._update_api_key()
            else:
                logger.error("❌ No hay más API keys disponibles")
                return False
        
        return False
