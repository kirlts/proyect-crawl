"""
Extractor de concursos usando LLM

Separa la lógica de extracción del cliente de API.
Utiliza GeminiClient para las llamadas a la API.
"""

import json
import logging
import time
import requests
import traceback
from datetime import datetime
from typing import List, Optional, Dict, Any

from models import Concurso, ConcursoResponse
from llm.gemini_client import GeminiClient
from llm.prompts import get_system_prompt, get_extraction_prompt
from config import EXTRACTION_CONFIG

logger = logging.getLogger(__name__)


class LLMExtractor:
    """
    Extractor de concursos usando LLM.
    
    Encapsula la lógica de:
    - Construcción de prompts
    - Llamadas al LLM con manejo de errores
    - Parsing y validación de respuestas
    - Transformación a modelos Concurso
    """
    
    def __init__(
        self,
        api_key_manager,
        model_name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        """
        Inicializa el extractor LLM.
        
        Args:
            api_key_manager: Gestor de API keys
            model_name: Nombre del modelo a usar (opcional)
            config: Configuración adicional (opcional)
        """
        self.api_key_manager = api_key_manager
        self.config = config or {}
        
        # Inicializar cliente de Gemini
        gemini_config = self.config.copy()
        if model_name:
            gemini_config["model"] = model_name
        
        self.gemini_client = GeminiClient(
            api_key_manager=api_key_manager,
            config=gemini_config
        )
        
        # Guardar configuración para acceso a timeouts
        self.extraction_config = EXTRACTION_CONFIG
    
    def extract_from_markdown(
        self,
        url: str,
        markdown: str,
        already_cleaned: bool = False
    ) -> tuple[List[Concurso], Dict[str, Any]]:
        """
        Extrae concursos de un markdown.
        
        Args:
            url: URL de origen del contenido
            markdown: Contenido markdown a analizar
            already_cleaned: Si True, asume que el markdown ya está limpio
            
        Returns:
            Tupla (lista de objetos Concurso extraídos, datos crudos para auditoría)
        """
        # Limpiar markdown si es necesario
        if not already_cleaned:
            from crawler.markdown_processor import clean_markdown_for_llm
            cleaned_markdown = clean_markdown_for_llm(markdown)
        else:
            cleaned_markdown = markdown
        
        # Generar prompt (sin URL, se asigna después programáticamente)
        system_prompt = get_system_prompt()
        extraction_prompt = get_extraction_prompt(cleaned_markdown)
        full_prompt = f"{system_prompt}\n\n{extraction_prompt}"
        
        # Llamar a Gemini
        logger.info(f"Enviando contenido a Gemini para {url} (tamaño: {len(cleaned_markdown)} caracteres)")
        
        response = self._call_llm_with_retry(full_prompt, url)
        
        # Parsear y validar respuesta (sin URL, se asignará después programáticamente)
        concursos = self._parse_response(response)
        
        # Preparar datos crudos para auditoría
        raw_data = {
            "url": url,
            "markdown_size": len(cleaned_markdown),
            "llm_response": response,
            "llm_response_size": len(response),
            "concursos_extraidos": len(concursos),
            "concursos": [c.model_dump() for c in concursos] if concursos else []
        }
        
        return concursos, raw_data
    
    def extract_from_batch(
        self,
        markdown_batch: str,
        urls_in_batch: List[str]
    ) -> tuple[List[Concurso], Dict[str, Any]]:
        """
        Extrae concursos de un batch de markdown (múltiples páginas combinadas).
        
        Args:
            markdown_batch: Markdown combinado de múltiples páginas
            urls_in_batch: Lista de URLs que fueron agrupadas en este batch
            
        Returns:
            Tupla (lista de objetos Concurso extraídos, datos crudos para auditoría)
        """
        # Crear prompt para batch
        system_prompt = get_system_prompt()
        
        # Construir prompt de batch
        num_pages = len(urls_in_batch)
        expected_concursos_per_page = 6  # ANID tiene 6 concursos por página (excepto la última)
        expected_total = num_pages * expected_concursos_per_page
        
        batch_prompt = f"""Analiza el siguiente contenido markdown extraído de {num_pages} páginas y extrae TODOS los concursos u oportunidades de financiamiento que encuentres.

INSTRUCCIONES CRÍTICAS:
- Este batch contiene {num_pages} páginas de resultados
- Cada página típicamente contiene aproximadamente 6 concursos (excepto posiblemente la última página)
- Extrae TODOS los concursos que encuentres, sin omitir ninguno
- Si una página tiene menos de 6 concursos, extrae exactamente los que encuentres
- Si una página tiene más de 6 concursos, extrae TODOS sin excepción
- Extrae TODOS los concursos que encuentres en el contenido

Para cada concurso, extrae:
1. **nombre** (REQUERIDO): Nombre completo del concurso
2. **fecha_apertura**: Texto original tal como aparece (ej: "10 de diciembre, 2025"). Busca "Apertura:", "Inicio:", "Desde:". Si no encuentras, usa null.
3. **fecha_cierre**: Texto original tal como aparece (ej: "19 de marzo, 2026 - 17:00"). Busca "Cierre:", "Fecha de cierre:", "Hasta:", "Vence:". Incluye hora si está presente. Si no encuentras, usa null.
4. **organismo** (REQUERIDO): Organismo administrador (ej: "ANID", "MINEDUC", "CNA"). Infiere desde contexto si no está explícito.
5. **financiamiento**: Monto o tipo disponible. Busca "monto", "financiamiento", "presupuesto", "$", "hasta", "entre", "máximo", "mínimo". Si no encuentras, usa null.
6. **descripcion** (opcional): Resumen breve del concurso
7. **subdireccion** (opcional): Subdirección o área del organismo

IMPORTANTE: 
- Extrae el TEXTO ORIGINAL de las fechas tal como aparecen en el contenido
- Retorna SOLO un JSON válido con este formato exacto: {{"concursos": [...]}}
- Los campos "nombre" y "organismo" son OBLIGATORIOS. Si faltan, no incluyas ese concurso.
- Si encuentras un concurso, inclúyelo en la respuesta.

CONTENIDO A ANALIZAR (separado por páginas con "---"):
{markdown_batch}"""
        
        full_prompt = f"{system_prompt}\n\n{batch_prompt}"
        
        # Llamar a Gemini
        logger.info(
            f"Enviando batch al LLM (URLs: {len(urls_in_batch)}, "
            f"tamaño: {len(markdown_batch):,} caracteres)"
        )
        
        response = self._call_llm_with_retry(full_prompt, urls_in_batch[0] if urls_in_batch else "unknown")
        
        # Parsear y validar respuesta (sin URL, se asignará después programáticamente)
        concursos = self._parse_response(response)
        
        # Preparar datos crudos para auditoría
        raw_data = {
            "urls": urls_in_batch,
            "markdown_size": len(markdown_batch),
            "llm_response": response,
            "llm_response_size": len(response),
            "concursos_extraidos": len(concursos),
            "concursos": [c.model_dump() for c in concursos] if concursos else []
        }
        
        return concursos, raw_data
    
    def _call_llm_with_retry(self, prompt: str, url: str) -> str:
        """
        Llama al LLM con manejo de errores y reintentos.
        Usa Structured Outputs para garantizar formato JSON correcto.
        
        Args:
            prompt: Prompt completo a enviar
            url: URL de origen (para logging)
            
        Returns:
            Texto de respuesta del LLM (JSON válido según el esquema)
        """
        from models import ConcursoResponse
        
        # Número máximo de reintentos: respetar EXTRACTION_CONFIG y no quemar todas las keys en un solo batch
        configured_retries = self.extraction_config.get("max_retries", 3) if hasattr(self, "extraction_config") else 3
        total_keys = len(self.api_key_manager.api_keys) if self.api_key_manager else 1
        max_retries = min(configured_retries, total_keys)
        last_error = None
        rate_limit_retry_times = []  # Rastrear tiempos de retry de rate limits temporales
        
        # Obtener el esquema JSON del modelo Pydantic y modificarlo
        json_schema = ConcursoResponse.model_json_schema()
        
        # Modificar el esquema:
        # 1. Las fechas deben aceptar texto original, no formato específico
        # 2. Eliminar campos que NO debe pensar el LLM (estado, URL, metadatos)
        if "properties" in json_schema and "concursos" in json_schema["properties"]:
            concursos_schema = json_schema["properties"]["concursos"]
            if "items" in concursos_schema and "properties" in concursos_schema["items"]:
                item_props = concursos_schema["items"]["properties"]
                
                # Asegurar que fecha_apertura y fecha_cierre sean strings sin formato específico
                for fecha_field in ["fecha_apertura", "fecha_cierre"]:
                    if fecha_field in item_props:
                        item_props[fecha_field] = {
                            "type": "string",
                            "description": item_props[fecha_field].get("description", ""),
                            "title": item_props[fecha_field].get("title", fecha_field)
                        }
                
                # Eliminar campos calculados automáticamente o manejados por el sistema
                fields_to_remove = [
                    "estado",  # Calculado desde fechas
                    "predicted_opening",  # Calculado por el sistema
                    "extraido_en",  # Agregado por el sistema
                    "fuente",  # Agregado por el sistema
                    "fecha_apertura_original",  # Duplicado de fecha_apertura
                    "url",  # La URL se obtiene SIEMPRE de forma programática desde el HTML, no del LLM
                ]
                
                for field in fields_to_remove:
                    if field in item_props:
                        del item_props[field]
                
                # Actualizar required fields si existe
                if "required" in concursos_schema["items"]:
                    required = concursos_schema["items"]["required"]
                    for field in fields_to_remove:
                        if field in required:
                            required.remove(field)
        
        # Inicializar max_output_tokens (se ajustará dinámicamente si hay truncamiento)
        prompt_size = len(prompt)
        if prompt_size > 200000:  # Batch grande (múltiples páginas)
            # Calcular tokens de salida estimados: ~6 concursos por página * ~800 tokens por concurso (más conservador)
            # Usar un factor más alto para evitar truncamiento
            estimated_pages = prompt_size / 50000  # Estimación aproximada
            estimated_concursos = estimated_pages * 6
            # Aumentar a 800 tokens por concurso para dar más margen (incluye JSON structure overhead)
            estimated_output_tokens = int(estimated_concursos * 800)  # ~800 tokens por concurso
            # Aplicar factor de seguridad adicional del 50% para evitar truncamiento
            estimated_output_tokens = int(estimated_output_tokens * 1.5)
            # Asegurar mínimo de 12000 y máximo de 32000 (límite de Gemini)
            # Usar al menos el 50% del límite máximo para batches grandes
            max_output_tokens = max(12000, min(estimated_output_tokens, 32000))
            # Si la estimación es muy baja, usar al menos 20000 para batches grandes
            if prompt_size > 150000:
                max_output_tokens = max(max_output_tokens, 20000)
            logger.info(f"📊 Batch grande detectado ({prompt_size:,} chars). Ajustando maxOutputTokens inicial a {max_output_tokens:,}")
        else:
            max_output_tokens = self.gemini_client.max_output_tokens
        
        # Contador de reintentos por truncamiento (independiente de max_retries)
        truncation_retries = 0
        max_truncation_retries = 3  # Máximo 3 aumentos de tokens
        
        for attempt in range(max_retries):
            try:
                # Usar API REST directamente para Structured Outputs
                # El SDK antiguo google.generativeai no soporta response_json_schema
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_client.model_name}:generateContent"
                
                headers = {
                    "Content-Type": "application/json",
                }
                
                payload = {
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }],
                    "generationConfig": {
                        "temperature": self.gemini_client.temperature,
                        "maxOutputTokens": max_output_tokens,
                        "responseMimeType": "application/json",
                        "responseJsonSchema": json_schema,
                    }
                }
                
                params = {"key": self.gemini_client.api_key}
                
                # Obtener timeout de configuración (default: 60 segundos)
                api_timeout = self.extraction_config.get("api_timeout", 60)
                
                try:
                    response = requests.post(url, json=payload, headers=headers, params=params, timeout=api_timeout)
                except requests.Timeout as timeout_error:
                    logger.error(f"⏱️ Timeout después de {api_timeout}s en llamada a API")
                    raise Exception(f"Timeout de {api_timeout}s excedido en llamada a Gemini API. La API no respondió a tiempo.")
                except requests.ConnectionError as conn_error:
                    logger.error(f"🔌 Error de conexión con Gemini API: {conn_error}")
                    raise Exception(f"Error de conexión con Gemini API. Verifica tu conexión a internet.")
                except requests.RequestException as req_error:
                    logger.error(f"📡 Error en request a Gemini API: {req_error}")
                    raise Exception(f"Error en request a Gemini API: {str(req_error)}")
                
                # Manejar errores HTTP
                if response.status_code != 200:
                    error_data = {}
                    response_text = ""
                    try:
                        if response.content:
                            error_data = response.json()
                            response_text = response.text[:1000]  # Primeros 1000 caracteres
                    except (ValueError, json.JSONDecodeError):
                        error_data = {}
                        response_text = response.text[:1000] if hasattr(response, 'text') else str(response.content)[:1000]
                    
                    error_msg = error_data.get("error", {}).get("message", f"HTTP {response.status_code}")
                    error_code = error_data.get("error", {}).get("code", response.status_code)
                    
                    # Log detallado del error HTTP
                    logger.error(f"❌ Error HTTP {response.status_code} en llamada a Gemini API:")
                    logger.error(f"   URL: {url}")
                    logger.error(f"   Error code: {error_code}")
                    logger.error(f"   Error message: {error_msg}")
                    logger.error(f"   Response body (primeros 500 chars): {response_text[:500]}")
                    
                    # Crear excepción con más contexto
                    error_exception = Exception(f"Error de API Gemini (HTTP {response.status_code}): {error_msg}")
                    error_exception.status_code = response.status_code
                    error_exception.error_code = error_code
                    error_exception.response_body = response_text
                    raise error_exception
                
                result = response.json()
                
                # Extraer el texto de la respuesta
                if "candidates" in result and len(result["candidates"]) > 0:
                    content = result["candidates"][0].get("content", {})
                    parts = content.get("parts", [])
                    if parts and "text" in parts[0]:
                        response_text = parts[0]["text"]
                        
                        # Verificar si la respuesta está truncada
                        finish_reason = result["candidates"][0].get("finishReason", "UNKNOWN")
                        is_truncated = False
                        
                        if finish_reason == "MAX_TOKENS":
                            # La respuesta está truncada por límite de tokens
                            is_truncated = True
                            logger.warning(
                                f"⚠️ Respuesta truncada detectada (finishReason: MAX_TOKENS). "
                                f"maxOutputTokens actual: {max_output_tokens:,}"
                            )
                        else:
                            # Verificar también si el JSON está truncado (aunque finishReason no sea MAX_TOKENS)
                            # Esto puede pasar si el JSON se corta justo antes del límite
                            try:
                                # Intentar parsear para verificar si está completo
                                json.loads(response_text)
                            except json.JSONDecodeError as json_error:
                                error_msg = str(json_error)
                                if "Unterminated string" in error_msg or "Expecting" in error_msg:
                                    is_truncated = True
                                    logger.warning(
                                        f"⚠️ JSON truncado detectado en parsing. "
                                        f"maxOutputTokens actual: {max_output_tokens:,}. "
                                        f"Error: {error_msg[:200]}"
                                    )
                        
                        # Si está truncado, aumentar tokens y reintentar
                        if is_truncated:
                            if truncation_retries < max_truncation_retries:
                                # Aumentar tokens significativamente
                                old_max_tokens = max_output_tokens
                                max_output_tokens = min(max_output_tokens * 2, 32000)  # Duplicar, pero no exceder límite
                                
                                if max_output_tokens > old_max_tokens:
                                    truncation_retries += 1
                                    logger.info(
                                        f"🔄 Reintentando con maxOutputTokens aumentado de {old_max_tokens:,} a {max_output_tokens:,} "
                                        f"(reintento {truncation_retries}/{max_truncation_retries})"
                                    )
                                    # Continuar al siguiente intento del loop (no retornar)
                                    continue
                                else:
                                    # Ya estamos en el máximo, no podemos aumentar más
                                    raise Exception(
                                        f"Respuesta truncada y no se puede aumentar más maxOutputTokens "
                                        f"(actual: {max_output_tokens:,}, límite máximo: 32000). "
                                        f"El batch es demasiado grande. Considera reducir el tamaño del batch."
                                    )
                            else:
                                # Agotados los reintentos por truncamiento
                                raise Exception(
                                    f"Respuesta truncada después de {max_truncation_retries} intentos de aumentar tokens. "
                                    f"maxOutputTokens final: {max_output_tokens:,}. "
                                    f"El batch es demasiado grande. Considera reducir el tamaño del batch."
                                )
                        
                        # Si llegamos aquí, la respuesta está completa
                        self.api_key_manager.record_api_call(self.gemini_client.api_key, success=True)
                        return response_text.strip()
                    else:
                        # Verificar finishReason para entender por qué no hay contenido
                        finish_reason = result["candidates"][0].get("finishReason", "UNKNOWN")
                        if finish_reason == "MAX_TOKENS":
                            raise Exception(f"Respuesta truncada por límite de tokens (maxOutputTokens: {max_output_tokens}). El batch puede ser demasiado grande.")
                        elif finish_reason in ["SAFETY", "RECITATION", "OTHER"]:
                            block_reason = result.get("promptFeedback", {}).get("blockReason", "UNKNOWN")
                            raise Exception(f"Respuesta bloqueada por {finish_reason} (blockReason: {block_reason}). El contenido puede violar políticas de seguridad.")
                        else:
                            raise Exception(f"Respuesta sin contenido. finishReason: {finish_reason}")
                elif "error" in result:
                    error_msg = result["error"].get("message", "Error desconocido de Gemini")
                    raise Exception(f"Error de Gemini API: {error_msg}")
                else:
                    raise Exception(f"Respuesta inesperada de Gemini: {result}")
                
            except Exception as e:
                last_error = e
                error_str = str(e)
                error_type = type(e).__name__
                self.api_key_manager.record_api_call(self.gemini_client.api_key, success=False)
                
                # Registrar error detallado
                if not hasattr(self, '_last_error_details'):
                    self._last_error_details = []
                
                error_details = {
                    "attempt": attempt + 1,
                    "error": error_str,
                    "type": error_type,
                    "api_key": self.gemini_client.api_key[:8] + "..." if self.gemini_client.api_key else None,
                    "model": self.gemini_client.model_name,
                    "timestamp": datetime.now().isoformat(),
                    "traceback": traceback.format_exc()
                }
                
                # Si es error HTTP, incluir detalles de respuesta
                if hasattr(e, 'status_code'):
                    error_details["http_status"] = e.status_code
                    error_details["error_code"] = getattr(e, 'error_code', None)
                    error_details["response_body"] = getattr(e, 'response_body', None)
                elif hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                    error_details["http_status"] = e.response.status_code
                    try:
                        error_details["response_body"] = e.response.text[:500] if hasattr(e.response, 'text') else None
                    except (AttributeError, ValueError):
                        pass
                
                # Si es error de requests (timeout, connection, etc.)
                if hasattr(e, 'request'):
                    error_details["request_url"] = str(e.request.url) if hasattr(e.request, 'url') else None
                    error_details["request_method"] = str(e.request.method) if hasattr(e.request, 'method') else None
                
                self._last_error_details.append(error_details)
                
                # Determinar si es rate limit temporal para reducir logging
                is_rate_limit_temporal = False
                retry_after = None
                if ("429" in error_str or "quota" in error_str.lower() or 
                    "ResourceExhausted" in error_type or
                    (hasattr(e, 'status_code') and e.status_code == 429)):
                    try:
                        import re
                        retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                        if retry_match:
                            retry_after = int(float(retry_match.group(1)))
                            if retry_after and retry_after < 60:
                                is_rate_limit_temporal = True
                    except (ValueError, AttributeError):
                        pass
                
                # Log simplificado para rate limits temporales
                if is_rate_limit_temporal:
                    logger.warning(
                        f"⏱️ Rate limit temporal en intento {attempt + 1}/{max_retries}. "
                        f"Esperar {retry_after}s antes de reintentar."
                    )
                else:
                    # Log detallado solo para errores no relacionados con rate limits temporales
                    logger.error(f"❌ Error en intento {attempt + 1}/{max_retries} de llamada al LLM:")
                    logger.error(f"   Tipo: {error_type}")
                    logger.error(f"   Mensaje: {error_str}")
                    if hasattr(e, 'status_code'):
                        logger.error(f"   HTTP Status: {e.status_code}")
                    if hasattr(e, 'error_code'):
                        logger.error(f"   Error Code: {e.error_code}")
                    if hasattr(e, 'response_body') and e.response_body:
                        logger.error(f"   Response Body: {e.response_body[:500]}")
                    logger.error(f"   Model: {self.gemini_client.model_name}")
                    # Solo mostrar traceback para errores críticos (no rate limits)
                    if not ("429" in error_str or "quota" in error_str.lower()):
                        logger.error(f"   Traceback completo:\n{traceback.format_exc()}")
                
                # Si es error de cuota, manejar según el tipo de límite
                if ("429" in error_str or "quota" in error_str.lower() or 
                    "ResourceExhausted" in error_type or
                    (hasattr(e, 'status_code') and e.status_code == 429)):
                    logger.warning(f"Intento {attempt + 1}/{max_retries}: Error de cuota detectado")
                    
                    retry_after = None
                    try:
                        import re
                        retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                        if retry_match:
                            retry_after = int(float(retry_match.group(1)))
                    except (ValueError, AttributeError):
                        pass
                    
                    # Si el retry_after es corto (< 60s), es un rate limit temporal
                    if retry_after and retry_after < 60:
                        # Rastrear tiempo de retry
                        rate_limit_retry_times.append(retry_after)
                        
                        # Si ya probamos varias keys (3+) y todas tienen rate limit temporal,
                        # esperar el tiempo máximo antes de reintentar
                        if len(rate_limit_retry_times) >= 3:
                            max_wait_time = max(rate_limit_retry_times)
                            logger.warning(
                                f"⏱️ Múltiples API keys con rate limit temporal detectado ({len(rate_limit_retry_times)} keys). "
                                f"Esperando {max_wait_time}s antes de reintentar..."
                            )
                            time.sleep(max_wait_time + 1)
                            # Limpiar lista para reintentar desde el principio
                            rate_limit_retry_times = []
                            continue
                        
                        # Intentar rotar a otra key
                        logger.info(
                            f"⏱️ Rate limit temporal detectado (retry in {retry_after}s). "
                            f"Rotando a otra API key..."
                        )
                        if self.gemini_client._handle_quota_error(e, retry_after):
                            # Log eliminado: se registra en gemini_client.py para evitar redundancia
                            time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2))
                            continue
                        else:
                            # No hay más keys disponibles, esperar el tiempo máximo indicado
                            max_wait_time = max(rate_limit_retry_times) if rate_limit_retry_times else retry_after
                            logger.warning(
                                f"⚠️ No hay más keys disponibles. Esperando {max_wait_time}s antes de reintentar..."
                            )
                            time.sleep(max_wait_time + 1)
                            rate_limit_retry_times = []  # Reset para reintentar
                            continue
                    
                    # Si el retry_after es largo o no se puede determinar, es un límite diario
                    # Rotar a otra key
                    if self.gemini_client._handle_quota_error(e, retry_after):
                        # Log eliminado: se registra en gemini_client.py para evitar redundancia
                        time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2))
                        continue
                    else:
                        # No hay más keys disponibles
                        logger.error("❌ No hay más API keys disponibles. Todas están agotadas.")
                        if attempt < max_retries - 1:
                            # Si hay retry_after, esperar ese tiempo antes de reintentar
                            wait_time = retry_after if retry_after else EXTRACTION_CONFIG.get("retry_delay", 2) * (attempt + 1)
                            logger.warning(f"⚠️ Esperando {wait_time}s antes de reintentar...")
                            time.sleep(wait_time)
                            continue
                        else:
                            raise Exception("Todas las API keys están agotadas. No se puede continuar.")
                
                # Si es timeout, es más probable que sea un problema de red o batch muy pesado que de cuota.
                # No marcar la key como agotada ni rotar todas las keys: solo reintentar hasta max_retries.
                if "timeout" in error_str.lower() or "Timeout" in error_type:
                    logger.error(f"⏱️ Timeout en intento {attempt + 1}. No se rotará de key para evitar marcar todas como agotadas.")
                    if attempt < max_retries - 1:
                        logger.warning(f"⚠️ Reintentando en {EXTRACTION_CONFIG.get('retry_delay', 2)}s con la MISMA API key...")
                        time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2) * (attempt + 1))
                        continue
                    else:
                        raise e
                
                # Para otros errores, reintentar si quedan intentos
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ Reintentando en {EXTRACTION_CONFIG.get('retry_delay', 2)}s...")
                    time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2) * (attempt + 1))
                    continue
                else:
                    raise e
        
        # Si llegamos aquí, todos los intentos fallaron
        if last_error:
            # Mejorar el mensaje de error con más contexto
            error_type = type(last_error).__name__
            error_msg = str(last_error)
            
            # Construir mensaje detallado
            detailed_error = f"Error al llamar al LLM después de {max_retries} intentos: [{error_type}] {error_msg}"
            
            # Si es un error HTTP, agregar más detalles
            if hasattr(last_error, 'response') and last_error.response is not None:
                status_code = getattr(last_error.response, 'status_code', None)
                if status_code:
                    detailed_error += f" (HTTP {status_code})"
            
            raise Exception(detailed_error) from last_error
        else:
            raise Exception(f"Error desconocido al llamar al LLM después de {max_retries} intentos. No se capturó ningún error específico.")
    
    def _parse_response(self, response_text: str) -> List[Concurso]:
        """
        Parsea la respuesta del LLM y la convierte a objetos Concurso.
        
        Con Structured Outputs, el JSON debería venir perfectamente formateado,
        pero mantenemos validación básica por seguridad.
        
        Nota: El LLM no devuelve URLs, estas se asignan programáticamente después.
        
        Args:
            response_text: Texto de respuesta del LLM (JSON válido según esquema)
            
        Returns:
            Lista de objetos Concurso (con URL placeholder que se reemplazará después)
        """
        # Con Structured Outputs, el JSON viene garantizado como válido según el esquema
        # Solo limpiamos si viene envuelto en markdown (aunque no debería ser necesario)
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        # Parsear JSON (Structured Outputs garantiza formato válido)
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            error_msg = str(e)
            logger.error(f"Error inesperado al parsear JSON (Structured Outputs debería garantizar formato válido): {e}")
            logger.error(f"Respuesta recibida (primeros 1000 chars): {response_text[:1000]}")
            
            # Si el error es "Unterminated string", el JSON está truncado
            # Esto NO debería pasar porque _call_llm_with_retry ya debería haber detectado y reintentado
            if "Unterminated string" in error_msg or "truncated" in error_msg.lower():
                logger.error(
                    f"⚠️ JSON truncado detectado en _parse_response. "
                    f"Esto indica que el sistema de reintento automático no funcionó correctamente. "
                    f"El batch puede ser demasiado grande o se alcanzó el límite máximo de tokens (32000)."
                )
            
            return []
        
        # Validar estructura y normalizar
        concursos_list = []
        
        if isinstance(data, list):
            # El LLM retornó un array directamente
            logger.info("Respuesta es un array directo, normalizando a estructura esperada")
            concursos_list = data
        elif isinstance(data, dict) and "concursos" in data:
            # Estructura esperada: {"concursos": [...]}
            concursos_list = data.get("concursos", [])
        else:
            logger.warning(f"Respuesta no tiene estructura esperada. Tipo: {type(data)}, Contenido: {str(data)[:200]}")
            return []
        
        # Convertir a objetos Concurso
        concursos = []
        for item in concursos_list:
            try:
                # Mapear campos (sin URL, se asignará después)
                concurso_dict = self._map_to_concurso_model(item)
                
                # Validar con Pydantic (sin URL por ahora)
                # El campo URL se asignará después programáticamente
                concurso = Concurso(**concurso_dict)
                concursos.append(concurso)
                
            except Exception as e:
                logger.warning(f"Error al validar concurso: {e}. Datos: {item}")
                continue
        
        return concursos
    
    def _map_to_concurso_model(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mapea un diccionario de respuesta del LLM al modelo Concurso.
        
        Con Structured Outputs, los campos ya vienen con los nombres correctos,
        solo necesitamos normalizar fechas. El estado se calcula determinísticamente desde las fechas o detección de "suspendido" en URL/contenido.
        
        Nota: El campo URL no se incluye aquí, se asigna programáticamente después.
        
        Args:
            item: Diccionario con datos del concurso (ya con nombres correctos gracias a Structured Outputs)
            
        Returns:
            Diccionario compatible con el modelo Concurso (sin URL)
        """
        from utils.date_parser import parse_date, is_past_date
        from datetime import datetime
        
        # Obtener valores originales del LLM (ya vienen con nombres correctos)
        fecha_apertura_raw = item.get("fecha_apertura")
        fecha_cierre_raw = item.get("fecha_cierre")
        
        # Normalizar fechas: parsear y convertir a formato YYYY-MM-DD
        fecha_apertura_normalized = None
        fecha_cierre_normalized = None
        
        if fecha_apertura_raw and fecha_apertura_raw.lower() not in ["suspendido", "null", "none", ""]:
            parsed = parse_date(fecha_apertura_raw)
            if parsed:
                fecha_apertura_normalized = parsed.strftime("%Y-%m-%d")
        
        if fecha_cierre_raw and fecha_cierre_raw.lower() not in ["suspendido", "null", "none", ""]:
            parsed = parse_date(fecha_cierre_raw)
            if parsed:
                fecha_cierre_normalized = parsed.strftime("%Y-%m-%d")
        
        # Detectar si el concurso está suspendido o adjudicado (antes de calcular estado normal)
        # Buscar palabras clave en los textos de fechas
        suspendido_keywords = ["suspendido", "suspendida", "convocatoria suspendida", 
                              "suspend", "suspended", "temporalmente suspendido"]
        adjudicado_keywords = ["concurso adjudicado", "adjudicado", "adjudicada"]
        
        fecha_texto_completo = f"{fecha_apertura_raw or ''} {fecha_cierre_raw or ''}".lower()
        is_suspendido = any(keyword in fecha_texto_completo for keyword in suspendido_keywords)
        is_adjudicado = any(keyword in fecha_texto_completo for keyword in adjudicado_keywords)
        
        # Calcular estado automáticamente basándose en las fechas (el LLM NO calcula estado)
        # El estado se calcula siempre de forma determinística desde las fechas
        estado = None
        
        # Si está suspendido, marcar como "Suspendido" independientemente de las fechas.
        # Si está adjudicado, el concurso está efectivamente cerrado.
        if is_suspendido:
            estado = "Suspendido"
        elif is_adjudicado:
            estado = "Cerrado"
        elif fecha_cierre_normalized:
            # Usar la fecha normalizada para calcular estado (más confiable)
            parsed_cierre = parse_date(fecha_cierre_normalized)
            if parsed_cierre:
                if parsed_cierre < datetime.now():
                    estado = "Cerrado"
                else:
                    estado = "Abierto"
        elif fecha_cierre_raw and fecha_cierre_raw.lower() not in ["suspendido", "null", "none", ""]:
            # Fallback: usar el texto original si no se pudo normalizar
            if is_past_date(fecha_cierre_raw):
                estado = "Cerrado"
            else:
                estado = "Abierto"
        elif fecha_apertura_normalized:
            # Si solo hay fecha de apertura
            parsed_apertura = parse_date(fecha_apertura_normalized)
            if parsed_apertura and parsed_apertura > datetime.now():
                estado = "Próximo"
            else:
                estado = "Abierto"
        elif fecha_apertura_raw and fecha_apertura_raw.lower() not in ["suspendido", "null", "none", ""]:
            # Fallback: usar el texto original
            parsed_apertura = parse_date(fecha_apertura_raw)
            if parsed_apertura and parsed_apertura > datetime.now():
                estado = "Próximo"
            else:
                estado = "Abierto"
        
        # Si no se pudo determinar estado, usar null
        if estado is None:
            estado = None
        
        # Resolver URL:
        # - Si el LLM entrega una URL específica y parece válida, la usamos.
        # - Si no, dejamos la URL vacía; más adelante se intentará recuperar
        #   desde el HTML y, si no es posible, se loggeará la incidencia.
        raw_url = (item.get("url") or "").strip() if isinstance(item.get("url"), str) else ""
        url_normalized = None
        if raw_url:
            # Aceptar directamente URLs absolutas de concursos
            if raw_url.startswith("http") and "/concursos/" in raw_url:
                url_normalized = raw_url
            # Evitar quedarnos con la URL genérica de listado
            if url_normalized and url_normalized.rstrip("/").endswith("/concursos"):
                url_normalized = None
        
        mapped = {
            "nombre": item.get("nombre", ""),
            "fecha_apertura": fecha_apertura_normalized,  # Normalizada
            "fecha_cierre": fecha_cierre_normalized,  # Normalizada
            "organismo": item.get("organismo", ""),
            "financiamiento": item.get("financiamiento"),
            # Si no tenemos URL confiable, se intentará asignar más adelante.
            # Si tampoco se logra, la URL quedará vacía y se registrará en logs/debug.
            "url": url_normalized or None,
            "estado": estado,  # Calculado
            "fecha_apertura_original": fecha_apertura_raw,  # Texto original
            "descripcion": item.get("descripcion"),
            "predicted_opening": item.get("predicted_opening"),
            "subdireccion": item.get("subdireccion"),
        }
        
        # Inferir organismo desde URL si no está presente
        if not mapped["organismo"]:
            url_lower = (mapped["url"] or "").lower()
            if "anid.cl" in url_lower:
                mapped["organismo"] = "ANID"
            elif "mineduc.cl" in url_lower or "centroestudios" in url_lower:
                mapped["organismo"] = "MINEDUC"
            elif "cnachile.cl" in url_lower or "cna" in url_lower:
                mapped["organismo"] = "CNA"
            else:
                mapped["organismo"] = "Desconocido"
        
        # Agregar metadatos
        mapped["extraido_en"] = datetime.now().isoformat()
        mapped["fuente"] = "anid.cl" if "anid.cl" in (mapped["url"] or "").lower() else None
        
        return mapped
    

