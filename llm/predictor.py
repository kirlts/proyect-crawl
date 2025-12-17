"""
Predictor de concursos usando LLM

Analiza si dos concursos son el mismo y predice fechas de apertura.
"""

import json
import logging
import time
import requests
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List

from models.prediccion import (
    PrediccionResponse,
    PrediccionConcurso,
    PrediccionBatchResponse,
    PrediccionConcursoBatchItem,
)
from llm.gemini_client import GeminiClient
from config import EXTRACTION_CONFIG, GEMINI_CONFIG

logger = logging.getLogger(__name__)


PREDICTION_SYSTEM_PROMPT = """Eres un analista experto en fondos de financiamiento para investigación académica en Chile.
Tu tarea es analizar concursos y generar predicciones prudentes y bien justificadas sobre futuras fechas de apertura.

Utiliza siempre la fecha actual que se te entrega como referencia temporal.
Entrega fechas que se sitúan en el futuro respecto de esa fecha actual y respalda tus decisiones con evidencia clara y explícita.
No propongas fechas anteriores o iguales a la última versión conocida ni fechas en el pasado respecto de la fecha actual."""

PREDICTION_FROM_PREVIOUS_PROMPT_TEMPLATE = """Analiza el siguiente concurso y su historial de versiones anteriores para estimar CUÁNDO se abrirá la PRÓXIMA VERSIÓN FUTURA.

FECHA ACTUAL (referencia temporal): {fecha_actual}

CONCURSO ACTUAL:
- Nombre: {nombre}
- URL: {url}
- Fecha apertura: {fecha_apertura}
- Fecha cierre: {fecha_cierre}
- Organismo: {organismo}
- Descripción: {descripcion}

CONCURSOS ANTERIORES (versiones históricas extraídas directamente de la página):
{previous_concursos_info}

INSTRUCCIONES:
1. Analiza el patrón de fechas de apertura y cierre de las versiones anteriores, observando año, mes y estacionalidad.
2. Identifica la periodicidad dominante (por ejemplo anual, semestral u otra cadencia estable) y el momento típico del año en que abre el concurso.
3. Propone una fecha de apertura FUTURA coherente con ese patrón y con la fecha actual ({fecha_actual}), estrictamente posterior al último año conocido de apertura.
4. Prioriza fechas que mantengan la misma ventana temporal histórica (por ejemplo mismos meses o trimestre).
5. Cuando la información es ambigua o sugiere una próxima versión demasiado lejana en el tiempo, utiliza una estimación prudente y marca fecha_predicha como null.

CONCEPTOS Y EJEMPLOS CONCEPTUALES:
- Ejemplo A (patrón anual claro): versiones 2022, 2023 y 2024 abren en marzo; una próxima versión razonable se ubica en marzo del año siguiente.
- Ejemplo B (patrón con cambios leves): versiones 2021 y 2023 abren entre abril y mayo; una próxima versión futura razonable se sitúa en esa misma ventana de meses del año siguiente disponible.
- Ejemplo C (información insuficiente): una sola versión previa sin señales claras de recurrencia motiva una estimación prudente y favorece fecha_predicha = null con una justificación clara.

Mantén siempre una justificación breve, concreta y fácil de entender que explique la relación entre el patrón histórico y la estimación propuesta."""

PREDICTION_FROM_PREVIOUS_BATCH_PROMPT_TEMPLATE = """Analiza varios concursos y sus versiones anteriores para estimar CUÁNDO se abrirá la PRÓXIMA VERSIÓN FUTURA de cada uno.

FECHA ACTUAL (referencia temporal): {fecha_actual}

CONJUNTO DE CONCURSOS:
{items_block}

TAREA:
1. Examina cada concurso de forma independiente, usando únicamente la información incluida en su bloque.
2. Para cada concurso, analiza la secuencia de versiones anteriores y la relación entre años, meses y fechas de apertura y cierre.
3. Identifica la periodicidad dominante y la ventana típica del año en que se abre el concurso.
4. Propone una fecha de apertura FUTURA prudente, coherente con el patrón histórico y con la fecha actual, y que sea estrictamente posterior al último año conocido de apertura.
5. Si la información no permite establecer un patrón razonable o la próxima versión se ubicaría demasiado lejos, utiliza una estimación prudente y establece fecha_predicha como null.

FORMATO DE RESPUESTA:
- Devuelve un único objeto JSON con la siguiente estructura:
  {{
    \"items\": [
      {{
        \"concurso_url\": \"<URL del concurso>\",
        \"prediccion\": {{
          \"es_mismo_concurso\": true,
          \"fecha_predicha\": \"YYYY-MM-DD\" o null,
          \"justificacion\": \"párrafo breve y claro\"
        }}
      }},
      ...
    ]
  }}

EJEMPLOS CONCEPTUALES (a modo ilustrativo, sin reproducir literalmente):
- Lote 1: varios concursos anuales con aperturas entre marzo y abril se proyectan nuevamente en ese rango de meses del siguiente año disponible.
- Lote 2: concursos con un historial corto o irregular priorizan justificaciones prudentes.
- Lote 3: concursos con historial antiguo y sin nuevas versiones recientes favorecen fecha_predicha = null con una explicación clara de la incertidumbre.

Genera una predicción coherente para cada concurso del lote, manteniendo independencia conceptual entre ellos y utilizando el mismo criterio de análisis histórico en todos los casos."""


PREDICTION_PROMPT_TEMPLATE = """Analiza si los siguientes dos concursos son esencialmente el mismo (solo difieren en año o versión) o si son concursos diferentes.

FECHA ACTUAL (referencia temporal, muy importante): {fecha_actual}

CONCURSO 1:
- Nombre: {nombre1}
- URL: {url1}
- Fecha apertura: {fecha_apertura1}
- Fecha cierre: {fecha_cierre1}
- Organismo: {organismo1}
- Descripción: {descripcion1}
- Contenido completo de la página:
{page_content1}

CONCURSO 2:
- Nombre: {nombre2}
- URL: {url2}
- Fecha apertura: {fecha_apertura2}
- Fecha cierre: {fecha_cierre2}
- Organismo: {organismo2}
- Descripción: {descripcion2}
- Contenido completo de la página:
{page_content2}

INFORMACIÓN HISTÓRICA (si está disponible):
{historical_info}

INSTRUCCIONES:
1. Determina si son el MISMO concurso (solo difieren en año/versión) o son DIFERENTES.

2. Si son el mismo concurso:
   - **PRIORIDAD MÁXIMA**: Si la información histórica incluye "CONCURSOS ANTERIORES (información histórica extraída directamente de la página)",
     esta información proviene directamente de la sección "Concursos anteriores" de la página ANID y es la fuente MÁS CONFIABLE.
     * Usa EXCLUSIVAMENTE esta información para analizar patrones y predecir la próxima apertura.
     * Analiza los intervalos entre versiones anteriores (años, meses, estacionalidad).
     * Calcula el patrón promedio de apertura basándote en las fechas históricas exactas proporcionadas.
     * Esta información es más precisa que cualquier otra fuente histórica.
   
   - Si NO hay información de "Concursos anteriores", usa la información histórica disponible del sistema.
   
   - Analiza el patrón histórico de aperturas/cierres.
   - Predice la fecha de PRÓXIMA apertura basándote en:
     * Intervalos históricos entre versiones (especialmente si provienen de "Concursos anteriores").
     * Patrones estacionales (meses típicos de apertura).
     * Información del contenido de las páginas.
   
   - LA FECHA PREDICHA DEBE SER SIEMPRE POSTERIOR a la FECHA ACTUAL indicada arriba.
     * No propongas meses/años que ya hayan pasado o sean anteriores/iguales a la fecha actual.
     * Si por el patrón histórico concluyes que NO HABRÁ NUEVAS CONVOCATORIAS FUTURAS, devuelve fecha_predicha = null.
   
   - Justifica tu decisión en un párrafo sencillo y claro (máximo 200 palabras). La justificación debe ser fácil de entender y explicar de forma concisa por qué se predice esa fecha basándose en el patrón histórico.

3. Si son diferentes:
   - Explica por qué son concursos distintos.
   - Justifica tu decisión.

IMPORTANTE:
- La fecha predicha debe ser en formato YYYY-MM-DD o texto descriptivo claro (ej: "marzo 2026", "primer trimestre 2026") y DEBE corresponder a un momento futuro respecto a la fecha actual.
- La justificación debe ser un párrafo sencillo y claro (máximo 200 palabras) que explique la predicción de forma comprensible. Debe ser concisa y fácil de entender.
- Si hay información de "Concursos anteriores", dale MÁXIMA PRIORIDAD sobre cualquier otra fuente histórica.
"""


class ConcursoPredictor:
    """
    Predictor de concursos usando LLM.
    
    Analiza similitud entre concursos y predice fechas de apertura.
    """
    
    def __init__(self, api_key_manager, model_name: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        """
        Inicializa el predictor.
        
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
        else:
            gemini_config["model"] = GEMINI_CONFIG.get("model", "gemini-2.5-flash-lite")
        
        self.gemini_client = GeminiClient(
            api_key_manager=api_key_manager,
            config=gemini_config
        )
        
        self.extraction_config = EXTRACTION_CONFIG
    
    def predict_from_previous_concursos(
        self,
        concurso: Dict[str, Any],
        previous_concursos_info: str
    ) -> PrediccionConcurso:
        """
        Predice la fecha de apertura de la próxima versión basándose en información
        de "Concursos anteriores" extraída directamente de la página.
        
        Args:
            concurso: Diccionario con datos del concurso actual
            previous_concursos_info: Información formateada de concursos anteriores
            
        Returns:
            Objeto PrediccionConcurso con la predicción
        """
        fecha_actual = datetime.now().strftime("%Y-%m-%d")
        
        prompt = PREDICTION_FROM_PREVIOUS_PROMPT_TEMPLATE.format(
            nombre=concurso.get("nombre", ""),
            url=concurso.get("url", ""),
            fecha_apertura=concurso.get("fecha_apertura", "N/A"),
            fecha_cierre=concurso.get("fecha_cierre", "N/A"),
            organismo=concurso.get("organismo", "N/A"),
            descripcion=concurso.get("descripcion", ""),
            previous_concursos_info=previous_concursos_info,
            fecha_actual=fecha_actual
        )
        
        full_prompt = f"{PREDICTION_SYSTEM_PROMPT}\n\n{prompt}"
        
        logger.info(
            f"🔮 Prediciendo próxima versión para '{concurso.get('nombre')}' "
            f"basándose en información de 'Concursos anteriores'..."
        )
        
        try:
            response_text = self._call_llm_with_structured_output(full_prompt)
            prediccion = self._parse_prediction_response(response_text)
            # Cuando usamos previous_concursos, siempre es el mismo concurso
            prediccion.es_mismo_concurso = True
            return prediccion
        except Exception as e:
            error_str = str(e)
            error_type = type(e).__name__
            
            # Construir mensaje de error detallado
            detailed_error = f"[{error_type}] {error_str}"
            
            # Agregar contexto adicional según el tipo de error
            if isinstance(e, ValueError):
                detailed_error = f"Error de validación: {error_str}"
            elif isinstance(e, json.JSONDecodeError):
                detailed_error = f"Error al parsear respuesta JSON: {error_str}"
            elif "Timeout" in error_type or "timeout" in error_str.lower():
                detailed_error = f"Timeout en llamada al LLM: {error_str}"
            elif "Connection" in error_type or "conexión" in error_str.lower():
                detailed_error = f"Error de conexión con el LLM: {error_str}"
            elif "429" in error_str or ("quota" in error_str.lower() and "retry in" in error_str.lower()):
                import re
                retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                if retry_match:
                    retry_after = int(float(retry_match.group(1)))
                    logger.warning(f"⏱️ Rate limit temporal al predecir. Esperar {retry_after}s antes de reintentar.")
                    detailed_error = f"Rate limit temporal: {error_str} (esperar {retry_after}s)"
                else:
                    logger.error(f"Error de cuota al predecir desde concursos anteriores: {error_str}")
                    detailed_error = f"Error de cuota: {error_str}"
            else:
                logger.error(f"Error al predecir desde concursos anteriores: [{error_type}] {error_str}")
            
            return PrediccionConcurso(
                es_mismo_concurso=True,
                fecha_predicha=None,
                justificacion=f"Error al analizar: {detailed_error}"
            )

    def predict_from_previous_concursos_batch(
        self,
        concursos_batch: list[dict]
    ) -> Dict[str, PrediccionConcurso]:
        """
        Predice fechas de apertura para un batch de concursos usando información
        de "Concursos anteriores" ya formateada.
        
        Args:
            concursos_batch: Lista de diccionarios con:
                - concurso: datos del concurso actual
                - previous_concursos_info: texto formateado con concursos anteriores
        
        Returns:
            Diccionario {concurso_url: PrediccionConcurso}
        """
        fecha_actual = datetime.now().strftime("%Y-%m-%d")
        
        # Construir bloque de items para el batch
        items_blocks = []
        for idx, item in enumerate(concursos_batch, start=1):
            concurso = item.get("concurso", {})
            previous_info = item.get("previous_concursos_info", "")
            
            block_lines = [
                f"CONCURSO {idx}:",
                f"- URL: {concurso.get('url', '')}",
                f"- Nombre: {concurso.get('nombre', '')}",
                f"- Fecha apertura: {concurso.get('fecha_apertura', 'N/A')}",
                f"- Fecha cierre: {concurso.get('fecha_cierre', 'N/A')}",
                f"- Organismo: {concurso.get('organismo', 'N/A')}",
                f"- Descripción: {concurso.get('descripcion', '')}",
                "",
                "CONCURSOS ANTERIORES:",
                previous_info,
                "",
            ]
            items_blocks.append("\n".join(block_lines))
        
        items_block = "\n\n".join(items_blocks)
        
        prompt = PREDICTION_FROM_PREVIOUS_BATCH_PROMPT_TEMPLATE.format(
            fecha_actual=fecha_actual,
            items_block=items_block
        )
        full_prompt = f"{PREDICTION_SYSTEM_PROMPT}\n\n{prompt}"
        
        logger.info(
            f"🔮 Prediciendo próximas versiones para un batch de {len(concursos_batch)} concursos "
            f"basándose en información de 'Concursos anteriores'... "
            f"(maxOutputTokens: 12000 para acomodar {len(concursos_batch)} concursos)"
        )
        
        # Reintentos automáticos para errores de parsing JSON u otros errores recuperables
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                response_text = self._call_llm_with_structured_output(
                    full_prompt,
                    response_model=PrediccionBatchResponse,
                    max_output_tokens=12000  # Explícitamente 12000 tokens para batches
                )
                # Si llegamos aquí, el parsing fue exitoso
                return self._parse_prediction_batch_response(response_text)
                
            except (ValueError, json.JSONDecodeError) as e:
                # Error de parsing JSON - reintentar automáticamente
                error_str = str(e)
                error_type = type(e).__name__
                last_error = e
                
                # Log detallado para diagnóstico
                if isinstance(e, json.JSONDecodeError):
                    error_details = f"Línea {e.lineno}, columna {e.colno}, posición {e.pos}"
                else:
                    error_details = error_str
                
                if attempt < max_retries - 1:
                    logger.warning(
                        f"⚠️ Error al parsear respuesta JSON del batch (intento {attempt + 1}/{max_retries}): "
                        f"[{error_type}] {error_details}. "
                        f"Posible causa: respuesta truncada por límite de tokens. Reintentando automáticamente..."
                    )
                    time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2) * (attempt + 1))
                    continue
                else:
                    # Agotados los reintentos
                    logger.error(
                        f"❌ Error al parsear respuesta JSON del batch después de {max_retries} intentos: "
                        f"[{error_type}] {error_details}. "
                        f"El JSON probablemente se truncó. Considera reducir el tamaño del batch o aumentar maxOutputTokens."
                    )
                    raise Exception(
                        f"Error crítico: No se pudo parsear respuesta del LLM después de {max_retries} reintentos. "
                        f"Último error: [{error_type}] {error_details}. "
                        f"Posible causa: respuesta truncada por límite de tokens (actualmente 12000). "
                        f"Se detendrá la ejecución de predicciones."
                    ) from e
                    
            except Exception as e:
                error_str = str(e)
                error_type = type(e).__name__
                last_error = e
                
                # Si es un error crítico que no debe reintentarse (ej: todas las API keys agotadas)
                if "Todas las API keys están agotadas" in error_str or "Error crítico" in error_str:
                    logger.error(f"❌ Error crítico al predecir batch: [{error_type}] {error_str}")
                    raise
                
                # Para otros errores, reintentar
                if attempt < max_retries - 1:
                    logger.warning(
                        f"⚠️ Error al predecir batch (intento {attempt + 1}/{max_retries}): "
                        f"[{error_type}] {error_str}. Reintentando automáticamente..."
                    )
                    time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2) * (attempt + 1))
                    continue
                else:
                    # Agotados los reintentos
                    logger.error(
                        f"❌ Error al predecir batch después de {max_retries} intentos: "
                        f"[{error_type}] {error_str}"
                    )
                    raise Exception(
                        f"Error crítico: No se pudo procesar batch después de {max_retries} reintentos. "
                        f"Último error: [{error_type}] {error_str}. "
                        f"Se detendrá la ejecución de predicciones."
                    ) from e
    
    def predict_concurso_similarity(
        self,
        concurso1: Dict[str, Any],
        concurso2: Dict[str, Any],
        historical_info: Optional[str] = None
    ) -> PrediccionConcurso:
        """
        Analiza si dos concursos son el mismo y predice fecha de apertura.
        
        Args:
            concurso1: Diccionario con datos del primer concurso
            concurso2: Diccionario con datos del segundo concurso
            historical_info: Información histórica adicional (opcional)
            
        Returns:
            Objeto PrediccionConcurso con la predicción
        """
        # Construir prompt (incluyendo fecha actual explícita para evitar fechas en el pasado)
        fecha_actual = datetime.now().strftime("%Y-%m-%d"),
        
        prompt = PREDICTION_PROMPT_TEMPLATE.format(
            nombre1=concurso1.get("nombre", ""),
            url1=concurso1.get("url", ""),
            fecha_apertura1=concurso1.get("fecha_apertura", "N/A"),
            fecha_cierre1=concurso1.get("fecha_cierre", "N/A"),
            organismo1=concurso1.get("organismo", "N/A"),
            descripcion1=concurso1.get("descripcion", "N/A"),
            page_content1=concurso1.get("page_content", "No disponible"),
            nombre2=concurso2.get("nombre", ""),
            url2=concurso2.get("url", ""),
            fecha_apertura2=concurso2.get("fecha_apertura", "N/A"),
            fecha_cierre2=concurso2.get("fecha_cierre", "N/A"),
            organismo2=concurso2.get("organismo", "N/A"),
            descripcion2=concurso2.get("descripcion", "N/A"),
            page_content2=concurso2.get("page_content", "No disponible"),
            historical_info=historical_info or "No hay información histórica disponible",
            fecha_actual=fecha_actual,
        )
        
        full_prompt = f"{PREDICTION_SYSTEM_PROMPT}\n\n{prompt}"
        
        # Llamar a LLM con structured output (log más explícito para debug)
        logger.info(
            "🤖 Analizando similitud entre concursos:"
            f" '{concurso1.get('nombre')}' ({concurso1.get('url')})"
            f" y '{concurso2.get('nombre')}' ({concurso2.get('url')})..."
        )
        
        try:
            response_text = self._call_llm_with_structured_output(full_prompt)
            prediccion = self._parse_prediction_response(response_text)
            return prediccion
        except Exception as e:
            error_str = str(e)
            # Log simplificado para rate limits temporales
            if "429" in error_str or ("quota" in error_str.lower() and "retry in" in error_str.lower()):
                import re
                retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                if retry_match:
                    retry_after = int(float(retry_match.group(1)))
                    logger.warning(f"⏱️ Rate limit temporal al predecir similitud. Esperar {retry_after}s antes de reintentar.")
                else:
                    logger.error(f"Error al predecir similitud: {error_str}")
            else:
                logger.error(f"Error al predecir similitud: {error_str}")
            # Retornar predicción por defecto en caso de error
            return PrediccionConcurso(
                es_mismo_concurso=False,
                fecha_predicha=None,
                justificacion=f"Error al analizar: {str(e)}"
            )
    
    def _call_llm_with_structured_output(self, prompt: str, response_model=None, max_output_tokens: Optional[int] = None) -> str:
        """
        Llama al LLM con structured output para garantizar formato correcto.
        
        Args:
            prompt: Prompt completo
            response_model: Modelo Pydantic para la respuesta (opcional)
            max_output_tokens: Límite de tokens de salida (opcional, por defecto 2000 para individual, 12000 para batch)
            
        Returns:
            Texto de respuesta del LLM (JSON válido)
        """
        from models.prediccion import PrediccionResponse, PrediccionBatchResponse
        
        # Modelo de respuesta por defecto (predicción individual)
        if response_model is None:
            response_model = PrediccionResponse
        
        # Determinar max_output_tokens si no se especifica
        if max_output_tokens is None:
            # Si es batch, usar más tokens; si es individual, usar menos
            if response_model == PrediccionBatchResponse:
                max_output_tokens = 12000  # Suficiente para 10 concursos con justificaciones
            else:
                max_output_tokens = 2000  # Suficiente para predicción individual
        
        # Obtener esquema JSON del modelo
        json_schema = response_model.model_json_schema()
        
        # URL de la API
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_client.model_name}:generateContent"
        
        headers = {"Content-Type": "application/json"}
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.3,  # Más bajo para mayor consistencia
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": json_schema,
            }
        }
        
        # Timeout
        api_timeout = self.extraction_config.get("api_timeout", 60)
        
        # Intentar con rotación de API keys (para errores de cuota, no para timeouts)
        configured_retries = self.extraction_config.get("max_retries", 3)
        total_keys = len(self.api_key_manager.api_keys) if self.api_key_manager else 1
        max_retries = min(configured_retries, total_keys)
        last_error = None
        last_attempt_info = None  # Para debug si llegamos al final sin error
        
        for attempt in range(max_retries):
            last_attempt_info = f"intento {attempt + 1}/{max_retries}"
            try:
                params = {"key": self.gemini_client.api_key}
                response = requests.post(url, json=payload, headers=headers, params=params, timeout=api_timeout)
                
                if response.status_code != 200:
                    error_data = {}
                    try:
                        if response.content:
                            error_data = response.json()
                    except (ValueError, json.JSONDecodeError):
                        error_data = {}
                    error_msg = error_data.get("error", {}).get("message", f"HTTP {response.status_code}")
                    error_code = error_data.get("error", {}).get("code", response.status_code)
                    
                    # Si es error 429 (quota exceeded), rotar inmediatamente
                    if response.status_code == 429 or "429" in error_msg or "quota" in error_msg.lower():
                        logger.warning(f"⚠️ Error de cuota detectado en intento {attempt + 1}/{max_retries}. Rotando API key...")
                        
                        retry_after = None
                        try:
                            import re
                            retry_match = re.search(r'retry in ([\d.]+)s', error_msg, re.IGNORECASE)
                            if retry_match:
                                retry_after = int(float(retry_match.group(1)))
                        except (ValueError, AttributeError):
                            pass
                        
                        # Crear error detallado
                        quota_error = Exception(f"Error de cuota en Gemini API (HTTP {response.status_code}): {error_msg}")
                        last_error = quota_error
                        
                        # Rotar a siguiente key
                        if self.gemini_client._handle_quota_error(quota_error, retry_after):
                            # Log eliminado: se registra en gemini_client.py para evitar redundancia
                            import time
                            time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2))
                            continue  # Reintentar con nueva key
                        else:
                            # No hay más keys disponibles
                            logger.error("❌ No hay más API keys disponibles. Todas están agotadas.")
                            # last_error ya está establecido arriba (línea 383)
                            if attempt < max_retries - 1:
                                import time
                                time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2) * (attempt + 1))
                                continue
                            else:
                                raise Exception(f"Todas las API keys están agotadas después de {max_retries} intentos. Último error: {error_msg}")
                    else:
                        # Otro error HTTP, lanzar excepción con detalles
                        detailed_error = f"Error de API Gemini (HTTP {response.status_code}): {error_msg}"
                        if error_data:
                            error_details = error_data.get("error", {})
                            if error_details.get("status"):
                                detailed_error += f" [Status: {error_details['status']}]"
                            if error_details.get("code"):
                                detailed_error += f" [Code: {error_details['code']}]"
                        raise Exception(detailed_error)
                
                try:
                    result = response.json()
                except json.JSONDecodeError as json_err:
                    # Error al parsear JSON de respuesta HTTP
                    last_error = Exception(f"Error al parsear respuesta JSON de Gemini API (HTTP {response.status_code}): {str(json_err)}. Respuesta recibida: {response.text[:200]}")
                    self.api_key_manager.record_api_call(self.gemini_client.api_key, success=False)
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2))
                        continue
                    else:
                        raise last_error
                
                # Extraer texto de respuesta
                if "candidates" in result and len(result["candidates"]) > 0:
                    content = result["candidates"][0].get("content", {})
                    parts = content.get("parts", [])
                    if parts and "text" in parts[0]:
                        self.api_key_manager.record_api_call(self.gemini_client.api_key, success=True)
                        return parts[0]["text"].strip()
                    else:
                        # Detectar el tipo específico de problema
                        if "candidates" in result and len(result["candidates"]) > 0:
                            candidate = result["candidates"][0]
                            if "finishReason" in candidate:
                                finish_reason = candidate["finishReason"]
                                if finish_reason == "SAFETY":
                                    raise Exception("Respuesta bloqueada por filtros de seguridad de Gemini")
                                elif finish_reason == "RECITATION":
                                    raise Exception("Respuesta bloqueada por detección de recitación (contenido duplicado)")
                                elif finish_reason == "OTHER":
                                    raise Exception(f"Respuesta bloqueada por Gemini (finishReason: {finish_reason})")
                                else:
                                    raise Exception(f"No se encontró texto en la respuesta de Gemini (finishReason: {finish_reason})")
                            else:
                                raise Exception("No se encontró texto en la respuesta de Gemini (candidato sin finishReason)")
                        else:
                            raise Exception("No se encontró texto en la respuesta de Gemini (sin candidatos)")
                else:
                    # Respuesta sin candidatos - puede ser bloqueo o error
                    if "promptFeedback" in result:
                        feedback = result["promptFeedback"]
                        if feedback.get("blockReason"):
                            block_reason = feedback["blockReason"]
                            raise Exception(f"Prompt bloqueado por Gemini (blockReason: {block_reason})")
                    raise Exception(f"Respuesta inesperada de Gemini: sin candidatos. Respuesta completa: {str(result)[:500]}")
                    
            except requests.Timeout as e:
                logger.error(f"⏱️ Timeout después de {api_timeout}s en llamada de predicción. No se rotará de key para evitar marcar todas como agotadas.")
                # No rotar ni marcar como agotada por timeout: reintentar solo hasta max_retries
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ Reintentando en {EXTRACTION_CONFIG.get('retry_delay', 2)}s con la MISMA API key...")
                    import time
                    time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2) * (attempt + 1))
                    last_error = Exception(f"Timeout de {api_timeout}s excedido en llamada a Gemini API (intento {attempt + 1}/{max_retries})")
                    continue
                else:
                    raise Exception(f"Timeout de {api_timeout}s excedido después de {max_retries} intentos en llamada a Gemini API")
            except requests.ConnectionError as e:
                logger.error(f"🔌 Error de conexión: {e}")
                # Para errores de conexión, no rotar (no es problema de cuota)
                error_detail = str(e)
                if "Name resolution failed" in error_detail or "DNS" in error_detail:
                    raise Exception(f"Error de conexión con Gemini API: No se pudo resolver el nombre del servidor (DNS)")
                elif "Connection refused" in error_detail:
                    raise Exception(f"Error de conexión con Gemini API: Conexión rechazada por el servidor")
                elif "timeout" in error_detail.lower():
                    raise Exception(f"Error de conexión con Gemini API: Timeout al establecer conexión")
                else:
                    raise Exception(f"Error de conexión con Gemini API: {error_detail}")
            except json.JSONDecodeError as e:
                # Error al parsear JSON de respuesta
                last_error = Exception(f"Error al parsear respuesta JSON de Gemini API: {str(e)}. Posible respuesta corrupta o inválida.")
                self.api_key_manager.record_api_call(self.gemini_client.api_key, success=False)
                if attempt < max_retries - 1:
                    import time
                    time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2))
                    continue
                else:
                    raise last_error
            except Exception as e:
                last_error = e
                error_str = str(e)
                error_type = type(e).__name__
                self.api_key_manager.record_api_call(self.gemini_client.api_key, success=False)
                
                # Si es error de cuota y no se manejó arriba, intentar rotar
                if ("429" in error_str or "quota" in error_str.lower() or 
                    "ResourceExhausted" in error_type):
                    logger.warning(f"⚠️ Error de cuota detectado en excepción. Rotando API key...")
                    
                    retry_after = None
                    try:
                        import re
                        retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                        if retry_match:
                            retry_after = int(float(retry_match.group(1)))
                    except (ValueError, AttributeError):
                        pass
                    
                    if self.gemini_client._handle_quota_error(e, retry_after):
                        # Log eliminado: se registra en gemini_client.py para evitar redundancia
                        import time
                        time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2))
                        # last_error ya está establecido arriba
                        continue  # Reintentar con nueva key
                    else:
                        if attempt < max_retries - 1:
                            import time
                            time.sleep(EXTRACTION_CONFIG.get("retry_delay", 2) * (attempt + 1))
                            # last_error ya está establecido arriba
                            continue
                        else:
                            raise Exception(f"Todas las API keys están agotadas después de {max_retries} intentos. Último error: [{error_type}] {error_str}")
                else:
                    # Otro tipo de error, mejorar mensaje y no reintentar
                    enhanced_error = Exception(f"Error inesperado al llamar a Gemini API (intento {attempt + 1}/{max_retries}): [{error_type}] {error_str}")
                    raise enhanced_error from e
        
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
            # Este caso no debería ocurrir, pero si ocurre, es un bug en la lógica
            # Intentar crear un error genérico con información de contexto
            error_context = f"max_retries={max_retries}, last_attempt={last_attempt_info if last_attempt_info else 'N/A'}"
            logger.error(f"⚠️ BUG: Se llegó al final del loop de reintentos sin capturar ningún error. {error_context}")
            # Crear un error genérico pero informativo
            generic_error = Exception(
                f"Error desconocido al llamar al LLM después de {max_retries} intentos. "
                f"No se capturó ningún error específico en ningún intento. "
                f"Esto indica un problema en la lógica de manejo de errores. "
                f"Contexto: {error_context}"
            )
            last_error = generic_error
            raise generic_error
    
    def _parse_prediction_response(self, response_text: str) -> PrediccionConcurso:
        """
        Parsea la respuesta del LLM a un objeto PrediccionConcurso.
        
        Args:
            response_text: Texto JSON de respuesta
            
        Returns:
            Objeto PrediccionConcurso
        """
        # Limpiar si viene envuelto en markdown
        if "```json" in response_text:
            import re
            match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if match:
                response_text = match.group(1)
        elif "```" in response_text:
            import re
            match = re.search(r'```\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if match:
                response_text = match.group(1)
        
        # Parsear JSON
        try:
            data = json.loads(response_text)
            
            # Validar estructura
            if "prediccion" not in data:
                # Intentar detectar qué campos tiene la respuesta
                available_keys = list(data.keys()) if isinstance(data, dict) else "no es un objeto"
                raise ValueError(f"Respuesta del LLM no contiene campo 'prediccion'. Campos disponibles: {available_keys}")
            
            prediccion_data = data["prediccion"]
            
            # Validar que prediccion_data es un diccionario
            if not isinstance(prediccion_data, dict):
                raise ValueError(f"Campo 'prediccion' no es un objeto válido. Tipo recibido: {type(prediccion_data).__name__}")
            
            # Crear objeto PrediccionConcurso con validación detallada
            try:
                return PrediccionConcurso(**prediccion_data)
            except TypeError as e:
                # Detectar qué campos faltan o son inválidos
                required_fields = ["es_mismo_concurso", "fecha_predicha", "justificacion"]
                missing_fields = [f for f in required_fields if f not in prediccion_data]
                if missing_fields:
                    raise ValueError(f"Faltan campos requeridos en la respuesta del LLM: {missing_fields}. Campos presentes: {list(prediccion_data.keys())}")
                else:
                    raise ValueError(f"Error al crear PrediccionConcurso: {str(e)}. Datos recibidos: {prediccion_data}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Error al parsear JSON de predicción: {e}")
            logger.error(f"Respuesta recibida (primeros 500 chars): {response_text[:500]}")
            raise ValueError(f"Respuesta del LLM no es JSON válido: {str(e)}. Posición del error: línea {e.lineno}, columna {e.colno}")
        except ValueError as e:
            # Re-lanzar ValueError con más contexto
            logger.error(f"Error de validación al procesar respuesta del LLM: {e}")
            raise
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Error inesperado al procesar respuesta del LLM: [{error_type}] {str(e)}")
            raise Exception(f"Error al procesar respuesta del LLM: [{error_type}] {str(e)}") from e

    def _parse_prediction_batch_response(self, response_text: str) -> Dict[str, PrediccionConcurso]:
        """
        Parsea la respuesta del LLM para un batch de concursos.
        
        Args:
            response_text: Texto JSON de respuesta
            
        Returns:
            Diccionario {concurso_url: PrediccionConcurso}
        """
        # Limpiar si viene envuelto en markdown
        if "```json" in response_text:
            import re
            match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if match:
                response_text = match.group(1)
        elif "```" in response_text:
            import re
            match = re.search(r'```\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if match:
                response_text = match.group(1)
        
        try:
            data = json.loads(response_text)
            
            if "items" not in data or not isinstance(data["items"], list):
                available_keys = list(data.keys()) if isinstance(data, dict) else "no es un objeto"
                raise ValueError(
                    f"Respuesta del LLM para batch no contiene lista 'items' válida. "
                    f"Campos disponibles: {available_keys}"
                )
            
            result: Dict[str, PrediccionConcurso] = {}
            for raw_item in data["items"]:
                try:
                    item = PrediccionConcursoBatchItem(**raw_item)
                    result[item.concurso_url] = item.prediccion
                except Exception as e:
                    logger.error(
                        f"Error al validar elemento de batch en respuesta del LLM: {e}. "
                        f"Datos recibidos: {raw_item}"
                    )
                    continue
            
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Error al parsear JSON de predicción (batch): {e}")
            logger.error(f"Respuesta recibida (primeros 500 chars): {response_text[:500]}")
            raise ValueError(
                f"Respuesta del LLM (batch) no es JSON válido: {str(e)}. "
                f"Posición del error: línea {e.lineno}, columna {e.colno}"
            )
        except ValueError as e:
            logger.error(f"Error de validación al procesar respuesta de batch del LLM: {e}")
            raise
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Error inesperado al procesar respuesta de batch del LLM: [{error_type}] {str(e)}")
            raise Exception(
                f"Error al procesar respuesta de batch del LLM: [{error_type}] {str(e)}"
            ) from e

    # Método deprecated: la asignación de confianza fue eliminada.
    def assign_confidence_batch(self, concursos_data: list, max_retries: int = 3) -> dict:
        return {}
