"""
Servicio independiente para generar predicciones de fechas de apertura de concursos.

Este servicio:
1. Carga concursos desde el historial por sitio
2. Permite aplicar filtros (estado, subdirección, término de búsqueda)
3. Usa exclusivamente los datos ya almacenados en el historial, en particular
   el campo ``previous_concursos`` extraído en la fase de scraping
4. Llama al LLM para predecir fechas futuras basándose en esos patrones históricos
5. Marca y persiste concursos no predecibles (por referencia a sí mismos o rechazo del LLM)
6. Genera archivos de debug separados para ejecuciones en lote e individuales
"""

import asyncio
import logging
import re
import traceback
import os
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
from urllib.parse import urlparse

from llm.predictor import ConcursoPredictor
from utils.history_manager import HistoryManager
from utils.anid_previous_concursos import format_previous_concursos_for_prediction
from utils.file_manager import save_predictions, save_debug_info_predictions, save_unpredictable_concursos
from utils.date_parser import parse_date, is_past_date
from utils.lock_manager import is_operation_locked
from config import EXTRACTION_CONFIG, PREDICTIONS_DIR

logger = logging.getLogger(__name__)


class PredictionService:
    """Servicio para generar predicciones de fechas de apertura de concursos"""
    
    def __init__(
        self,
        history_manager: Optional[HistoryManager] = None,
        api_key_manager=None,
        model_name: Optional[str] = None
    ):
        """
        Inicializa el servicio de predicciones.
        
        Args:
            history_manager: Gestor de historial (si None, se crea uno nuevo)
            api_key_manager: Gestor de API keys para LLM
            model_name: Nombre del modelo LLM a usar
        """
        self.history_manager = history_manager or HistoryManager()
        
        # Inicializar predictor
        from config import GEMINI_CONFIG, EXTRACTION_CONFIG
        
        if model_name is None:
            model_name = GEMINI_CONFIG.get("model", "gemini-2.5-flash-lite")
        
        self.predictor = ConcursoPredictor(
            api_key_manager=api_key_manager,
            model_name=model_name,
            config=EXTRACTION_CONFIG
        )
    
    def generate_predictions(
        self,
        site: str,
        filters: Optional[Dict[str, Any]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        should_stop_callback: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any]:
        """
        Genera predicciones para concursos de un sitio.
        
        Args:
            site: Nombre del sitio (ej: "anid.cl")
            filters: Diccionario con filtros a aplicar:
                - estado: "Cerrado", "Abierto", None (todos)
                - subdireccion: str o None
                - search_term: str o None
            status_callback: Función para reportar progreso
            should_stop_callback: Función que retorna True si se debe detener
            
        Returns:
            Diccionario con resultados:
            {
                "predictions": [...],
                "debug_info": {...},
                "stats": {...}
            }
        """
        # Resiliencia: si hay scraping en curso para el mismo sitio, avisar y no continuar
        if is_operation_locked(site, "scrape"):
            msg = f"Hay un scraping en curso para {site}. Espera a que finalice antes de generar predicciones."
            logger.warning(msg)
            return {"predictions": [], "debug_info": {"error": msg, "site": site}, "stats": {}}

        start_time = datetime.now()
        debug_info = {
            "execution": {
                "start_time": start_time.isoformat(),
                "site": site,
                "filters": filters or {}
            },
            "scraping": {
                "urls_scraped": 0,
                "urls_failed": 0,
                "previous_concursos_extracted": {}
            },
            "predictions": {
                "total_analyzed": 0,
                "successful": 0,
                "failed": 0,
                "filtered": 0,
                "errors": [],
                "filters": []
            },
            "stats": {}
        }
        
        # Cargar concursos del historial
        if status_callback:
            status_callback(f"📚 Cargando concursos de {site}...")
        
        history = self.history_manager.load_history(site)
        all_concursos = []
        
        for hist_concurso in history.get("concursos", []):
            versions = hist_concurso.get("versions", [])
            if versions:
                latest = versions[-1]
                concurso = {
                    "nombre": hist_concurso.get("nombre"),
                    "url": hist_concurso.get("url"),
                    "organismo": hist_concurso.get("organismo"),
                    "fecha_apertura": latest.get("fecha_apertura"),
                    "fecha_cierre": latest.get("fecha_cierre"),
                    "estado": latest.get("estado"),
                    "subdireccion": hist_concurso.get("subdireccion") or latest.get("subdireccion"),
                    "first_seen": hist_concurso.get("first_seen"),
                    "last_seen": hist_concurso.get("last_seen")
                }
                all_concursos.append(concurso)
        
        logger.info(f"📚 Cargados {len(all_concursos)} concursos del historial de {site}")
        
        # Aplicar filtros
        filtered_concursos = self._apply_filters(all_concursos, filters or {})
        logger.info(f"🔍 Después de filtros: {len(filtered_concursos)} concursos")
        
        debug_info["stats"]["total_concursos"] = len(all_concursos)
        debug_info["stats"]["filtered_concursos"] = len(filtered_concursos)
        
        if not filtered_concursos:
            logger.warning(f"⚠️ No hay concursos para analizar después de aplicar filtros")
            debug_info["execution"]["end_time"] = datetime.now().isoformat()
            debug_info["execution"]["duration_seconds"] = (
                datetime.now() - start_time
            ).total_seconds()
            return {
                "predictions": [],
                "debug_info": debug_info,
                "stats": debug_info["stats"]
            }
        
        # Filtrar solo concursos cerrados (solo estos pueden tener predicciones)
        closed_concursos = [c for c in filtered_concursos if c.get("estado") == "Cerrado"]
        logger.info(f"🔒 {len(closed_concursos)} concursos cerrados para analizar")
        special_domains_allow_without_previous = {"centroestudios.mineduc.cl"}
        
        # Evitar predecir concursos que ya tienen predicción guardada
        existing_pred_urls: set[str] = set()
        try:
            safe_site = site.replace(".", "_").replace("/", "_")
            pred_path = os.path.join(PREDICTIONS_DIR, f"predictions_{safe_site}.json")
            if os.path.exists(pred_path):
                with open(pred_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    existing_pred_urls = {
                        p.get("concurso_url") for p in data.get("predictions", []) if p.get("concurso_url")
                    }
        except Exception as e:
            logger.warning(f"⚠️ No se pudieron cargar predicciones existentes para evitar duplicados: {e}")
            existing_pred_urls = set()

        # Crear índice del historial por URL para búsqueda eficiente O(1)
        history_index_by_url = {}
        for hist_concurso in history.get("concursos", []):
            url = hist_concurso.get("url")
            if url:
                history_index_by_url[url] = hist_concurso
        
        # Filtrar concursos que tienen previous_concursos en el historial
        # Solo estos pueden tener predicciones (necesitan versiones anteriores)
        concursos_con_versiones_previas = []
        for concurso in closed_concursos:
            concurso_url = concurso.get("url")
            if not concurso_url:
                continue
            if concurso_url in existing_pred_urls:
                logger.info(
                    f"⏭️ Concurso ya tiene predicción guardada, se omite: {concurso.get('nombre', 'N/A')} ({concurso_url})"
                )
                continue
            
            # Buscar en el índice del historial si tiene previous_concursos
            hist_concurso = history_index_by_url.get(concurso_url)
            if hist_concurso:
                previous_concursos = hist_concurso.get("previous_concursos", [])
                # Solo incluir si tiene versiones anteriores (lista no vacía)
                if previous_concursos:
                    concursos_con_versiones_previas.append(concurso)
                else:
                    domain = urlparse(concurso_url).netloc.replace("www.", "")
                    if domain in special_domains_allow_without_previous:
                        concursos_con_versiones_previas.append(concurso)
            else:
                domain = urlparse(concurso_url).netloc.replace("www.", "")
                if domain in special_domains_allow_without_previous:
                    concursos_con_versiones_previas.append(concurso)
                    continue
                logger.warning(
                    f"⚠️ Concurso '{concurso.get('nombre', 'N/A')}' ({concurso_url}) no encontrado en historial. "
                    f"Puede que no haya sido scrapeado aún."
                )
        
        closed_concursos = concursos_con_versiones_previas
        logger.info(f"📚 {len(closed_concursos)} concursos cerrados con versiones anteriores disponibles para predicción")
        
        if not closed_concursos:
            logger.warning(f"⚠️ No hay concursos cerrados para generar predicciones")
            debug_info["execution"]["end_time"] = datetime.now().isoformat()
            debug_info["execution"]["duration_seconds"] = (
                datetime.now() - start_time
            ).total_seconds()
            return {
                "predictions": [],
                "debug_info": debug_info,
                "stats": debug_info["stats"]
            }
        
        # Leer "Concursos anteriores" del historial (ya fueron extraídos durante el scraping)
        if status_callback:
            status_callback(f"📚 Leyendo 'Concursos anteriores' del historial para {len(closed_concursos)} concursos...")
        
        predictions_to_save = []
        unpredictable_to_save = []
        
        async def generate_predictions_from_history():
            """Genera predicciones usando datos del historial (sin scrapear), en batches."""
            BATCH_SIZE = 10
            
            # OPTIMIZACIÓN: Filtrar primero todos los no predecibles ANTES de crear batches
            # Esto garantiza que cada batch tenga exactamente 10 concursos (o menos solo en el último)
            concursos_predecibles = []
            
            logger.info(f"🔍 Filtrando concursos no predecibles antes de crear batches...")
            for concurso in closed_concursos:
                concurso_url = concurso.get("url")
                if not concurso_url:
                    continue
                
                debug_info["predictions"]["total_analyzed"] += 1
                
                try:
                    # Obtener previous_concursos del índice del historial
                    hist_concurso = history_index_by_url.get(concurso_url)
                    if not hist_concurso:
                        # Caso raro: marcar como no predecible
                        logger.warning(
                            f"⚠️ Concurso '{concurso.get('nombre', 'N/A')}' no encontrado en historial. "
                            f"Marcando como no predecible."
                        )
                        unpredictable_entry = {
                            "concurso_nombre": concurso.get("nombre", ""),
                            "concurso_url": concurso_url,
                            "justificacion": "El concurso no se encontró en el historial durante el procesamiento.",
                            "previous_concursos": [],
                            "reason": "not_found_in_history",
                            "marked_at": datetime.now().isoformat()
                        }
                        unpredictable_to_save.append(unpredictable_entry)
                        debug_info["predictions"]["filtered"] += 1
                        debug_info["predictions"]["filters"].append({
                            "concurso_nombre": concurso.get("nombre"),
                            "concurso_url": concurso_url,
                            "filter_reason": "unpredictable",
                            "reason": "not_found_in_history",
                            "justificacion": "El concurso no se encontró en el historial durante el procesamiento.",
                            "source": "pre_batch_filtering"
                        })
                        continue
                    
                    previous_concursos = hist_concurso.get("previous_concursos", [])
                    domain = urlparse(concurso_url).netloc.replace("www.", "")
                    # Sitio especial: centroestudios.mineduc.cl (FONIDE anual)
                    if domain == "centroestudios.mineduc.cl" and not previous_concursos:
                        base_date_str = concurso.get("fecha_apertura") or concurso.get("fecha_cierre")
                        if base_date_str:
                            parsed = parse_date(base_date_str)
                            prev_year = parsed.year if parsed else None
                            previous_concursos = [{
                                "nombre": concurso.get("nombre", ""),
                                "fecha_apertura": base_date_str,
                                "fecha_cierre": None,
                                "año": prev_year,
                            }]
                    
                    if not previous_concursos:
                        logger.debug(
                            f"ℹ️ Concurso '{concurso.get('nombre', 'N/A')}' no tiene versiones anteriores. "
                            f"Marcando como no predecible."
                        )
                        unpredictable_entry = {
                            "concurso_nombre": concurso.get("nombre", ""),
                            "concurso_url": concurso_url,
                            "justificacion": "El concurso no tiene versiones anteriores disponibles en el historial.",
                            "previous_concursos": [],
                            "reason": "no_previous_versions",
                            "marked_at": datetime.now().isoformat()
                        }
                        unpredictable_to_save.append(unpredictable_entry)
                        debug_info["predictions"]["filtered"] += 1
                        debug_info["predictions"]["filters"].append({
                            "concurso_nombre": concurso.get("nombre"),
                            "concurso_url": concurso_url,
                            "filter_reason": "unpredictable",
                            "reason": "no_previous_versions",
                            "justificacion": "El concurso no tiene versiones anteriores disponibles en el historial.",
                            "source": "pre_batch_filtering"
                        })
                        continue
                    
                    # Para sitios especiales, aplicar predicción determinística anual y saltar LLM
                    if domain == "centroestudios.mineduc.cl":
                        pred_entry = self._predict_centro_estudios(concurso, previous_concursos)
                        if pred_entry:
                            predictions_to_save.append(pred_entry)
                            debug_info["predictions"]["successful"] += 1
                            debug_info["scraping"]["previous_concursos_extracted"][concurso_url] = {
                                "count": len(previous_concursos),
                                "concursos": previous_concursos,
                                "source": "history_or_synthesized"
                            }
                            if status_callback:
                                status_callback(
                                    f"✅ Predicción (regla anual) para '{concurso.get('nombre', 'N/A')}': {pred_entry['fecha_predicha']}"
                                )
                            continue
                        else:
                            auto_justificacion = "No se pudo estimar fecha base para aplicar la regla anual de FONIDE."
                            unpredictable_entry = {
                                "concurso_nombre": concurso.get("nombre", ""),
                                "concurso_url": concurso_url,
                                "justificacion": auto_justificacion,
                                "previous_concursos": previous_concursos,
                                "reason": "missing_base_date",
                                "marked_at": datetime.now().isoformat()
                            }
                            unpredictable_to_save.append(unpredictable_entry)
                            debug_info["predictions"]["filtered"] += 1
                            debug_info["predictions"]["filters"].append({
                                "concurso_nombre": concurso.get("nombre"),
                                "concurso_url": concurso_url,
                                "filter_reason": "unpredictable",
                                "reason": "missing_base_date",
                                "justificacion": auto_justificacion,
                                "source": "pre_batch_filtering"
                            })
                            continue
                    

                    # Detectar si todos los previous_concursos son referencias a sí mismo
                    all_self_references = all(
                        prev.get("url", "").strip() == concurso_url.strip() 
                        for prev in previous_concursos 
                        if prev.get("url")
                    )
                    
                    if all_self_references:
                        # Marcar como no predecible ANTES de crear batches
                        auto_justificacion = (
                            "El sistema detectó que el único concurso previo listado en la página del concurso "
                            f"\"{concurso.get('nombre', 'N/A')}\" es un enlace al mismo concurso (misma URL). "
                            "El sistema utiliza esta información para concluir que no existen concursos previos reales que respalden una predicción."
                        )
                        unpredictable_entry = {
                            "concurso_nombre": concurso.get("nombre", ""),
                            "concurso_url": concurso_url,
                            "justificacion": auto_justificacion,
                            "previous_concursos": previous_concursos,
                            "reason": "self_reference",
                            "marked_at": datetime.now().isoformat()
                        }
                        unpredictable_to_save.append(unpredictable_entry)
                        debug_info["predictions"]["filtered"] += 1
                        debug_info["predictions"]["filters"].append({
                            "concurso_nombre": concurso.get("nombre"),
                            "concurso_url": concurso_url,
                            "filter_reason": "unpredictable",
                            "reason": "self_reference",
                            "justificacion": auto_justificacion,
                            "source": "pre_batch_filtering"
                        })
                        logger.info(
                            f"⚠️ Concurso '{concurso.get('nombre', 'N/A')}' marcado como no predecible por referencia a sí mismo "
                            "(único concurso previo apunta a la misma URL)."
                        )
                        continue
                    
                    # Si llegamos aquí, el concurso ES predecible → agregar a la lista
                    logger.info(
                        f"✅ Usando {len(previous_concursos)} concursos anteriores del historial para {concurso.get('nombre', 'N/A')}"
                    )
                    debug_info["scraping"]["previous_concursos_extracted"][concurso_url] = {
                        "count": len(previous_concursos),
                        "concursos": previous_concursos,
                        "source": "history"
                    }
                    
                    concursos_predecibles.append({
                        "concurso": concurso,
                        "concurso_url": concurso_url,
                        "previous_concursos": previous_concursos,
                    })
                    
                except Exception as e:
                    logger.error(f"Error al preparar concurso {concurso_url} para batch: {e}", exc_info=True)
                    debug_info["predictions"]["failed"] += 1
                    debug_info["predictions"]["errors"].append({
                        "concurso_nombre": concurso.get("nombre"),
                        "concurso_url": concurso_url,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "timestamp": datetime.now().isoformat()
                    })
            
            logger.info(
                f"📊 Filtrado completado: {len(concursos_predecibles)} concursos predecibles "
                f"de {len(closed_concursos)} totales. Creando batches de {BATCH_SIZE}..."
            )
            
            # Ahora crear batches de exactamente BATCH_SIZE con los concursos predecibles
            for batch_start in range(0, len(concursos_predecibles), BATCH_SIZE):
                if should_stop_callback and should_stop_callback():
                    logger.info("Proceso detenido durante generación de predicciones")
                    break
                
                batch_predecibles = concursos_predecibles[batch_start: batch_start + BATCH_SIZE]
                batch_for_llm = []
                
                # Preparar datos para el batch (todos estos son predecibles, ya fueron filtrados)
                for item in batch_predecibles:
                    concurso = item["concurso"]
                    concurso_url = item["concurso_url"]
                    previous_concursos = item["previous_concursos"]
                    
                    try:
                        # Todos estos concursos ya fueron validados como predecibles
                        previous_concursos_info = format_previous_concursos_for_prediction(previous_concursos)
                        
                        concurso_dict = {
                            "nombre": concurso.get("nombre", ""),
                            "url": concurso_url,
                            "fecha_apertura": concurso.get("fecha_apertura") or "",
                            "fecha_cierre": concurso.get("fecha_cierre") or "",
                            "organismo": concurso.get("organismo", ""),
                            "descripcion": ""
                        }
                        
                        batch_for_llm.append({
                            "concurso": concurso_dict,
                            "concurso_url": concurso_url,
                            "previous_concursos": previous_concursos,
                            "previous_concursos_info": previous_concursos_info,
                        })
                    except Exception as e:
                        logger.error(f"Error al preparar concurso {concurso_url} para batch: {e}", exc_info=True)
                        debug_info["predictions"]["failed"] += 1
                        debug_info["predictions"]["errors"].append({
                            "concurso_nombre": concurso.get("nombre"),
                            "concurso_url": concurso_url,
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "timestamp": datetime.now().isoformat()
                        })
                
                # Validación de seguridad: si el batch está vacío (no debería pasar después del filtrado previo)
                if not batch_for_llm:
                    logger.warning(f"⚠️ Batch vacío detectado (índice {batch_start}). Esto no debería pasar después del filtrado previo.")
                    continue
                
                # Llamar al LLM una sola vez para el batch (con reintentos automáticos internos)
                try:
                    batch_predictions = self.predictor.predict_from_previous_concursos_batch(batch_for_llm)
                except Exception as e:
                    error_str = str(e)
                    error_type = type(e).__name__
                    
                    # Verificar si es un error crítico después de agotar reintentos
                    is_critical_error = "Error crítico" in error_str or "Se detendrá la ejecución" in error_str
                    
                    if is_critical_error:
                        # Error crítico: agotados los reintentos, detener ejecución
                        debug_info["predictions"]["failed"] += len(batch_for_llm)
                        
                        error_entry = {
                            "concurso_nombre": "BATCH_CRITICAL",
                            "concurso_url": f"Batch de {len(batch_for_llm)} concursos",
                            "source": "previous_concursos_batch",
                            "error": error_str,
                            "error_type": error_type,
                            "timestamp": datetime.now().isoformat(),
                            "critical": True,
                            "execution_stopped": True
                        }
                        debug_info["predictions"]["errors"].append(error_entry)
                        
                        logger.error(
                            f"❌ ERROR CRÍTICO: No se pudo procesar batch después de 3 reintentos. "
                            f"Deteniendo ejecución de predicciones. Error: [{error_type}] {error_str}"
                        )
                        
                        # Finalizar debug y guardar antes de detener
                        end_time = datetime.now()
                        debug_info["execution"]["end_time"] = end_time.isoformat()
                        debug_info["execution"]["duration_seconds"] = (
                            end_time - start_time
                        ).total_seconds()
                        debug_info["execution"]["stopped_early"] = True
                        debug_info["execution"]["stop_reason"] = f"Error crítico en batch después de 3 reintentos: {error_str}"
                        debug_info["stats"]["predictions_saved"] = len(predictions_to_save)
                        
                        # Guardar archivo de debug
                        try:
                            from utils.file_manager import save_debug_info_predictions
                            debug_file_path = save_debug_info_predictions(debug_info)
                            logger.error(f"🐛 Archivo de debug guardado antes de detener: {debug_file_path}")
                        except Exception as debug_err:
                            logger.error(f"Error al guardar debug antes de detener: {debug_err}")
                        
                        # Detener ejecución
                        if status_callback:
                            status_callback(f"❌ Error crítico: Deteniendo ejecución de predicciones")
                        
                        return {
                            "predictions": predictions_to_save,
                            "debug_info": debug_info,
                            "stats": debug_info["stats"]
                        }
                    else:
                        # Error no crítico: marcar TODOS los concursos del batch como no predecibles
                        # (no se puede saltar ningún concurso sin resultado)
                        for item in batch_for_llm:
                            concurso_dict = item["concurso"]
                            concurso_url = item["concurso_url"]
                            previous_concursos = item["previous_concursos"]
                            concurso_nombre = concurso_dict.get("nombre", "")
                            
                            unpredictable_entry = {
                                "concurso_nombre": concurso_nombre,
                                "concurso_url": concurso_url,
                                "justificacion": f"Error al procesar batch: [{error_type}] {error_str}",
                                "previous_concursos": previous_concursos,
                                "reason": "batch_error",
                                "marked_at": datetime.now().isoformat()
                            }
                            unpredictable_to_save.append(unpredictable_entry)
                            debug_info["predictions"]["filtered"] += 1
                            debug_info["predictions"]["filters"].append({
                                "concurso_nombre": concurso_nombre,
                                "concurso_url": concurso_url,
                                "filter_reason": "unpredictable",
                                "reason": "batch_error",
                                "justificacion": f"Error al procesar batch: [{error_type}] {error_str}",
                                "source": "previous_concursos_batch"
                            })
                        
                        debug_info["predictions"]["failed"] += len(batch_for_llm)
                        
                        error_entry = {
                            "concurso_nombre": "BATCH",
                            "concurso_url": "BATCH",
                            "source": "previous_concursos_batch",
                            "error": error_str,
                            "error_type": error_type,
                            "timestamp": datetime.now().isoformat(),
                            "concursos_affected": len(batch_for_llm)
                        }
                        debug_info["predictions"]["errors"].append(error_entry)
                        
                        # Log especializado para errores de cuota
                        if "429" in error_str or ("quota" in error_str.lower() and "retry in" in error_str.lower()):
                            retry_match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                            if retry_match:
                                retry_after = int(float(retry_match.group(1)))
                                error_entry["retry_after_seconds"] = retry_after
                                logger.warning(
                                    f"⏱️ Rate limit temporal al generar predicciones para un batch de {len(batch_for_llm)} concursos. "
                                    f"El sistema puede esperar {retry_after}s antes de reintentar. "
                                    f"Todos los concursos del batch fueron marcados como no predecibles."
                                )
                            else:
                                logger.error(f"Error al generar predicciones para batch: {error_str}")
                        else:
                            logger.error(
                                f"Error al generar predicciones para batch: {error_str}. "
                                f"Todos los {len(batch_for_llm)} concursos del batch fueron marcados como no predecibles."
                            )
                        
                        continue
                
                # Procesar predicciones recibidas del LLM
                # IMPORTANTE: TODOS los concursos en batch_for_llm DEBEN tener un resultado
                # (predicción válida o marcado como no predecible)
                for item in batch_for_llm:
                    concurso_dict = item["concurso"]
                    concurso_url = item["concurso_url"]
                    previous_concursos = item["previous_concursos"]
                    concurso_nombre = concurso_dict.get("nombre", "")
                    
                    prediccion = batch_predictions.get(concurso_url)
                    if not prediccion:
                        # El LLM no devolvió predicción para este concurso concreto
                        # DEBE marcarse como no predecible (no se puede saltar)
                        unpredictable_entry = {
                            "concurso_nombre": concurso_nombre,
                            "concurso_url": concurso_url,
                            "justificacion": "El LLM no devolvió una predicción para este concurso en el batch procesado.",
                            "previous_concursos": previous_concursos,
                            "reason": "llm_no_response",
                            "marked_at": datetime.now().isoformat()
                        }
                        unpredictable_to_save.append(unpredictable_entry)
                        debug_info["predictions"]["filtered"] += 1
                        debug_info["predictions"]["filters"].append({
                            "concurso_nombre": concurso_nombre,
                            "concurso_url": concurso_url,
                            "filter_reason": "unpredictable",
                            "reason": "llm_no_response",
                            "justificacion": "El LLM no devolvió una predicción para este concurso en el batch procesado.",
                            "source": "previous_concursos_batch"
                        })
                        logger.warning(
                            f"⚠️ Concurso '{concurso_nombre}' marcado como no predecible: "
                            f"LLM no devolvió predicción en batch."
                        )
                        continue
                    
                    # Caso: el LLM decide marcar como no predecible (justificación explícita, sin fecha)
                    if prediccion.es_mismo_concurso and not prediccion.fecha_predicha:
                        unpredictable_entry = {
                            "concurso_nombre": concurso_nombre,
                            "concurso_url": concurso_url,
                            "justificacion": prediccion.justificacion,
                            "previous_concursos": previous_concursos,
                            "reason": "llm_rejected",
                            "marked_at": datetime.now().isoformat()
                        }
                        unpredictable_to_save.append(unpredictable_entry)
                        debug_info["predictions"]["filtered"] += 1
                        debug_info["predictions"]["filters"].append({
                            "concurso_nombre": concurso_nombre,
                            "concurso_url": concurso_url,
                            "filter_reason": "unpredictable",
                            "reason": "llm_rejected",
                            "justificacion": prediccion.justificacion,
                            "source": "previous_concursos_batch"
                        })
                        logger.info(
                            f"⚠️ Concurso '{concurso_nombre}' marcado como no predecible por decisión del LLM."
                        )
                        continue
                    
                    # Predicción con fecha propuesta
                    if prediccion.es_mismo_concurso and prediccion.fecha_predicha:
                        parsed_date = parse_date(prediccion.fecha_predicha)
                        now = datetime.now()
                        # Año máximo de versiones previas (para evitar predecir en el mismo/año menor)
                        max_prev_year = None
                        try:
                            years_prev = []
                            for prev in previous_concursos or []:
                                # año explícito
                                if prev.get("año"):
                                    years_prev.append(int(prev["año"]))
                                # derivar de fechas
                                for campo in ("fecha_apertura", "fecha_cierre"):
                                    if prev.get(campo):
                                        y_match = re.search(r"^(\d{4})", prev[campo])
                                        if y_match:
                                            years_prev.append(int(y_match.group(1)))
                            if years_prev:
                                max_prev_year = max(years_prev)
                        except Exception:
                            max_prev_year = None
                        
                        if parsed_date:
                            if parsed_date.date() <= now.date():
                                # Fecha en el pasado: marcar como no predecible (no se puede saltar)
                                unpredictable_entry = {
                                    "concurso_nombre": concurso_nombre,
                                    "concurso_url": concurso_url,
                                    "justificacion": f"El LLM predijo una fecha en el pasado ({prediccion.fecha_predicha}), lo cual no es válido para una predicción futura.",
                                    "previous_concursos": previous_concursos,
                                    "reason": "invalid_date_past",
                                    "marked_at": datetime.now().isoformat()
                                }
                                unpredictable_to_save.append(unpredictable_entry)
                                debug_info["predictions"]["filtered"] += 1
                                debug_info["predictions"]["filters"].append({
                                    "concurso_nombre": concurso_nombre,
                                    "concurso_url": concurso_url,
                                    "raw_fecha_predicha": prediccion.fecha_predicha,
                                    "filter_reason": "unpredictable",
                                    "reason": "invalid_date_past",
                                    "justificacion": f"El LLM predijo una fecha en el pasado ({prediccion.fecha_predicha}), lo cual no es válido para una predicción futura.",
                                    "source": "previous_concursos_batch"
                                })
                                logger.warning(
                                    f"⚠️ Concurso '{concurso_nombre}' marcado como no predecible: "
                                    f"fecha predicha en el pasado ({prediccion.fecha_predicha})."
                                )
                                continue
                            
                            if max_prev_year is not None and parsed_date.year <= max_prev_year:
                                # Fecha no es estrictamente posterior al último año conocido
                                unpredictable_entry = {
                                    "concurso_nombre": concurso_nombre,
                                    "concurso_url": concurso_url,
                                    "justificacion": (
                                        f"La fecha predicha ({prediccion.fecha_predicha}) no es posterior "
                                        f"al último año conocido ({max_prev_year})."
                                    ),
                                    "previous_concursos": previous_concursos,
                                    "reason": "invalid_date_not_future_cycle",
                                    "marked_at": datetime.now().isoformat()
                                }
                                unpredictable_to_save.append(unpredictable_entry)
                                debug_info["predictions"]["filtered"] += 1
                                debug_info["predictions"]["filters"].append({
                                    "concurso_nombre": concurso_nombre,
                                    "concurso_url": concurso_url,
                                    "raw_fecha_predicha": prediccion.fecha_predicha,
                                    "filter_reason": "unpredictable",
                                    "reason": "invalid_date_not_future_cycle",
                                    "justificacion": (
                                        f"La fecha predicha ({prediccion.fecha_predicha}) no es posterior "
                                        f"al último año conocido ({max_prev_year})."
                                    ),
                                    "source": "previous_concursos_batch"
                                })
                                logger.warning(
                                    f"⚠️ Concurso '{concurso_nombre}' marcado como no predecible: "
                                    f"fecha no posterior al último año conocido ({max_prev_year})."
                                )
                                continue
                            
                            if (parsed_date.year - now.year) > 1:
                                # Fecha demasiado lejana: marcar como no predecible (no se puede saltar)
                                unpredictable_entry = {
                                    "concurso_nombre": concurso_nombre,
                                    "concurso_url": concurso_url,
                                    "justificacion": f"El LLM predijo una fecha demasiado lejana ({prediccion.fecha_predicha}), más de un año en el futuro, lo cual no es confiable.",
                                    "previous_concursos": previous_concursos,
                                    "reason": "invalid_date_too_far",
                                    "marked_at": datetime.now().isoformat()
                                }
                                unpredictable_to_save.append(unpredictable_entry)
                                debug_info["predictions"]["filtered"] += 1
                                debug_info["predictions"]["filters"].append({
                                    "concurso_nombre": concurso_nombre,
                                    "concurso_url": concurso_url,
                                    "raw_fecha_predicha": prediccion.fecha_predicha,
                                    "filter_reason": "unpredictable",
                                    "reason": "invalid_date_too_far",
                                    "justificacion": f"El LLM predijo una fecha demasiado lejana ({prediccion.fecha_predicha}), más de un año en el futuro, lo cual no es confiable.",
                                    "source": "previous_concursos_batch"
                                })
                                logger.warning(
                                    f"⚠️ Concurso '{concurso_nombre}' marcado como no predecible: "
                                    f"fecha predicha demasiado lejana ({prediccion.fecha_predicha})."
                                )
                                continue
                            
                            # Predicción válida
                            prediction_entry = {
                                "concurso_nombre": concurso_nombre,
                                "concurso_url": concurso_url,
                                "fecha_predicha": prediccion.fecha_predicha,
                                "justificacion": prediccion.justificacion,
                                "predicted_at": datetime.now().isoformat(),
                                "source": "previous_concursos",
                                "previous_concursos": previous_concursos,
                            }
                            predictions_to_save.append(prediction_entry)
                            debug_info["predictions"]["successful"] += 1
                            
                            if status_callback:
                                status_callback(
                                    f"✅ Predicción generada para '{concurso_nombre}': "
                                    f"{prediccion.fecha_predicha}"
                                )
                        else:
                            # Fecha no parseable: marcar como no predecible (no se puede saltar)
                            unpredictable_entry = {
                                "concurso_nombre": concurso_nombre,
                                "concurso_url": concurso_url,
                                "justificacion": f"El LLM devolvió una fecha no parseable: '{prediccion.fecha_predicha}'. No se pudo validar la predicción.",
                                "previous_concursos": previous_concursos,
                                "reason": "invalid_date_unparseable",
                                "marked_at": datetime.now().isoformat()
                            }
                            unpredictable_to_save.append(unpredictable_entry)
                            debug_info["predictions"]["filtered"] += 1
                            debug_info["predictions"]["filters"].append({
                                "concurso_nombre": concurso_nombre,
                                "concurso_url": concurso_url,
                                "raw_fecha_predicha": prediccion.fecha_predicha,
                                "filter_reason": "unpredictable",
                                "reason": "invalid_date_unparseable",
                                "justificacion": f"El LLM devolvió una fecha no parseable: '{prediccion.fecha_predicha}'. No se pudo validar la predicción.",
                                "source": "previous_concursos_batch"
                            })
                            logger.warning(
                                f"⚠️ Concurso '{concurso_nombre}' marcado como no predecible: "
                                f"fecha no parseable ({prediccion.fecha_predicha})."
                            )
                    else:
                        # Caso inesperado: tiene fecha_predicha pero no es_mismo_concurso o estructura inválida
                        # Generar justificación detallada basada en lo que recibimos
                        if not prediccion.es_mismo_concurso and prediccion.fecha_predicha:
                            justificacion_detallada = (
                                f"El LLM indicó que los concursos NO son el mismo (es_mismo_concurso=False) "
                                f"pero aún así proporcionó una fecha predicha ({prediccion.fecha_predicha}). "
                                f"Esta inconsistencia indica que la respuesta del LLM no es válida para este caso. "
                                f"Justificación del LLM: {prediccion.justificacion[:200] if prediccion.justificacion else 'N/A'}"
                            )
                        elif prediccion.es_mismo_concurso and not prediccion.fecha_predicha:
                            # Este caso ya debería estar manejado arriba como llm_rejected, pero por seguridad lo manejamos aquí también
                            justificacion_detallada = (
                                f"El LLM indicó que los concursos son el mismo (es_mismo_concurso=True) "
                                f"pero no proporcionó una fecha predicha. "
                                f"Justificación del LLM: {prediccion.justificacion[:200] if prediccion.justificacion else 'N/A'}"
                            )
                        else:
                            justificacion_detallada = (
                                f"La respuesta del LLM tiene una estructura inesperada: "
                                f"es_mismo_concurso={prediccion.es_mismo_concurso}, "
                                f"fecha_predicha={prediccion.fecha_predicha}. "
                                f"Justificación del LLM: {prediccion.justificacion[:200] if prediccion.justificacion else 'N/A'}"
                            )
                        
                        # Marcar como no predecible (no se puede saltar)
                        unpredictable_entry = {
                            "concurso_nombre": concurso_nombre,
                            "concurso_url": concurso_url,
                            "justificacion": justificacion_detallada,
                            "previous_concursos": previous_concursos,
                            "reason": "invalid_prediction_structure",
                            "marked_at": datetime.now().isoformat()
                        }
                        unpredictable_to_save.append(unpredictable_entry)
                        debug_info["predictions"]["filtered"] += 1
                        debug_info["predictions"]["filters"].append({
                            "concurso_nombre": concurso_nombre,
                            "concurso_url": concurso_url,
                            "filter_reason": "unpredictable",
                            "reason": "invalid_prediction_structure",
                            "justificacion": justificacion_detallada,
                            "llm_response": {
                                "es_mismo_concurso": prediccion.es_mismo_concurso,
                                "fecha_predicha": prediccion.fecha_predicha,
                                "justificacion": prediccion.justificacion
                            },
                            "source": "previous_concursos_batch"
                        })
                        logger.warning(
                            f"⚠️ Concurso '{concurso_nombre}' marcado como no predecible: "
                            f"estructura de predicción inválida. "
                            f"es_mismo_concurso={prediccion.es_mismo_concurso}, "
                            f"fecha_predicha={prediccion.fecha_predicha}"
                        )
        
        # Ejecutar generación de predicciones desde historial
        try:
            asyncio.run(generate_predictions_from_history())
        except Exception as e:
            logger.error(f"Error durante scraping y predicciones: {e}", exc_info=True)
        
        # Guardar predicciones
        if predictions_to_save:
            try:
                save_predictions(site, predictions_to_save)
                logger.info(f"💾 Guardadas {len(predictions_to_save)} predicciones para {site}")
            except Exception as e:
                logger.error(f"Error al guardar predicciones: {e}", exc_info=True)
        
        # Guardar concursos no predecibles
        if unpredictable_to_save:
            try:
                save_unpredictable_concursos(site, unpredictable_to_save)
                logger.info(f"⚠️ Guardados {len(unpredictable_to_save)} concursos no predecibles para {site}")
            except Exception as e:
                logger.error(f"Error al guardar concursos no predecibles: {e}", exc_info=True)
        
        # Finalizar debug
        end_time = datetime.now()
        debug_info["execution"]["end_time"] = end_time.isoformat()
        debug_info["execution"]["duration_seconds"] = (
            end_time - start_time
        ).total_seconds()
        
        debug_info["stats"]["predictions_saved"] = len(predictions_to_save)
        debug_info["stats"]["unpredictable_saved"] = len(unpredictable_to_save)
        
        # VALIDACIÓN CRÍTICA: Verificar que todos los concursos disponibles fueron procesados
        # OBLIGATORIO: predicciones + no predecibles = total disponible
        total_disponibles = len(concursos_con_versiones_previas)
        total_procesados = len(predictions_to_save) + len(unpredictable_to_save)
        total_analizados = debug_info["predictions"]["total_analyzed"]
        
        # Verificar que la suma sea correcta
        if total_procesados != total_disponibles:
            error_msg = (
                f"❌ ERROR DE INTEGRIDAD: La suma de predicciones ({len(predictions_to_save)}) + "
                f"no predecibles ({len(unpredictable_to_save)}) = {total_procesados} "
                f"NO coincide con el total de concursos disponibles ({total_disponibles}). "
                f"Total analizados en batches: {total_analizados}. "
                f"Faltan {total_disponibles - total_procesados} concursos sin procesar. "
                f"Esto indica un error en el flujo de procesamiento."
            )
            logger.error(error_msg)
            debug_info["execution"]["validation_error"] = {
                "message": error_msg,
                "total_disponibles": total_disponibles,
                "total_procesados": total_procesados,
                "predictions": len(predictions_to_save),
                "unpredictable": len(unpredictable_to_save),
                "total_analizados": total_analizados,
                "missing": total_disponibles - total_procesados
            }
            # Lanzar excepción para que el usuario sepa que hay un problema
            raise ValueError(error_msg)
        else:
            logger.info(
                f"✅ Validación exitosa: {len(predictions_to_save)} predicciones + "
                f"{len(unpredictable_to_save)} no predecibles = {total_procesados} "
                f"(total disponible: {total_disponibles})"
            )
            debug_info["execution"]["validation_passed"] = True
            debug_info["execution"]["validation_summary"] = {
                "total_disponibles": total_disponibles,
                "predictions": len(predictions_to_save),
                "unpredictable": len(unpredictable_to_save),
                "total_procesados": total_procesados
            }
        
        # Guardar archivo de debug
        try:
            debug_file_path = save_debug_info_predictions(debug_info)
            logger.info(f"🐛 Archivo de debug de predicciones generado: {debug_file_path}")
        except Exception as e:
            logger.error(f"Error al guardar archivo de debug: {e}", exc_info=True)
        
        return {
            "predictions": predictions_to_save,
            "debug_info": debug_info,
            "stats": debug_info["stats"]
        }

    def _predict_centro_estudios(
        self,
        concurso: Dict[str, Any],
        previous_concursos: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Predicción determinística para FONIDE (centroestudios.mineduc.cl):
        asume anualidad y proyecta la misma fecha del año siguiente.
        """
        base_date_str = concurso.get("fecha_apertura") or concurso.get("fecha_cierre")
        if not base_date_str and previous_concursos:
            prev = previous_concursos[0]
            base_date_str = prev.get("fecha_apertura") or prev.get("fecha_cierre")
        base_date = parse_date(base_date_str) if base_date_str else None
        if not base_date:
            return None
        target = base_date.replace(year=base_date.year + 1)
        if target.date() <= datetime.now().date():
            target = target.replace(year=target.year + 1)
        fecha_predicha = target.strftime("%Y-%m-%d")
        justificacion = (
            "FONIDE es anual. Se proyecta la próxima convocatoria en la misma fecha del año siguiente, "
            f"tomando como referencia la fecha conocida ({base_date.strftime('%Y-%m-%d')})."
        )
        return {
            "concurso_nombre": concurso.get("nombre", ""),
            "concurso_url": concurso.get("url", ""),
            "fecha_predicha": fecha_predicha,
            "justificacion": justificacion,
            "predicted_at": datetime.now().isoformat(),
            "source": "previous_concursos",
            "previous_concursos": previous_concursos,
        }
    
    
    def _apply_filters(
        self,
        concursos: List[Dict[str, Any]],
        filters: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Aplica filtros a una lista de concursos.
        
        Args:
            concursos: Lista de concursos
            filters: Diccionario con filtros:
                - estado: "Cerrado", "Abierto", None
                - subdireccion: str o None
                - search_term: str o None
                
        Returns:
            Lista filtrada de concursos
        """
        filtered = concursos.copy()
        
        # Filtro por estado
        if filters.get("estado"):
            estado_filter = filters["estado"]
            filtered = [c for c in filtered if c.get("estado") == estado_filter]
        
        # Filtro por subdirección
        if filters.get("subdireccion"):
            subdireccion_filter = filters["subdireccion"].lower()
            filtered = [
                c for c in filtered
                if c.get("subdireccion", "").lower() == subdireccion_filter
            ]
        
        # Filtro por término de búsqueda
        if filters.get("search_term"):
            search_term = filters["search_term"].lower()
            filtered = [
                c for c in filtered
                if search_term in c.get("nombre", "").lower()
            ]
        
        return filtered
    
    async def generate_prediction_for_concurso(
        self,
        concurso: Dict[str, Any],
        status_callback: Optional[Callable[[str], None]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Genera una predicción para un concurso individual.
        
        Args:
            concurso: Diccionario con información del concurso
            status_callback: Función para reportar el estado del proceso
            
        Returns:
            Diccionario con la predicción generada o None si falla
        """
        from utils.file_manager import save_debug_info_individual_prediction
        
        start_time = datetime.now()
        concurso_url = concurso.get("url")
        debug_info = {
            "start_time": start_time.isoformat(),
            "concurso": concurso,
            "scraping": {"success": False, "url": concurso_url},
            "previous_concursos": {"extracted_count": 0, "items": []},
            "prediction": {"success": False, "filtered": False}
        }
        
        if not concurso_url:
            debug_info["scraping"]["error"] = "URL no disponible"
            debug_info["end_time"] = datetime.now().isoformat()
            debug_info["duration_seconds"] = (datetime.now() - start_time).total_seconds()
            save_debug_info_individual_prediction(debug_info)
            return None
        
        # Determinar sitio desde la URL
        from urllib.parse import urlparse
        parsed_url = urlparse(concurso_url)
        site = parsed_url.netloc.replace("www.", "")
        
        # Obtener previous_concursos del historial (siempre deben estar guardados durante el scraping)
        previous_concursos = []
        history = self.history_manager.load_history(site)
        if history:
            # Crear índice por URL para búsqueda eficiente
            history_index_by_url = {
                hc.get("url"): hc 
                for hc in history.get("concursos", []) 
                if hc.get("url")
            }
            
            hist_concurso = history_index_by_url.get(concurso_url)
            if hist_concurso:
                previous_concursos = hist_concurso.get("previous_concursos", [])
                if previous_concursos:
                    logger.info(
                        f"✅ Usando {len(previous_concursos)} concursos anteriores del historial para {concurso.get('nombre', 'N/A')}"
                    )
                    debug_info["previous_concursos"]["extracted_count"] = len(previous_concursos)
                    debug_info["previous_concursos"]["items"] = previous_concursos
                    debug_info["previous_concursos"]["source"] = "history"
                    debug_info["scraping"]["success"] = True
                else:
                    logger.debug(
                        f"ℹ️ No hay concursos anteriores en el historial para {concurso.get('nombre', 'N/A')} (no tiene versiones anteriores)"
                    )
                    debug_info["scraping"]["success"] = True  # El scraping ya se hizo, solo que no había concursos anteriores
            else:
                logger.warning(
                    f"⚠️ Concurso '{concurso.get('nombre', 'N/A')}' ({concurso_url}) no encontrado en historial. "
                    f"Puede que no haya sido scrapeado aún."
                )
                debug_info["scraping"]["error"] = "Concurso no encontrado en historial"
        
        if not previous_concursos:
            debug_info["prediction"]["filtered"] = True
            debug_info["prediction"]["filter_reason"] = "no_previous_concursos"
            debug_info["end_time"] = datetime.now().isoformat()
            debug_info["duration_seconds"] = (datetime.now() - start_time).total_seconds()
            save_debug_info_individual_prediction(debug_info)
            if status_callback:
                status_callback(f"ℹ️ No se encontraron 'Concursos anteriores' para {concurso.get('nombre', 'N/A')}")
            return None
        
        # Generar predicción
        try:
            previous_concursos_info = format_previous_concursos_for_prediction(previous_concursos)
            
            concurso_dict = {
                "nombre": concurso.get("nombre", ""),
                "url": concurso_url,
                "fecha_apertura": concurso.get("fecha_apertura") or "",
                "fecha_cierre": concurso.get("fecha_cierre") or "",
                "organismo": concurso.get("organismo", ""),
                "descripcion": ""
            }
            
            if status_callback:
                status_callback(f"🔮 Generando predicción para {concurso.get('nombre', 'N/A')}...")
            
            prediccion = self.predictor.predict_from_previous_concursos(
                concurso_dict,
                previous_concursos_info
            )
            
            if prediccion and prediccion.es_mismo_concurso and prediccion.fecha_predicha:
                parsed_date = parse_date(prediccion.fecha_predicha)
                now = datetime.now()
                
                if parsed_date and parsed_date.date() > now.date() and (parsed_date.year - now.year) <= 1:
                    prediction_entry = {
                        "concurso_nombre": concurso.get("nombre", ""),
                        "concurso_url": concurso_url,
                        "fecha_predicha": prediccion.fecha_predicha,
                        "justificacion": prediccion.justificacion,
                        "predicted_at": datetime.now().isoformat(),
                        "source": "previous_concursos",
                        "previous_concursos": previous_concursos  # Guardar información de concursos anteriores
                    }
                    
                    debug_info["prediction"]["success"] = True
                    debug_info["prediction"]["fecha_predicha"] = prediccion.fecha_predicha
                    debug_info["prediction"]["justificacion"] = prediccion.justificacion
                    
                    if status_callback:
                        status_callback(f"✅ Predicción generada: {prediccion.fecha_predicha}")
                    
                    debug_info["end_time"] = datetime.now().isoformat()
                    debug_info["duration_seconds"] = (datetime.now() - start_time).total_seconds()
                    save_debug_info_individual_prediction(debug_info)
                    
                    return prediction_entry
                else:
                    debug_info["prediction"]["filtered"] = True
                    if parsed_date:
                        if parsed_date.date() <= now.date():
                            debug_info["prediction"]["filter_reason"] = "past_date"
                        else:
                            debug_info["prediction"]["filter_reason"] = "too_far_in_future"
                    else:
                        debug_info["prediction"]["filter_reason"] = "unparseable_date"
                    debug_info["prediction"]["fecha_predicha"] = prediccion.fecha_predicha
                    debug_info["end_time"] = datetime.now().isoformat()
                    debug_info["duration_seconds"] = (datetime.now() - start_time).total_seconds()
                    save_debug_info_individual_prediction(debug_info)
                    if status_callback:
                        status_callback(f"ℹ️ Predicción descartada (fecha no válida)")
                    return None
            else:
                debug_info["prediction"]["filtered"] = True
                debug_info["prediction"]["filter_reason"] = "no_prediction_from_llm"
                debug_info["end_time"] = datetime.now().isoformat()
                debug_info["duration_seconds"] = (datetime.now() - start_time).total_seconds()
                save_debug_info_individual_prediction(debug_info)
                if status_callback:
                    status_callback(f"ℹ️ No se pudo generar predicción")
                return None
                
        except Exception as e:
            debug_info["prediction"]["error"] = str(e)
            debug_info["end_time"] = datetime.now().isoformat()
            debug_info["duration_seconds"] = (datetime.now() - start_time).total_seconds()
            save_debug_info_individual_prediction(debug_info)
            logger.error(f"Error al generar predicción para {concurso.get('nombre')}: {e}", exc_info=True)
            if status_callback:
                status_callback(f"❌ Error: {str(e)}")
            return None

