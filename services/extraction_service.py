"""
Servicio de extracción de concursos

Orquesta el proceso completo:
1. Scraping de URLs (con paginación si aplica)
2. Limpieza y procesamiento de contenido
3. Agrupación en batches
4. Extracción con LLM
5. Validación y normalización de datos
"""

import json
import logging
import asyncio
import traceback
from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime

from crawler import WebScraper
from crawler.markdown_processor import clean_markdown_for_llm
from crawler.batch_processor import create_batches
from crawler.strategies import get_strategy_for_url
from crawler.strategies.centro_estudios_strategy import CentroEstudiosStrategy
from llm.extractors.llm_extractor import LLMExtractor
from models import Concurso
from config import CRAWLER_CONFIG, EXTRACTION_CONFIG, GEMINI_CONFIG
from utils.history_manager import HistoryManager
from utils.file_manager import save_page_cache, load_page_cache, save_debug_info_scraping, save_results
from utils.lock_manager import site_operation_lock
# NOTA: extract_previous_concursos_from_html ahora se usa a través de estrategias
# Se mantiene comentado para referencia, pero ya no se usa directamente
# from utils.anid_previous_concursos import extract_previous_concursos_from_html
from utils.date_parser import parse_date, is_past_date
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# NOTA: KNOWN_SUBDIRECCIONES se ha movido a crawler/strategies/anid_strategy.py
# Se mantiene aquí solo para compatibilidad temporal durante la migración


class ExtractionService:
    """
    Servicio principal para extraer concursos de URLs.
    
    Separa la lógica de negocio de la UI y proporciona una interfaz
    clara para el procesamiento de URLs.
    """
    
    def __init__(
        self,
        api_key_manager,
        model_name: Optional[str] = None,
        crawler_config: Optional[Dict[str, Any]] = None,
        extraction_config: Optional[Dict[str, Any]] = None
    ):
        """
        Inicializa el servicio de extracción.
        
        Args:
            api_key_manager: Gestor de API keys para el LLM
            model_name: Nombre del modelo LLM a usar (opcional)
            crawler_config: Configuración del crawler (opcional)
            extraction_config: Configuración de extracción (opcional)
        """
        self.api_key_manager = api_key_manager
        self.model_name = model_name
        self.crawler_config = crawler_config or CRAWLER_CONFIG
        self.extraction_config = extraction_config or EXTRACTION_CONFIG
        
        # Inicializar componentes
        self.scraper = WebScraper(config=self.crawler_config)
        self.extractor = LLMExtractor(
            api_key_manager=api_key_manager,
            model_name=model_name or GEMINI_CONFIG.get("model"),
            config=self.extraction_config
        )
        self.history_manager = HistoryManager()
    
    def extract_from_urls(
        self,
        urls: List[str],
        follow_pagination: bool = True,
        max_pages: int = 10,
        progress_callback: Optional[callable] = None,
        status_callback: Optional[callable] = None,
        should_stop_callback: Optional[callable] = None
    ) -> List[Concurso]:
        """
        Wrapper resiliente: aplica lock por sitio/operación antes de extraer.
        """
        site_for_lock = None
        if urls:
            try:
                parsed = urlparse(urls[0])
                site_for_lock = (parsed.netloc or parsed.path.split('/')[0]).replace("www.", "")
            except Exception:
                site_for_lock = None
        # Si no podemos determinar el sitio, seguimos sin lock para no bloquear funcionalidad
        if site_for_lock:
            with site_operation_lock(site_for_lock, "scrape", timeout_seconds=60):
                return self._extract_from_urls_impl(
                    urls,
                    follow_pagination=follow_pagination,
                    max_pages=max_pages,
                    progress_callback=progress_callback,
                    status_callback=status_callback,
                    should_stop_callback=should_stop_callback,
                )
        return self._extract_from_urls_impl(
            urls,
            follow_pagination=follow_pagination,
            max_pages=max_pages,
            progress_callback=progress_callback,
            status_callback=status_callback,
            should_stop_callback=should_stop_callback,
        )

    def _extract_from_urls_impl(
        self,
        urls: List[str],
        follow_pagination: bool = True,
        max_pages: int = 10,
        progress_callback: Optional[callable] = None,
        status_callback: Optional[callable] = None,
        should_stop_callback: Optional[callable] = None
    ) -> List[Concurso]:
        """
        Extrae concursos de una lista de URLs.
        
        Args:
            urls: Lista de URLs a procesar
            follow_pagination: Si True, detecta y procesa páginas adicionales
            max_pages: Número máximo de páginas a procesar cuando hay paginación
            progress_callback: Función callback para reportar progreso (0.0 a 1.0)
            status_callback: Función callback para reportar estado (mensaje string)
            
        Returns:
            Lista de concursos extraídos y validados
        """
        primary_strategy = get_strategy_for_url(urls[0]) if urls else None
        # Inicializar información de debug
        start_time = datetime.now()
        debug_info = {
            "execution": {
                "start_time": start_time.isoformat(),
                "urls": urls,
                "follow_pagination": follow_pagination,
                "max_pages": max_pages,
                "model_name": self.model_name,
                "config": {
                    "crawler": self.crawler_config,
                    "extraction": self.extraction_config
                }
            },
            "scraping": {
                "pages_scraped": 0,
                "pages_failed": 0,
                "total_html_size": 0,
                "total_markdown_size": 0,
                "total_markdown_cleaned_size": 0,
                "errors": [],
                # Métricas adicionales para auditoría fina
                # - concursos_html_detectados_total: cuántos items de concurso se detectaron en el HTML
                #   (por ejemplo, .jet-listing-grid__item en sitios con JetEngine)
                # - concursos_html_por_pagina: lista de dicts con url de página y count detectado
                "concursos_html_detectados_total": 0,
                "concursos_html_por_pagina": []
            },
            "llm": {
                "batches_processed": 0,
                "total_calls": 0,
                "total_failed": 0,
                "api_keys_used": [],
                "errors": [],
                "raw_files": []
            },
            "extraction": {
                "concursos_found": 0,
                "concursos_after_dedup": 0,
                "duplicates_removed": 0
            },
            "warnings": [],
            "timeouts": {
                "api_timeout": self.extraction_config.get("api_timeout", 60),
                "max_time_per_batch": self.extraction_config.get("max_time_per_batch", 300),
                "max_total_time": self.extraction_config.get("max_total_time"),
                "continue_on_error": self.extraction_config.get("continue_on_error", True),
                "max_consecutive_failures": self.extraction_config.get("max_consecutive_failures", 5)
            }
        }
        
        all_concursos: List[Concurso] = []
        all_page_contents: List[Dict[str, Any]] = []
        # Historial que usaremos luego para predicciones; se actualizará si escribimos nuevo historial
        history_for_predictions = None
        
        # Fase 0: Cargar historial del sitio (si hay URLs)
        site = None
        history_data = None
        if urls:
            # Determinar sitio desde la primera URL
            from urllib.parse import urlparse
            parsed = urlparse(urls[0])
            site = parsed.netloc.replace("www.", "") if parsed.netloc else parsed.path.split('/')[0]
            
            if status_callback:
                status_callback(f"📚 Cargando historial de {site}...")
            
            history_data = self.history_manager.load_history(site)
            existing_count = len(history_data.get("concursos", []))
            if existing_count > 0:
                logger.info(f"📚 Historial encontrado para {site}: {existing_count} concursos históricos")
                debug_info["history"] = {
                    "site": site,
                    "existing_concursos": existing_count
                }
        
        # Fase 1: Scraping de todas las URLs
        total_urls = len(urls)
        for i, url in enumerate(urls):
            # Verificar si debe detenerse
            if should_stop_callback and should_stop_callback():
                logger.info("Proceso detenido por el usuario durante scraping")
                if status_callback:
                    status_callback("⚠️ Proceso detenido por el usuario")
                break
            
            try:
                if status_callback:
                    status_callback(f"Scrapeando {i+1}/{total_urls}: {url}")
                
                page_results = self._scrape_url(url, follow_pagination, max_pages)
                
                # Limpiar y preparar markdown de todas las páginas
                for page_result in page_results:
                    if not page_result.get("success") or not page_result.get("markdown"):
                        debug_info["scraping"]["pages_failed"] += 1
                        debug_info["warnings"].append({
                            "type": "scraping_failed",
                            "url": url,
                            "message": f"No se pudo procesar página de {url}"
                        })
                        logger.warning(f"No se pudo procesar página de {url}")
                        continue
                    
                    markdown = page_result["markdown"]
                    cleaned_markdown = clean_markdown_for_llm(markdown)
                    page_result["markdown_cleaned"] = cleaned_markdown
                    
                    # Extraer URLs de concursos desde el HTML programáticamente
                    from utils.url_extractor import extract_concurso_urls_from_html
                    page_html = page_result.get("html", "")
                    page_url = page_result.get("url", url)
                    concurso_urls_map = extract_concurso_urls_from_html(page_html, page_url)
                    page_result["concurso_urls_map"] = concurso_urls_map
                    
                    # Actualizar contadores de concursos detectados en HTML
                    concursos_html_count = len(concurso_urls_map)
                    debug_info["scraping"]["concursos_html_detectados_total"] += concursos_html_count
                    debug_info["scraping"]["concursos_html_por_pagina"].append({
                        "page_url": page_url,
                        "concursos_html_detectados": concursos_html_count
                    })
                    
                    all_page_contents.append(page_result)
                    
                    # Actualizar stats de scraping
                    debug_info["scraping"]["pages_scraped"] += 1
                    debug_info["scraping"]["total_html_size"] += len(page_result.get("html", ""))
                    debug_info["scraping"]["total_markdown_size"] += len(markdown)
                    debug_info["scraping"]["total_markdown_cleaned_size"] += len(cleaned_markdown)
                
                # Reportar progreso
                if progress_callback:
                    progress_callback((i + 1) / total_urls)
                    
            except Exception as e:
                error_msg = str(e)
                debug_info["scraping"]["errors"].append({
                    "url": url,
                    "error": error_msg,
                    "type": type(e).__name__
                })
                logger.error(f"Error al procesar URL {url}: {e}", exc_info=True)
                if status_callback:
                    status_callback(f"Error al procesar {url}: {e}")
        
        # Verificar si debe detenerse después del scraping
        if should_stop_callback and should_stop_callback():
            logger.info("Proceso detenido por el usuario después del scraping")
            if status_callback:
                status_callback("⚠️ Proceso detenido. Retornando resultados parciales...")
            return all_concursos

        # Atajo de deduplicación: si todas las URLs detectadas ya existen en historial,
        # evitar pasar por LLM/enriquecimiento y salir temprano.
        if site and history_data:
            history_urls = {
                (c.get("url") or "").strip()
                for c in history_data.get("concursos", [])
                if c.get("url")
            }
            detected_urls = set()
            for page_result in all_page_contents:
                detected_urls.update(page_result.get("concurso_urls_map", {}).keys())

            if detected_urls and detected_urls.issubset(history_urls):
                logger.info(
                    f"🔁 Todas las {len(detected_urls)} URLs detectadas ya existen en historial ({site}). "
                    "Saltando extracción/enriquecimiento y evitando costo de LLM."
                )
                if status_callback:
                    status_callback(
                        f"🔁 {len(detected_urls)} concursos ya estaban en el historial. "
                        "No se procesan nuevamente."
                    )
                debug_info.setdefault("history", {})
                debug_info["history"]["existing_concursos"] = len(detected_urls)
                debug_info["history"]["new_concursos"] = 0
                debug_info["extraction"]["concursos_found"] = len(detected_urls)
                debug_info["extraction"]["concursos_after_dedup"] = len(detected_urls)
                debug_info["extraction"]["duplicates_removed"] = len(detected_urls)
                # Guardar debug mínimo
                try:
                    debug_file_path = save_debug_info_scraping(debug_info)
                    logger.info(f"🐛 Archivo de debug generado (atajo dedup): {debug_file_path}")
                except Exception as e:
                    logger.error(f"Error al guardar debug (atajo dedup): {e}", exc_info=True)
                return []
        
        # Fase 2: Agrupación en batches
        if status_callback:
            status_callback("Agrupando contenido para el LLM...")
        
        # Atajo determinista para Centro Estudios MINEDUC (FONIDE): sin LLM
        if isinstance(primary_strategy, CentroEstudiosStrategy):
            if not all_page_contents:
                logger.warning("No hay contenido para centroestudios.mineduc.cl")
                return []
            page = all_page_contents[0]
            md = page.get("markdown") or page.get("content") or ""
            html = page.get("html", "")
            page_url = page.get("url", urls[0] if urls else "")
            import re

            def _find_date(patterns, text):
                months = {
                    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
                    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
                    "septiembre": "09", "setiembre": "09", "octubre": "10",
                    "noviembre": "11", "diciembre": "12",
                }
                for pat in patterns:
                    m = re.search(pat, text, re.IGNORECASE)
                    if m:
                        dia = int(m.group(1))
                        mes_str = m.group(2).lower()
                        anio = int(m.group(3))
                        mes = months.get(mes_str)
                        if mes:
                            return f"{anio:04d}-{mes}-{dia:02d}"
                return None

            # Bloque focal: Convocatoria actual
            block_match = re.search(
                r"###\s*Convocatoria actual.*?(Bases de postulación|Bases de postulaci[oó]n)",
                md,
                flags=re.IGNORECASE | re.DOTALL,
            )
            focal_block = block_match.group(0) if block_match else ""

            fecha_cierre = _find_date(
                [r"postulaciones.*?hasta el\s+(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})"],
                focal_block or md,
            )
            fecha_apertura = _find_date(
                [r"consultas.*?hasta el\s+(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})"],
                focal_block or md,
            )

            # Nombre dinámico por si cambia FONIDE 16/17/...
            nombre_match = re.search(r"Convocatoria actual\s*\(([^)]+)\)", md, re.IGNORECASE)
            nombre = (
                f"Fondo de Investigación y Desarrollo en Educación ({nombre_match.group(1)})"
                if nombre_match
                else "Fondo de Investigación y Desarrollo en Educación (FONIDE)"
            )

            concurso = Concurso(
                nombre=nombre,
                url=page_url,
                organismo="Centro de Estudios MINEDUC",
                fecha_apertura=fecha_apertura,
                fecha_cierre=fecha_cierre,
                estado="Cerrado",
                descripcion="Concurso FONIDE del Centro de Estudios MINEDUC; fecha límite indicada en la sección Documentos FONIDE 16.",
                subdireccion=None,
                financiamiento=None,
            )
            save_page_cache(site=primary_strategy.site_name, url=page_url, html=html, markdown=md)
            enriched_content = {page_url: {"markdown": md, "html": html}}
            history_update = self.history_manager.update_history(
                site=primary_strategy.site_name,
                concursos=[concurso],
                enriched_content=enriched_content
            )
            self.history_manager.save_history(primary_strategy.site_name, history_update)
            debug_info["extraction"]["concursos_found"] = 1
            debug_info["extraction"]["concursos_after_dedup"] = 1
            debug_info["scraping"]["pages_scraped"] = len(all_page_contents)
            debug_info["scraping"]["pages_failed"] = debug_info["scraping"].get("pages_failed", 0)
            debug_info["execution"]["end_time"] = datetime.now().isoformat()
            debug_info["execution"]["duration_seconds"] = (
                datetime.now() - start_time
            ).total_seconds()
            save_debug_info_scraping(debug_info)
            logger.info("✅ Extracción determinista completada para Centro Estudios MINEDUC (FONIDE) sin LLM.")
            return [concurso]

        batch_size = self.extraction_config.get("batch_size", 500000)
        batches = create_batches(all_page_contents, batch_size=batch_size)
        logger.info(f"Creadas {len(batches)} batches para {len(all_page_contents)} páginas")
        
        # Fase 3: Extracción con LLM
        total_batches = len(batches)
        consecutive_failures = 0
        max_consecutive_failures = self.extraction_config.get("max_consecutive_failures", 5)
        max_time_per_batch = self.extraction_config.get("max_time_per_batch", 300)
        max_total_time = self.extraction_config.get("max_total_time")
        continue_on_error = self.extraction_config.get("continue_on_error", True)
        execution_start_time = datetime.now()
        
        for batch_idx, (pages_in_batch, combined_markdown) in enumerate(batches):
            # Verificar si debe detenerse
            if should_stop_callback and should_stop_callback():
                logger.info("Proceso detenido por el usuario durante extracción LLM")
                if status_callback:
                    status_callback("⚠️ Proceso detenido. Retornando resultados parciales...")
                break
            
            # Verificar tiempo total transcurrido
            if max_total_time:
                elapsed_time = (datetime.now() - execution_start_time).total_seconds()
                if elapsed_time > max_total_time:
                    logger.warning(f"⏱️ Tiempo máximo de ejecución ({max_total_time}s) alcanzado. Deteniendo procesamiento.")
                    if status_callback:
                        status_callback(f"⏱️ Tiempo máximo alcanzado. Retornando resultados parciales...")
                    debug_info["warnings"].append({
                        "type": "max_time_reached",
                        "message": f"Tiempo máximo de {max_total_time}s alcanzado",
                        "batches_processed": batch_idx,
                        "total_batches": total_batches
                    })
                    break
            
            # Verificar fallos consecutivos
            if consecutive_failures >= max_consecutive_failures:
                logger.error(f"❌ Máximo de fallos consecutivos ({max_consecutive_failures}) alcanzado. Abortando procesamiento.")
                if status_callback:
                    status_callback(f"❌ Demasiados fallos consecutivos. Abortando...")
                debug_info["warnings"].append({
                    "type": "max_consecutive_failures",
                    "message": f"Máximo de {max_consecutive_failures} fallos consecutivos alcanzado",
                    "batches_processed": batch_idx,
                    "total_batches": total_batches
                })
                break
            
            # Extraer URLs del batch
            urls_in_batch = [page.get("url", "unknown") for page in pages_in_batch]
            
            # Combinar todos los mapas de URLs de concursos de las páginas en el batch
            # Estructura de batch_concurso_urls_map: {url: nombre_html}
            batch_concurso_urls_map: Dict[str, str] = {}
            for page in pages_in_batch:
                page_urls_map = page.get("concurso_urls_map", {})
                # No queremos perder información por colisiones de clave;
                # si una URL ya estaba, la dejamos tal cual.
                for url_key, nombre_val in page_urls_map.items():
                    if url_key not in batch_concurso_urls_map:
                        batch_concurso_urls_map[url_key] = nombre_val
            
            if status_callback:
                status_callback(
                    f"Extrayendo batch {batch_idx+1}/{total_batches} "
                    f"(URLs: {len(urls_in_batch)}, "
                    f"chars: {len(combined_markdown):,})"
                )
            
            logger.info(
                f"Enviando batch {batch_idx+1}/{total_batches} al LLM. "
                f"URLs: {urls_in_batch}, "
                f"Tamaño: {len(combined_markdown):,} chars, "
                f"URLs de concursos extraídas: {len(batch_concurso_urls_map)}"
            )
            
            # Extraer concursos del batch con verificación de tiempo
            batch_start_time = datetime.now()
            try:
                # El timeout real está en requests.post (60s por defecto)
                # Aquí solo verificamos el tiempo total transcurrido para logging
                batch_concursos, raw_batch_data = self.extractor.extract_from_batch(
                    combined_markdown,
                    urls_in_batch
                )
                
                # Asignar URLs correctas programáticamente (refuerzo sobre lo que venga del LLM)
                from utils.url_extractor import match_concurso_to_url
                default_batch_url = urls_in_batch[0] if urls_in_batch else "unknown"
                
                for concurso in batch_concursos:
                    # Si la URL viene vacía o es genérica/listado, intentar corregirla;
                    # en ningún caso usaremos la URL del listado como "URL del concurso".
                    if (not concurso.url or
                        concurso.url == default_batch_url or 
                        str(concurso.url).rstrip('/').endswith('/concursos')):
                        
                        correct_url = match_concurso_to_url(
                            concurso.nombre,
                            batch_concurso_urls_map,
                            default_batch_url
                        )
                        # Solo aceptar la URL si es distinta del listado y parece un concurso real
                        if (correct_url and
                            correct_url != default_batch_url and
                            not str(correct_url).rstrip('/').endswith('/concursos')):
                            logger.debug(f"🔗 URL asignada para '{concurso.nombre}': {correct_url}")
                            concurso.url = correct_url
                        else:
                            # No tenemos una URL confiable para este concurso
                            logger.warning(
                                f"⚠️ No se pudo determinar una URL específica para el concurso "
                                f"'{concurso.nombre}'. La URL se dejará vacía y se registrará en debug."
                            )
                            concurso.url = None

                # Fallback: garantizar que cada URL de concurso detectada en HTML tenga
                # al menos un objeto Concurso, incluso si el LLM lo omitió.
                from models import Concurso as ConcursoModel
                urls_con_concurso = {c.url for c in batch_concursos if getattr(c, "url", None)}
                
                for url_html, nombre_html in batch_concurso_urls_map.items():
                    if url_html not in urls_con_concurso:
                        # Crear un concurso mínimo basado solo en HTML
                        from urllib.parse import urlparse
                        domain = urlparse(url_html).netloc
                        # Obtener organismo desde la estrategia
                        strategy = get_strategy_for_url(f"https://{domain}")
                        organismo = strategy.get_organismo_name(f"https://{domain}")
                        
                        try:
                            fallback_concurso = ConcursoModel(
                                nombre=nombre_html or "Concurso sin título",
                                fecha_apertura=None,
                                fecha_cierre=None,
                                organismo=organismo,
                                financiamiento=None,
                                url=url_html,
                                estado=None,
                                fecha_apertura_original=None,
                                descripcion=None,
                                predicted_opening=None,
                                subdireccion=None,
                                extraido_en=datetime.now().isoformat(),
                                fuente=domain or None,
                            )
                            batch_concursos.append(fallback_concurso)
                            urls_con_concurso.add(url_html)
                            
                            warning_entry = {
                                "type": "llm_missed_concurso",
                                "batch": batch_idx + 1,
                                "concurso_nombre": nombre_html,
                                "concurso_url": url_html,
                                "message": "Concurso detectado en HTML pero no devuelto por el LLM. Creado concurso mínimo desde HTML."
                            }
                            debug_info["warnings"].append(warning_entry)
                            logger.warning(
                                f"⚠️ [llm_missed_concurso] Concurso '{nombre_html}' ({url_html}) "
                                f"detectado en HTML pero no devuelto por el LLM. Creado desde HTML."
                            )
                        except Exception as e:
                            logger.error(
                                f"Error al crear concurso de fallback desde HTML para URL {url_html}: {e}",
                                exc_info=True
                            )
                
                # Validar que se extrajeron suficientes concursos
                num_pages_in_batch = len(urls_in_batch)
                # Valor típico: muchos sitios tienen ~6 concursos por página, excepto la última que puede tener menos
                # Este valor es una estimación genérica y puede variar según el sitio
                expected_per_page = 6
                expected_typical = num_pages_in_batch * expected_per_page  # Típico: 6 concursos por página
                concursos_found = len(batch_concursos)
                concursos_per_page = concursos_found / num_pages_in_batch if num_pages_in_batch > 0 else 0
                
                # Detectar posible pérdida de datos
                # Criterios:
                # 1. Menos de 4 concursos por página (muy sospechoso, deberían ser 6)
                # 2. Menos de 5 concursos por página Y no es el último batch (posible pérdida)
                # 3. Total de concursos significativamente menor al esperado
                is_last_batch = (batch_idx + 1) == total_batches
                threshold_suspicious = 4  # Menos de 4 por página es muy sospechoso
                threshold_warning = 5  # Menos de 5 por página es una advertencia (excepto último batch)
                
                possible_data_loss = False
                loss_severity = None
                
                if concursos_per_page < threshold_suspicious:
                    # Muy sospechoso: menos de 4 por página
                    possible_data_loss = True
                    loss_severity = "high"
                    logger.warning(
                        f"🚨 Batch {batch_idx+1}: POSIBLE PÉRDIDA DE DATOS DETECTADA. "
                        f"Se extrajeron {concursos_found} concursos ({concursos_per_page:.1f} por página), "
                        f"pero se esperaban aproximadamente {expected_typical} ({expected_per_page} por página). "
                        f"Intentando re-extracción automática con modelo más potente..."
                    )
                elif concursos_per_page < threshold_warning and not is_last_batch:
                    # Advertencia: menos de 5 por página y no es el último batch
                    possible_data_loss = True
                    loss_severity = "medium"
                    logger.warning(
                        f"⚠️ Batch {batch_idx+1}: Posible pérdida menor detectada. "
                        f"Se extrajeron {concursos_found} concursos ({concursos_per_page:.1f} por página), "
                        f"esperados {expected_typical} ({expected_per_page} por página). "
                        f"Intentando re-extracción automática..."
                    )
                elif concursos_found > expected_typical:
                    logger.info(
                        f"ℹ️ Batch {batch_idx+1}: Se extrajeron {concursos_found} concursos "
                        f"({concursos_per_page:.1f} por página, más de los {expected_per_page} típicos). "
                        f"Esto puede ser normal si hay páginas con más concursos."
                    )
                
                # Re-extracción automática si se detecta pérdida
                if possible_data_loss:
                    if status_callback:
                        status_callback(
                            f"🔄 Re-extrayendo batch {batch_idx+1} con modelo más potente "
                            f"(pérdida detectada: {loss_severity})..."
                        )
                    
                    re_extracted_concursos = self._re_extract_batch_with_powerful_model(
                        combined_markdown,
                        urls_in_batch,
                        batch_idx + 1
                    )
                    
                    if re_extracted_concursos and len(re_extracted_concursos) > concursos_found:
                        improvement = len(re_extracted_concursos) - concursos_found
                        logger.info(
                            f"✅ Re-extracción exitosa: Se recuperaron {improvement} concursos adicionales "
                            f"({concursos_found} → {len(re_extracted_concursos)})"
                        )
                        batch_concursos = re_extracted_concursos
                        debug_info["llm"]["auto_recovery"] = debug_info["llm"].get("auto_recovery", [])
                        debug_info["llm"]["auto_recovery"].append({
                            "batch": batch_idx + 1,
                            "original_count": concursos_found,
                            "recovered_count": len(re_extracted_concursos),
                            "improvement": improvement,
                            "severity": loss_severity
                        })
                    else:
                        logger.warning(
                            f"⚠️ Re-extracción no mejoró los resultados "
                            f"(original: {concursos_found}, re-extraído: {len(re_extracted_concursos) if re_extracted_concursos else 0})"
                        )
                        debug_info["warnings"].append({
                            "type": "low_concurso_count",
                            "batch": batch_idx + 1,
                            "concursos_found": concursos_found,
                            "concursos_per_page": concursos_per_page,
                            "expected_typical": expected_typical,
                            "expected_per_page": expected_per_page,
                            "pages_in_batch": num_pages_in_batch,
                            "re_extraction_attempted": True,
                            "re_extraction_improved": False,
                            "urls": urls_in_batch[:3]  # Primeras 3 URLs para referencia
                        })
                else:
                    # Registrar información normal
                    if concursos_per_page < threshold_warning:
                        debug_info["warnings"].append({
                            "type": "low_concurso_count",
                            "batch": batch_idx + 1,
                            "concursos_found": concursos_found,
                            "concursos_per_page": concursos_per_page,
                            "expected_typical": expected_typical,
                            "expected_per_page": expected_per_page,
                            "pages_in_batch": num_pages_in_batch,
                            "is_last_batch": is_last_batch,
                            "urls": urls_in_batch[:3]
                        })

                # Registrar concursos que aún no tienen URL asignada (incidencia explícita)
                for concurso in batch_concursos:
                    if not getattr(concurso, "url", None):
                        warning_entry = {
                            "type": "missing_concurso_url",
                            "batch": batch_idx + 1,
                            "concurso_nombre": concurso.nombre,
                            "message": "No se pudo determinar URL específica para este concurso.",
                            "urls_batch_context": urls_in_batch[:3]
                        }
                        debug_info["warnings"].append(warning_entry)
                        logger.warning(
                            f"⚠️ [missing_concurso_url] Concurso '{concurso.nombre}' "
                            f"sin URL específica después de todos los intentos."
                        )
                
                all_concursos.extend(batch_concursos)
                debug_info["llm"]["batches_processed"] += 1
                debug_info["extraction"]["concursos_found"] += len(batch_concursos)
                consecutive_failures = 0  # Resetear contador de fallos
                batch_elapsed = (datetime.now() - batch_start_time).total_seconds()
                logger.info(
                    f"✅ Extraídos {len(batch_concursos)} concursos del batch {batch_idx+1} "
                    f"({num_pages_in_batch} páginas) en {batch_elapsed:.1f}s"
                )
                
                # Warning si se acerca al límite
                if batch_elapsed > max_time_per_batch * 0.8:
                    logger.warning(f"⏱️ Batch {batch_idx+1} tomó {batch_elapsed:.1f}s (límite recomendado: {max_time_per_batch}s)")
                
                # Guardar resultados crudos para auditoría
                raw_file_path = self._save_raw_results(batch_idx + 1, raw_batch_data, pages_in_batch, combined_markdown)
                debug_info["llm"]["raw_files"].append(raw_file_path)
                
                # Obtener estadísticas de API key manager
                if hasattr(self.api_key_manager, 'get_total_stats'):
                    api_stats = self.api_key_manager.get_total_stats()
                    debug_info["llm"]["total_calls"] = api_stats.get("total_calls", 0)
                    debug_info["llm"]["total_failed"] = api_stats.get("total_failed", 0)
                
                if hasattr(self.api_key_manager, 'get_current_key'):
                    current_key = self.api_key_manager.get_current_key()
                    if current_key and current_key not in debug_info["llm"]["api_keys_used"]:
                        # Solo mostrar primeros 8 caracteres por seguridad
                        debug_info["llm"]["api_keys_used"].append(current_key[:8] + "..." if current_key else None)
                
                # Capturar errores detallados del extractor si existen
                if hasattr(self.extractor, '_last_error_details') and self.extractor._last_error_details:
                    debug_info["llm"]["errors"].extend(self.extractor._last_error_details)
                    self.extractor._last_error_details = []  # Limpiar después de capturar
                        
            except Exception as e:
                consecutive_failures += 1
                error_msg = str(e)
                batch_elapsed = (datetime.now() - batch_start_time).total_seconds()
                is_timeout = "timeout" in error_msg.lower() or "Timeout" in type(e).__name__
                
                # Log detallado en consola
                logger.error(f"❌ Error al extraer batch {batch_idx+1}/{total_batches}:")
                logger.error(f"   URLs afectadas: {urls_in_batch}")
                logger.error(f"   Tipo de error: {type(e).__name__}")
                logger.error(f"   Mensaje: {error_msg}")
                logger.error(f"   Fallos consecutivos: {consecutive_failures}/{max_consecutive_failures}")
                
                if is_timeout:
                    logger.error(f"   ⏱️ Timeout detectado: El batch tomó {batch_elapsed:.1f}s")
                else:
                    logger.error(f"   Traceback completo:\n{traceback.format_exc()}")
                
                # Capturar error usando helper
                error_details = self._log_and_capture_error(
                    e,
                    f"batch_extraction (batch {batch_idx+1}/{total_batches})",
                    urls_in_batch,
                    debug_info,
                    include_traceback=not is_timeout
                )
                
                # Agregar información adicional específica del batch
                error_details["batch"] = batch_idx + 1
                error_details["consecutive_failures"] = consecutive_failures
                error_details["batch_elapsed_seconds"] = batch_elapsed
                
                # Decidir si continuar o abortar
                if not continue_on_error:
                    logger.error("❌ continue_on_error=False. Abortando procesamiento.")
                    if status_callback:
                        status_callback("❌ Error crítico. Abortando procesamiento...")
                    break
                elif consecutive_failures >= max_consecutive_failures:
                    logger.error(f"❌ Máximo de fallos consecutivos alcanzado. Abortando.")
                    if status_callback:
                        status_callback(f"❌ Demasiados fallos consecutivos. Abortando...")
                    break
                else:
                    logger.warning(f"⚠️ Continuando con siguiente batch a pesar del error...")
                    if status_callback:
                        status_callback(f"⚠️ Error en batch {batch_idx+1}. Continuando...")
        
        # Fase 3.5: Comparar con historial y separar concursos nuevos vs existentes
        new_concursos: List[Concurso] = []
        existing_concursos_from_history: List[Concurso] = []
        existing_keys_set: Set[Tuple[str, str]] = set()
        
        if site and history_data:
            if status_callback:
                status_callback("🔍 Comparando con historial...")
            
            existing_concursos_list, new_concursos_list, history_dict = self.history_manager.find_existing_concursos(
                site, all_concursos
            )
            
            # Convertir concursos existentes del historial a objetos Concurso
            for concurso in existing_concursos_list:
                key = self.history_manager._normalize_concurso_key(concurso)
                existing_keys_set.add(key)
                
                # Obtener datos más recientes del historial
                hist_data = history_dict.get(key, {})
                versions = hist_data.get("versions", [])
                
                if versions:
                    # Usar la versión más reciente del historial
                    latest_version = versions[-1]
                    
                    # Crear objeto Concurso desde historial
                    try:
                        # Construir objeto Concurso con datos del historial
                        concurso_dict = {
                            "nombre": hist_data.get("nombre"),
                            "url": hist_data.get("url"),
                            "organismo": hist_data.get("organismo"),
                            "fecha_apertura": latest_version.get("fecha_apertura"),
                            "fecha_cierre": latest_version.get("fecha_cierre"),
                            "estado": latest_version.get("estado"),
                            "financiamiento": latest_version.get("financiamiento") or hist_data.get("financiamiento"),
                            "descripcion": latest_version.get("descripcion") or hist_data.get("descripcion"),
                            "subdireccion": latest_version.get("subdireccion") or hist_data.get("subdireccion"),
                            "fecha_apertura_original": latest_version.get("fecha_apertura"),
                            "fuente": site
                        }
                        
                        # Normalizar fechas si es necesario
                        if concurso_dict["fecha_apertura"]:
                            parsed = parse_date(concurso_dict["fecha_apertura"])
                            if parsed:
                                concurso_dict["fecha_apertura"] = parsed.strftime("%Y-%m-%d")
                        
                        if concurso_dict["fecha_cierre"]:
                            parsed = parse_date(concurso_dict["fecha_cierre"])
                            if parsed:
                                concurso_dict["fecha_cierre"] = parsed.strftime("%Y-%m-%d")
                        
                        # Calcular estado si no está
                        if not concurso_dict["estado"]:
                            if concurso_dict["fecha_cierre"]:
                                if is_past_date(concurso_dict["fecha_cierre"]):
                                    concurso_dict["estado"] = "Cerrado"
                                else:
                                    concurso_dict["estado"] = "Abierto"
                        
                        concurso_from_history = Concurso(**concurso_dict)
                        existing_concursos_from_history.append(concurso_from_history)
                    except Exception as e:
                        logger.warning(f"Error al reconstruir concurso desde historial: {e}")
                        # Si falla, usar el concurso extraído normalmente
                        new_concursos_list.append(concurso)
                else:
                    # No hay versiones, usar el concurso extraído
                    new_concursos_list.append(concurso)
            
            new_concursos = new_concursos_list
            
            logger.info(
                f"📊 Análisis de historial: {len(existing_concursos_from_history)} existentes, "
                f"{len(new_concursos)} nuevos"
            )
            
            if "history" not in debug_info:
                debug_info["history"] = {}
            debug_info["history"]["new_concursos"] = len(new_concursos)
            debug_info["history"]["existing_concursos"] = len(existing_concursos_from_history)
        else:
            # No hay historial, todos son nuevos
            new_concursos = all_concursos
        
        # Fase 4: Scraping de URLs individuales SOLO para concursos nuevos
        if new_concursos and status_callback:
            status_callback(f"Scrapeando páginas individuales de {len(new_concursos)} concursos nuevos...")
        
        # Extraer URLs únicas SOLO de concursos nuevos
        concurso_urls = set()
        for concurso in new_concursos:
            if not getattr(concurso, "url", None):
                continue
            
            url_val = concurso.url.strip()
            if not url_val:
                continue
            
            # Optimización: si la URL indica explícitamente que el concurso está suspendido,
            # no es necesario scrappear la página individual para completar datos básicos.
            # Lo marcamos directamente como "Suspendido" y no lo incluimos en la cola de scraping.
            if "concurso-suspendido" in url_val:
                if not getattr(concurso, "estado", None):
                    concurso.estado = "Suspendido"
                logger.info(
                    f"🔎 Detectado concurso suspendido por URL: {url_val}. "
                    f"Marcando estado='Suspendido' y omitiendo scraping individual."
                )
                continue
            
            if url_val not in concurso_urls:
                concurso_urls.add(url_val)
        
        debug_info["scraping"]["individual_pages_scraped"] = 0
        debug_info["scraping"]["individual_pages_failed"] = 0
        
        # Crear un diccionario para mapear URLs a contenido enriquecido
        enriched_content = {}
        
        # Scrapear URLs individuales de forma asíncrona pero secuencial (más robusto)
        async def scrape_all_individual_urls():
            """Scrapea todas las URLs individuales dentro de una sola sesión asíncrona"""
            results = {}
            for i, concurso_url in enumerate(concurso_urls):
                if should_stop_callback and should_stop_callback():
                    logger.info("Proceso detenido durante scraping de URLs individuales")
                    break
                
                try:
                    if status_callback:
                        status_callback(f"Scrapeando concurso {i+1}/{len(concurso_urls)}: {concurso_url}")
                    
                    # Scrapear URL individual usando método simple (sin hooks complejos)
                    result = await self.scraper.scrape_url_simple(concurso_url)
                    results[concurso_url] = result
                    
                except Exception as e:
                    logger.error(f"Error al scrapear URL individual {concurso_url}: {e}", exc_info=True)
                    results[concurso_url] = {
                        "success": False,
                        "error": str(e)
                    }
            return results
        
        # Ejecutar scraping asíncrono de todas las URLs
        try:
            individual_results = asyncio.run(scrape_all_individual_urls())
            
            # Procesar resultados
            for concurso_url, result in individual_results.items():
                if result.get("success") and result.get("markdown"):
                    markdown = result["markdown"]
                    cleaned_markdown = clean_markdown_for_llm(markdown)
                    
                    # Extraer información de "Concursos anteriores" usando la estrategia apropiada
                    previous_concursos = []
                    html_content = result.get("html", "")
                    
                    # Detectar concursos suspendidos directamente desde el HTML/markdown
                    is_suspended = False
                    try:
                        texto_busqueda = (html_content or "") + "\n" + (markdown or "")
                        texto_busqueda_lower = texto_busqueda.lower()
                        if "concurso suspendido" in texto_busqueda_lower or "concurso suspendido" in texto_busqueda_lower:
                            is_suspended = True
                    except Exception:
                        # Si algo falla en la detección, no marcamos como suspendido y continuamos normalmente
                        is_suspended = False
                    
                    # Extraer información de "Concursos anteriores" usando la estrategia apropiada
                    strategy = get_strategy_for_url(concurso_url)
                    if html_content:
                        try:
                            previous_concursos = strategy.extract_previous_concursos(html_content, concurso_url)
                            # El log de extracción ya se hace en el extractor específico
                        except Exception as e:
                            logger.warning(
                                f"Error al extraer concursos anteriores de {concurso_url}: {e}",
                                exc_info=True
                            )
                    
                    # Guardar HTML/MD completos en cache sin compresión (solo en scraping inicial)
                    try:
                        if site:
                            save_page_cache(site, concurso_url, html_content or "", markdown or "")
                    except Exception as e:
                        logger.warning(f"⚠️ No se pudo guardar cache de página para {concurso_url}: {e}")
                    
                    # OPTIMIZACIÓN: Intentar extraer datos determinísticamente antes de usar LLM
                    # Extrae: nombre, fechas, y detecta suspendido
                    deterministic_data = None
                    if not is_suspended:  # Solo intentar si no está suspendido
                        from utils.deterministic_date_extractor import extract_concurso_data_deterministically
                        try:
                            deterministic_data = extract_concurso_data_deterministically(
                                cleaned_markdown,
                                concurso_url,
                                html_content
                            )
                            if deterministic_data:
                                logger.debug(
                                    f"✅ Datos extraídos determinísticamente para {concurso_url}: "
                                    f"nombre={deterministic_data.get('nombre')}, "
                                    f"apertura={deterministic_data.get('fecha_apertura')}, "
                                    f"cierre={deterministic_data.get('fecha_cierre')}"
                                )
                        except Exception as e:
                            logger.debug(f"Error en extracción determinística para {concurso_url}: {e}")
                            deterministic_data = None
                    
                    enriched_content[concurso_url] = {
                        "markdown": cleaned_markdown,
                        "html_size": len(html_content),
                        "markdown_size": len(markdown),
                        "markdown_cleaned_size": len(cleaned_markdown),
                        "previous_concursos": previous_concursos,  # Información histórica extraída
                        "is_suspended": is_suspended,
                        "deterministic_data": deterministic_data,  # Datos extraídos determinísticamente (nombre, fechas, suspendido)
                    }
                    debug_info["scraping"]["individual_pages_scraped"] += 1
                    debug_info["scraping"]["total_html_size"] += len(html_content)
                    debug_info["scraping"]["total_markdown_size"] += len(markdown)
                    debug_info["scraping"]["total_markdown_cleaned_size"] += len(cleaned_markdown)
                    
                    # Registrar en debug_info cuántos concursos anteriores se encontraron
                    if previous_concursos:
                        if "previous_concursos_extracted" not in debug_info["scraping"]:
                            debug_info["scraping"]["previous_concursos_extracted"] = {}
                        debug_info["scraping"]["previous_concursos_extracted"][concurso_url] = len(previous_concursos)
                else:
                    debug_info["scraping"]["individual_pages_failed"] += 1
                    error_msg = result.get("error", "Error desconocido")
                    logger.warning(f"No se pudo scrapear URL individual: {concurso_url} - {error_msg}")
                    debug_info["scraping"]["errors"].append({
                        "url": concurso_url,
                        "error": error_msg,
                        "type": type(result.get("error", Exception())).__name__ if result.get("error") else "UnknownError",
                        "context": "individual_page_scraping"
                    })
        except Exception as e:
            logger.error(f"Error general al scrapear URLs individuales: {e}", exc_info=True)
            debug_info["scraping"]["errors"].append({
                "error": str(e),
                "type": type(e).__name__,
                "context": "individual_page_scraping_batch"
            })
        
        # Guardar enriched_content en debug_info para acceso posterior
        debug_info["scraping"]["enriched_content"] = enriched_content
        
        # Fase 5: Enriquecer concursos con información de páginas individuales
        if enriched_content and status_callback:
            status_callback("Enriqueciendo concursos con información detallada...")
        
        # Agrupar URLs por batch para enriquecimiento
        enriched_batches = []
        current_batch = []
        current_size = 0
        
        for url, content in enriched_content.items():
            content_size = len(content["markdown"])
            if current_size + content_size > batch_size and current_batch:
                # Guardar batch actual
                combined_markdown = "\n\n---SEPARADOR DE CONCURSO---\n\n".join([enriched_content[u]["markdown"] for u in current_batch])
                enriched_batches.append((current_batch, combined_markdown))
                current_batch = [url]
                current_size = content_size
            else:
                current_batch.append(url)
                current_size += content_size
        
        if current_batch:
            combined_markdown = "\n\n---SEPARADOR DE CONCURSO---\n\n".join([enriched_content[u]["markdown"] for u in current_batch])
            enriched_batches.append((current_batch, combined_markdown))
        
        # Extraer información adicional de páginas individuales
        continue_on_error_enrichment = self.extraction_config.get("continue_on_error", True)
        
        for batch_urls, combined_markdown in enriched_batches:
            if should_stop_callback and should_stop_callback():
                break
            
            # Verificar tiempo total transcurrido
            if max_total_time:
                elapsed_time = (datetime.now() - execution_start_time).total_seconds()
                if elapsed_time > max_total_time:
                    logger.warning(f"⏱️ Tiempo máximo de ejecución alcanzado durante enriquecimiento. Deteniendo.")
                    break
            
            try:
                enriched_concursos, _ = self.extractor.extract_from_batch(
                    combined_markdown,
                    batch_urls
                )
                
                # Actualizar concursos nuevos con información enriquecida.
                # OPTIMIZACIÓN: Preferir fechas determinísticas sobre las del LLM si están disponibles.
                # El LLM todavía se usa para nombre, organismo, descripción, etc.
                for enriched in enriched_concursos:
                    for concurso in new_concursos:
                        if concurso.url == enriched.url:
                            # Obtener datos determinísticos si están disponibles
                            deterministic_data = enriched_content.get(concurso.url, {}).get("deterministic_data")
                            
                            # Si tenemos datos determinísticos, usarlos en lugar de los del LLM
                            if deterministic_data:
                                # Usar nombre determinístico si está disponible y el LLM no encontró uno válido
                                if deterministic_data.get("nombre") and (
                                    not enriched.nombre or 
                                    enriched.nombre.strip().lower() == "concurso sin título"
                                ):
                                    enriched.nombre = deterministic_data["nombre"]
                                
                                # Usar fechas determinísticas si están disponibles
                                if deterministic_data.get("fecha_apertura") and not concurso.fecha_apertura:
                                    enriched.fecha_apertura = deterministic_data["fecha_apertura"]
                                    enriched.fecha_apertura_original = deterministic_data["fecha_apertura"]
                                if deterministic_data.get("fecha_cierre") and not concurso.fecha_cierre:
                                    enriched.fecha_cierre = deterministic_data["fecha_cierre"]
                            
                            self._update_concurso_from_enriched(concurso, enriched, debug_info, enriched_content.get(concurso.url, {}))
                            break
                            
            except Exception as e:
                self._log_and_capture_error(
                    e,
                    "enrichment",
                    batch_urls,
                    debug_info
                )
                
                # Continuar o abortar según configuración
                if not continue_on_error_enrichment:
                    logger.error("❌ continue_on_error=False. Abortando enriquecimiento.")
                    break
                else:
                    logger.warning(f"⚠️ Continuando con siguiente batch de enriquecimiento a pesar del error...")
        
        # Refuerzo: segundo intento focalizado en FECHAS para concursos que aún
        # no tienen fecha_cierre (y cuya página individual fue scrapeada con éxito).
        enrichment_debug = debug_info.setdefault("enrichment", {})
        
        if enriched_content:
            # Excluir concursos suspendidos del reintento de fechas:
            # si la página o la URL indican suspensión, no vale la pena llamar al LLM.
            missing_date_concursos: List[Concurso] = []
            skipped_suspended = 0
            for c in new_concursos:
                url_c = getattr(c, "url", None)
                if not url_c or url_c not in enriched_content:
                    continue
                if getattr(c, "fecha_cierre", None) is not None:
                    continue
                content_meta = enriched_content.get(url_c, {})
                if content_meta.get("is_suspended"):
                    # Forzar estado suspendido si no está
                    if not getattr(c, "estado", None):
                        c.estado = "Suspendido"
                    skipped_suspended += 1
                    continue
                missing_date_concursos.append(c)
            
            enrichment_debug["date_retry_candidates"] = len(missing_date_concursos)
            if skipped_suspended:
                enrichment_debug["date_retry_skipped_suspended"] = skipped_suspended
            
            if missing_date_concursos:
                enrichment_debug["date_retry_attempted"] = True
                if status_callback:
                    status_callback(
                        f"Reintentando extracción de fechas para {len(missing_date_concursos)} concursos sin fecha de cierre..."
                    )
                
                # Construir batches sólo con los concursos problemáticos
                retry_urls = [c.url for c in missing_date_concursos if getattr(c, "url", None) in enriched_content]
                date_retry_batches = []
                current_batch_urls: List[str] = []
                current_size = 0
                
                for url in retry_urls:
                    content = enriched_content.get(url)
                    if not content:
                        continue
                    content_size = len(content["markdown"])
                    if current_size + content_size > batch_size and current_batch_urls:
                        combined_markdown = "\n\n---SEPARADOR DE CONCURSO---\n\n".join(
                            [enriched_content[u]["markdown"] for u in current_batch_urls]
                        )
                        date_retry_batches.append((current_batch_urls, combined_markdown))
                        current_batch_urls = [url]
                        current_size = content_size
                    else:
                        current_batch_urls.append(url)
                        current_size += content_size
                
                if current_batch_urls:
                    combined_markdown = "\n\n---SEPARADOR DE CONCURSO---\n\n".join(
                        [enriched_content[u]["markdown"] for u in current_batch_urls]
                    )
                    date_retry_batches.append((current_batch_urls, combined_markdown))
                
                date_retry_success = 0
                
                for batch_urls, combined_markdown in date_retry_batches:
                    if should_stop_callback and should_stop_callback():
                        break
                    
                    if max_total_time:
                        elapsed_time = (datetime.now() - execution_start_time).total_seconds()
                        if elapsed_time > max_total_time:
                            logger.warning(
                                "⏱️ Tiempo máximo de ejecución alcanzado durante reintento de fechas. Deteniendo."
                            )
                            break
                    
                    try:
                        enriched_concursos, _ = self.extractor.extract_from_batch(
                            combined_markdown,
                            batch_urls,
                        )
                        
                        for enriched in enriched_concursos:
                            for concurso in new_concursos:
                                if concurso.url == enriched.url:
                                    before_cierre = concurso.fecha_cierre
                                    before_estado = concurso.estado
                                    
                                    # Solo nos enfocamos en fechas y estado en este reintento
                                    # Usar función helper pero solo para campos críticos
                                    if not concurso.fecha_apertura and enriched.fecha_apertura:
                                        concurso.fecha_apertura = enriched.fecha_apertura
                                        concurso.fecha_apertura_original = (
                                            enriched.fecha_apertura_original or enriched.fecha_apertura
                                        )
                                    if not concurso.fecha_cierre and enriched.fecha_cierre:
                                        concurso.fecha_cierre = enriched.fecha_cierre
                                    if not concurso.estado and enriched.estado:
                                        concurso.estado = enriched.estado
                                    
                                    if (before_cierre is None and concurso.fecha_cierre is not None) or (
                                        before_estado is None and concurso.estado is not None
                                    ):
                                        date_retry_success += 1
                                    
                                    break
                    
                    except Exception as e:
                        self._log_and_capture_error(
                            e,
                            "enrichment_date_retry",
                            batch_urls,
                            debug_info
                        )
                        
                        if not continue_on_error_enrichment:
                            logger.error("❌ continue_on_error=False. Abortando reintento de fechas.")
                            break
                        else:
                            logger.warning(
                                "⚠️ Continuando con siguiente batch de reintento de fechas a pesar del error..."
                            )
                
                enrichment_debug["date_retry_success"] = date_retry_success
                
                # Registrar concursos que siguen sin fecha de cierre tras el reintento
                still_missing = [
                    c for c in new_concursos
                    if (getattr(c, "url", None) in enriched_content)
                    and (getattr(c, "fecha_cierre", None) is None)
                    and not enriched_content.get(getattr(c, "url", None), {}).get("is_suspended")
                ]
                enrichment_debug["date_retry_remaining"] = len(still_missing)
                
                for concurso in still_missing:
                    warning_entry = {
                        "type": "missing_fecha_cierre_after_retry",
                        "concurso_nombre": concurso.nombre,
                        "concurso_url": getattr(concurso, "url", None),
                        "message": "Concurso sin fecha de cierre incluso después del reintento focalizado.",
                    }
                    debug_info["warnings"].append(warning_entry)
                    logger.warning(
                        f"⚠️ [missing_fecha_cierre_after_retry] Concurso '{concurso.nombre}' "
                        f"({getattr(concurso, 'url', None)}) sigue sin fecha de cierre tras reintento."
                    )
        
        # Fase 6: Combinar concursos nuevos y existentes, luego eliminar duplicados
        # Combinar todos los concursos (nuevos procesados + existentes del historial)
        all_concursos_combined = new_concursos + existing_concursos_from_history
        
        # Eliminar duplicados
        unique_concursos = self._deduplicate_concursos(all_concursos_combined)
        debug_info["extraction"]["concursos_after_dedup"] = len(unique_concursos)
        debug_info["extraction"]["duplicates_removed"] = len(all_concursos_combined) - len(unique_concursos)
        logger.info(f"Total de concursos únicos después de deduplicación: {len(unique_concursos)}")
        
        # Validación final: Detectar pérdida total de datos
        total_pages_scraped = debug_info["scraping"]["pages_scraped"]
        total_new_concursos = len(new_concursos)
        expected_min_total = total_pages_scraped * 5  # Mínimo conservador: 5 por página
        expected_typical_total = total_pages_scraped * 6  # Típico: 6 por página
        
        if total_pages_scraped > 0:
            concursos_per_page_avg = total_new_concursos / total_pages_scraped
            if concursos_per_page_avg < 4:
                logger.warning(
                    f"🚨 VALIDACIÓN FINAL: Posible pérdida significativa de datos detectada. "
                    f"Se scrapearon {total_pages_scraped} páginas pero solo se encontraron {total_new_concursos} concursos nuevos "
                    f"({concursos_per_page_avg:.1f} por página). Se esperaban aproximadamente {expected_typical_total} "
                    f"({expected_typical_total/total_pages_scraped:.1f} por página)."
                )
                debug_info["warnings"].append({
                    "type": "total_data_loss_detected",
                    "total_pages_scraped": total_pages_scraped,
                    "total_new_concursos": total_new_concursos,
                    "concursos_per_page_avg": concursos_per_page_avg,
                    "expected_typical": expected_typical_total,
                    "expected_per_page": 6,
                    "severity": "high" if concursos_per_page_avg < 3 else "medium"
                })
            elif concursos_per_page_avg < 5:
                logger.info(
                    f"ℹ️ Validación final: {total_new_concursos} concursos nuevos de {total_pages_scraped} páginas "
                    f"({concursos_per_page_avg:.1f} por página). Esto puede ser normal si la última página tiene menos concursos."
                )
        
        # Fase 6.5: Actualizar historial SOLO con concursos nuevos (no los que ya estaban en historial)
        if site:
            if status_callback:
                status_callback("💾 Actualizando historial...")
            
            try:
                # Solo pasar los concursos nuevos al historial, no los que ya existían
                # Usar directamente new_concursos en lugar de filtrar unique_concursos
                # porque unique_concursos incluye los existentes del historial
                concursos_to_update = new_concursos
                
                logger.info(
                    f"📝 Actualizando historial con {len(concursos_to_update)} concursos nuevos "
                    f"(de {len(unique_concursos)} totales, {len(existing_concursos_from_history)} existentes)"
                )
                
                # Pasar enriched_content al historial para guardar contenido completo
                # skip_similarity_check=True porque estos concursos ya fueron validados como nuevos
                # en find_existing_concursos, no deben agruparse con existentes
                updated_history = self.history_manager.update_history(
                    site,
                    concursos_to_update,
                    existing_keys=existing_keys_set,
                    enriched_content=enriched_content,
                    skip_similarity_check=True  # Ya validados como nuevos, no agrupar por similitud
                )
                history_file = self.history_manager.save_history(site, updated_history)
                logger.info(f"💾 Historial actualizado: {history_file}")
                debug_info["history"]["history_file"] = history_file
                debug_info["history"]["total_concursos_in_history"] = len(updated_history.get("concursos", []))
                # Usar este historial actualizado para la fase de predicciones,
                # evitando recargarlo desde disco innecesariamente.
                history_for_predictions = updated_history
            except Exception as e:
                logger.error(f"Error al actualizar historial: {e}", exc_info=True)
                debug_info["history"]["update_error"] = str(e)
        
        # Fase 6.6: Removida - Las predicciones ahora se generan en un proceso separado
        # Ver services/prediction_service.py para generar predicciones
        
        # Finalizar información de debug
        debug_info["execution"]["end_time"] = datetime.now().isoformat()
        debug_info["execution"]["duration_seconds"] = (
            datetime.fromisoformat(debug_info["execution"]["end_time"]) - 
            datetime.fromisoformat(debug_info["execution"]["start_time"])
        ).total_seconds()
        
        # Guardar archivo de debug (incluyendo contenido raw y procesado)
        try:
            # Obtener último archivo raw generado
            last_raw_file = debug_info["llm"]["raw_files"][-1] if debug_info["llm"]["raw_files"] else None
            raw_content = None
            if last_raw_file:
                try:
                    with open(last_raw_file, "r", encoding="utf-8") as f:
                        raw_content = json.load(f)
                except (IOError, OSError, json.JSONDecodeError):
                    # Si no se puede leer el archivo raw, continuar sin él
                    pass
            
            # Convertir concursos a dict para incluir en debug
            processed_concursos = [c.model_dump() if hasattr(c, 'model_dump') else c for c in unique_concursos]
            
            # Guardar resultados procesados temporalmente para incluir en debug
            processed_file = save_results(processed_concursos)
            
            # Incluir solo metadata de contenido (no contenido completo para reducir tamaño)
            enriched_content = debug_info.get("scraping", {}).get("enriched_content", {})
            individual_pages_content = {}
            for url, content in enriched_content.items():
                individual_pages_content[url] = {
                    "html_size": content.get("html_size", 0),
                    "markdown_size": content.get("markdown_size", 0),
                    "markdown_cleaned_size": content.get("markdown_cleaned_size", 0),
                    "has_previous_concursos": len(content.get("previous_concursos", [])) > 0,
                    "previous_concursos_count": len(content.get("previous_concursos", []))
                }
            
            # Incluir información de concursos anteriores en el debug
            previous_concursos_by_url = {}
            for url, content in enriched_content.items():
                prev_concursos = content.get("previous_concursos", [])
                if prev_concursos:
                    previous_concursos_by_url[url] = {
                        "count": len(prev_concursos),
                        "concursos": prev_concursos  # Guardar detalles completos
                    }
            
            # Incluir solo metadata de contenido (no contenido completo)
            debug_info["raw_content"] = {"available": raw_content is not None}
            debug_info["individual_pages_content"] = individual_pages_content
            debug_info["previous_concursos_extracted"] = previous_concursos_by_url
            debug_info["processed_content"] = {
                "file": processed_file,
                "concursos_count": len(processed_concursos)
            }
            
            debug_file_path = save_debug_info_scraping(debug_info)
            logger.info(f"🐛 Archivo de debug generado: {debug_file_path}")
        except Exception as e:
            logger.error(f"Error al guardar archivo de debug: {e}", exc_info=True)
        
        if status_callback:
            status_callback(f"✅ Procesamiento completado exitosamente")
        
        return unique_concursos
    
    def repair_incomplete_concursos(
        self,
        site: str,
        incomplete_urls: List[str],
        status_callback: Optional[callable] = None,
        should_stop_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Repara concursos incompletos scrapeando solo sus URLs individuales.
        
        Args:
            site: Nombre del sitio (ej: "anid.cl")
            incomplete_urls: Lista de URLs de concursos incompletos a reparar
            status_callback: Función callback para reportar estado
            should_stop_callback: Función callback para verificar si debe detenerse
            
        Returns:
            Diccionario con estadísticas de la reparación
        """
        from crawler.markdown_processor import clean_markdown_for_llm
        # NOTA: extract_previous_concursos_from_html ahora se usa a través de estrategias
        from models import Concurso as ConcursoModel
        
        repair_stats = {
            "urls_processed": 0,
            "urls_successful": 0,
            "urls_failed": 0,
            "concursos_repaired": 0,
            "concursos_still_incomplete": [],
            "errors": []
        }
        
        if not incomplete_urls:
            logger.info("No hay URLs incompletas para reparar")
            return repair_stats
        
        # Cargar historial una sola vez para poder marcar concursos suspendidos
        history = self.history_manager.load_history(site)
        history_changed = False
        history_index: Dict[str, Dict[str, Any]] = {}
        for hist_concurso in history.get("concursos", []):
            hist_url = (hist_concurso.get("url") or "").strip()
            if hist_url:
                history_index[hist_url] = hist_concurso
        
        # Separar URLs que deben marcarse directamente como "Suspendido"
        # (por tener "concurso-suspendido" en su ruta) de las que sí requieren scraping.
        urls_to_scrape: List[str] = []
        cached_results: Dict[str, Dict[str, Any]] = {}
        for url in incomplete_urls:
            if not url:
                continue
            url_stripped = url.strip()
            
            if "concurso-suspendido" in url_stripped:
                hist_concurso = history_index.get(url_stripped)
                if not hist_concurso:
                    logger.warning(
                        f"⚠️ URL marcada como 'concurso-suspendido' no encontrada en historial: {url_stripped}"
                    )
                    continue
                
                # Marcar estado suspendido a nivel de historial y en su última versión
                hist_concurso["estado"] = "Suspendido"
                versions = hist_concurso.get("versions", [])
                if not versions:
                    # Si no hay versiones, crear una nueva con el estado suspendido
                    versions = [{
                        "estado": "Suspendido",
                        "detected_at": datetime.now().isoformat()
                    }]
                    hist_concurso["versions"] = versions
                else:
                    latest_version = versions[-1]
                    latest_version["estado"] = "Suspendido"
                    if "detected_at" not in latest_version:
                        latest_version["detected_at"] = datetime.now().isoformat()
                    versions[-1] = latest_version
                    hist_concurso["versions"] = versions
                hist_concurso["last_seen"] = datetime.now().isoformat()
                history_changed = True
                repair_stats["concursos_repaired"] += 1
                
                logger.info(
                    f"✅ Concurso marcado como 'Suspendido' sin scraping por patrón en URL: {url_stripped}"
                )
            else:
                # Intentar usar cache existente antes de re-scrapear
                cached = load_page_cache(site, url_stripped)
                if cached and cached.get("markdown"):
                    cached_results[url_stripped] = {
                        "success": True,
                        "html": cached.get("html", ""),
                        "markdown": cached.get("markdown", ""),
                        "url": url_stripped,
                        "cache_hit": True
                    }
                    repair_stats["urls_processed"] += 1
                    repair_stats["urls_successful"] += 1
                else:
                    urls_to_scrape.append(url_stripped)
        
        # Guardar historial si hubo cambios por marcaje directo de suspendidos
        if history_changed:
            try:
                self.history_manager.save_history(site, history)
            except Exception as e:
                logger.error(
                    f"Error al guardar historial después de marcar concursos suspendidos: {e}",
                    exc_info=True
                )
        
        if status_callback and urls_to_scrape:
            status_callback(f"Scrapeando {len(urls_to_scrape)} URLs de concursos incompletos...")
        
        # Scrapear URLs individuales
        async def scrape_repair_urls():
            """Scrapea las URLs de reparación"""
            results = {}
            for i, url in enumerate(urls_to_scrape):
                if should_stop_callback and should_stop_callback():
                    logger.info("Proceso de reparación detenido por el usuario")
                    break
                
                try:
                    if status_callback:
                        status_callback(f"Scrapeando {i+1}/{len(urls_to_scrape)}: {url}")
                    
                    result = await self.scraper.scrape_url_simple(url)
                    results[url] = result
                    repair_stats["urls_processed"] += 1
                    
                except Exception as e:
                    logger.error(f"Error al scrapear URL de reparación {url}: {e}", exc_info=True)
                    results[url] = {
                        "success": False,
                        "error": str(e)
                    }
                    repair_stats["urls_failed"] += 1
                    repair_stats["errors"].append({
                        "url": url,
                        "error": str(e),
                        "type": type(e).__name__
                    })
            return results
        
        # Ejecutar scraping (solo para las URLs que no tenían cache)
        individual_results = dict(cached_results)
        if urls_to_scrape:
            try:
                scraped_results = asyncio.run(scrape_repair_urls())
                individual_results.update(scraped_results)
            except Exception as e:
                logger.error(f"Error general al scrapear URLs de reparación: {e}", exc_info=True)
                repair_stats["errors"].append({
                    "error": str(e),
                    "type": type(e).__name__,
                    "context": "repair_scraping_batch"
                })
                return repair_stats
        
        # Procesar resultados y crear enriched_content
        enriched_content = {}
        for url, result in individual_results.items():
            if result.get("success") and result.get("markdown"):
                markdown = result["markdown"]
                cleaned_markdown = clean_markdown_for_llm(markdown)
                
                # Extraer información de "Concursos anteriores" usando la estrategia apropiada
                previous_concursos = []
                html_content = result.get("html", "")
                is_cache_hit = result.get("cache_hit", False)
                
                # Guardar/actualizar cache si proviene de scraping nuevo
                if not is_cache_hit:
                    try:
                        save_page_cache(site, url, html_content or "", markdown or "")
                    except Exception as e:
                        logger.warning(f"⚠️ No se pudo guardar cache de página (repair) para {url}: {e}")
                
                # Detectar concursos suspendidos directamente desde el HTML/markdown
                is_suspended = False
                try:
                    texto_busqueda = (html_content or "") + "\n" + (markdown or "")
                    texto_busqueda_lower = texto_busqueda.lower()
                    if "concurso suspendido" in texto_busqueda_lower or "concurso suspendido" in texto_busqueda_lower:
                        is_suspended = True
                except Exception:
                    is_suspended = False
                
                # Extraer información de "Concursos anteriores" usando la estrategia apropiada
                strategy = get_strategy_for_url(url)
                if html_content:
                    try:
                        previous_concursos = strategy.extract_previous_concursos(html_content, url)
                    except Exception as e:
                        logger.warning(f"Error al extraer concursos anteriores de {url}: {e}")
                
                # OPTIMIZACIÓN: Intentar extraer datos determinísticamente antes de usar LLM
                deterministic_data = None
                if not is_suspended:  # Solo intentar si no está suspendido
                    from utils.deterministic_date_extractor import extract_concurso_data_deterministically
                    try:
                        deterministic_data = extract_concurso_data_deterministically(
                            cleaned_markdown,
                            url,
                            html_content
                        )
                        if deterministic_data:
                            logger.debug(
                                f"✅ Datos extraídos determinísticamente para reparación {url}: "
                                f"nombre={deterministic_data.get('nombre')}, "
                                f"apertura={deterministic_data.get('fecha_apertura')}, "
                                f"cierre={deterministic_data.get('fecha_cierre')}"
                            )
                    except Exception as e:
                        logger.debug(f"Error en extracción determinística para reparación {url}: {e}")
                        deterministic_data = None
                
                enriched_content[url] = {
                    "markdown": cleaned_markdown,
                    "html_size": len(html_content),
                    "markdown_size": len(markdown),
                    "markdown_cleaned_size": len(cleaned_markdown),
                    "previous_concursos": previous_concursos,
                    "is_suspended": is_suspended,
                    "deterministic_data": deterministic_data,  # Datos extraídos determinísticamente
                }
                repair_stats["urls_successful"] += 1
            else:
                repair_stats["urls_failed"] += 1
                error_msg = result.get("error", "Error desconocido")
                repair_stats["errors"].append({
                    "url": url,
                    "error": error_msg,
                    "type": "scraping_failed"
                })
        
        if not enriched_content:
            # Si no hay contenido enriquecido, puede ser porque:
            # 1. Todas las URLs fueron marcadas como suspendidas sin scraping
            # 2. Falló el scraping de todas las URLs
            # En cualquier caso, debemos recalcular los incompletos finales
            logger.info("No hay contenido enriquecido para procesar con LLM. Recalculando concursos incompletos...")
            try:
                final_incomplete = self.history_manager.find_incomplete_concurso_urls(site)
                repair_stats["concursos_still_incomplete"] = [
                    {
                        "url": entry["url"],
                        "nombre": entry["nombre"],
                        "estado": entry["estado"],
                        "fecha_apertura": entry["fecha_apertura"],
                        "fecha_cierre": entry["fecha_cierre"]
                    }
                    for entry in final_incomplete
                ]
            except Exception as e:
                logger.error(f"Error al recalcular concursos incompletos: {e}", exc_info=True)
            return repair_stats
        
        # Agrupar en batches para el LLM
        if status_callback:
            status_callback("Extrayendo información con LLM...")
        
        batch_size = self.extraction_config.get("batch_size", 500000)
        enriched_batches = []
        current_batch = []
        current_size = 0
        
        for url, content in enriched_content.items():
            content_size = len(content["markdown"])
            if current_size + content_size > batch_size and current_batch:
                combined_markdown = "\n\n---SEPARADOR DE CONCURSO---\n\n".join(
                    [enriched_content[u]["markdown"] for u in current_batch]
                )
                enriched_batches.append((current_batch, combined_markdown))
                current_batch = [url]
                current_size = content_size
            else:
                current_batch.append(url)
                current_size += content_size
        
        if current_batch:
            combined_markdown = "\n\n---SEPARADOR DE CONCURSO---\n\n".join(
                [enriched_content[u]["markdown"] for u in current_batch]
            )
            enriched_batches.append((current_batch, combined_markdown))
        
        # Extraer información con LLM
        repaired_concursos = []
        for batch_urls, combined_markdown in enriched_batches:
            if should_stop_callback and should_stop_callback():
                break
            
            try:
                enriched_concursos, _ = self.extractor.extract_from_batch(
                    combined_markdown,
                    batch_urls
                )
                
                # Crear objetos Concurso para actualizar el historial
                # OPTIMIZACIÓN: Preferir fechas determinísticas sobre las del LLM si están disponibles
                for enriched in enriched_concursos:
                    # Asegurar que la URL esté correcta
                    if not enriched.url or enriched.url in batch_urls:
                        # Buscar la URL correcta en el batch
                        for batch_url in batch_urls:
                            if batch_url in enriched.url or enriched.url in batch_url:
                                enriched.url = batch_url
                                break
                        else:
                            # Si no se encuentra, usar la primera del batch
                            enriched.url = batch_urls[0] if batch_urls else None
                    
                    if enriched.url:
                        # Obtener datos determinísticos si están disponibles
                        deterministic_data = enriched_content.get(enriched.url, {}).get("deterministic_data")
                        
                        # Si tenemos datos determinísticos, usarlos en lugar de los del LLM
                        if deterministic_data:
                            # Usar nombre determinístico si está disponible
                            if deterministic_data.get("nombre") and (
                                not enriched.nombre or 
                                enriched.nombre.strip().lower() == "concurso sin título"
                            ):
                                enriched.nombre = deterministic_data["nombre"]
                            
                            # Usar fechas determinísticas si están disponibles
                            if deterministic_data.get("fecha_apertura"):
                                enriched.fecha_apertura = deterministic_data["fecha_apertura"]
                                enriched.fecha_apertura_original = deterministic_data["fecha_apertura"]
                            if deterministic_data.get("fecha_cierre"):
                                enriched.fecha_cierre = deterministic_data["fecha_cierre"]
                        
                        repaired_concursos.append(enriched)
                        
            except Exception as e:
                logger.error(f"Error al extraer información con LLM para reparación: {e}", exc_info=True)
                repair_stats["errors"].append({
                    "error": str(e),
                    "type": type(e).__name__,
                    "context": "llm_extraction",
                    "urls": batch_urls
                })
        
        if not repaired_concursos:
            logger.warning("No se pudieron extraer concursos reparados del LLM")
            return repair_stats
        
        # Actualizar historial con los concursos reparados
        if status_callback:
            status_callback("Actualizando historial con concursos reparados...")
        
        try:
            # Cargar historial actual
            history = self.history_manager.load_history(site)
            
            # Crear índice de concursos por URL
            history_index = {}
            for i, hist_concurso in enumerate(history.get("concursos", [])):
                hist_url = hist_concurso.get("url", "").strip()
                if hist_url:
                    history_index[hist_url] = i
            
            # Actualizar cada concurso reparado
            detected_at = datetime.now().isoformat()
            for repaired in repaired_concursos:
                if not repaired.url:
                    continue
                
                url_key = repaired.url.strip()
                if url_key in history_index:
                    idx = history_index[url_key]
                    hist_concurso = history["concursos"][idx]
                    # Obtener contenido enriquecido
                    page_content = enriched_content.get(url_key, {})
                    page_markdown = page_content.get("markdown", "")
                    previous_concursos = page_content.get("previous_concursos", [])
                    page_is_suspended = page_content.get("is_suspended", False)
                    
                    # Actualizar campos principales si están vacíos
                    updated = False
                    if (not hist_concurso.get("nombre") or 
                        hist_concurso.get("nombre", "").strip().lower() == "concurso sin título"):
                        if repaired.nombre and repaired.nombre.strip().lower() != "concurso sin título":
                            hist_concurso["nombre"] = repaired.nombre.strip()
                            updated = True
                    
                    # Actualizar última versión o crear nueva
                    versions = hist_concurso.get("versions", [])
                    if not versions:
                        versions = [{}]
                    
                    latest_version = versions[-1]
                    version_updated = False
                    
                    # Considerar fechas mal formateadas como ausentes para poder sobrescribirlas
                    def _malform_date(val: Optional[str]) -> bool:
                        if not val:
                            return True
                        import re
                        if "**" in val:
                            return True
                        return re.fullmatch(r"\d{4}-\d{2}-\d{2}", val.strip()) is None
                    
                    if (_malform_date(latest_version.get("fecha_apertura"))
                        and repaired.fecha_apertura):
                        latest_version["fecha_apertura"] = repaired.fecha_apertura
                        version_updated = True
                    
                    if (_malform_date(latest_version.get("fecha_cierre"))
                        and repaired.fecha_cierre):
                        latest_version["fecha_cierre"] = repaired.fecha_cierre
                        version_updated = True
                    
                    # Estado:
                    # - Si la página se detectó como "Concurso suspendido", forzar estado "Suspendido"
                    #   incluso si el LLM no lo devolvió.
                    # - En otro caso, solo completar si viene desde el LLM.
                    if page_is_suspended:
                        latest_version["estado"] = "Suspendido"
                        version_updated = True
                    elif not latest_version.get("estado") and repaired.estado:
                        latest_version["estado"] = repaired.estado
                        version_updated = True
                    
                    if version_updated:
                        latest_version["detected_at"] = detected_at
                        versions[-1] = latest_version
                        hist_concurso["versions"] = versions
                        updated = True
                    
                    # Actualizar otros campos si están disponibles
                    if not hist_concurso.get("organismo") and repaired.organismo:
                        hist_concurso["organismo"] = repaired.organismo
                        updated = True
                    
                    if not hist_concurso.get("subdireccion") and repaired.subdireccion:
                        hist_concurso["subdireccion"] = repaired.subdireccion
                        updated = True
                    
                    # Actualizar contenido de página y concursos anteriores
                    if page_markdown:
                        hist_concurso["latest_page_content"] = page_markdown
                        hist_concurso["latest_page_content_updated"] = detected_at
                    
                    hist_concurso["previous_concursos"] = previous_concursos
                    hist_concurso["previous_concursos_updated"] = detected_at
                    hist_concurso["last_seen"] = detected_at
                    
                    # Si el concurso está marcado como suspendido en la página y aún
                    # no tiene estado a nivel de historial, forzar también ahí.
                    if page_is_suspended:
                        hist_concurso["estado"] = "Suspendido"
                        updated = True
                    
                    if updated:
                        repair_stats["concursos_repaired"] += 1
                        logger.info(f"✅ Concurso reparado: {url_key}")
            
            # Guardar historial actualizado
            self.history_manager.save_history(site, history)
            
            # Verificar cuáles siguen incompletos
            final_incomplete = self.history_manager.find_incomplete_concurso_urls(site)
            repair_stats["concursos_still_incomplete"] = [
                {
                    "url": entry["url"],
                    "nombre": entry["nombre"],
                    "estado": entry["estado"],
                    "fecha_apertura": entry["fecha_apertura"],
                    "fecha_cierre": entry["fecha_cierre"]
                }
                for entry in final_incomplete
            ]
            
        except Exception as e:
            logger.error(f"Error al actualizar historial durante reparación: {e}", exc_info=True)
            repair_stats["errors"].append({
                "error": str(e),
                "type": type(e).__name__,
                "context": "history_update"
            })
        
        return repair_stats
    
    def _save_raw_results(
        self,
        batch_num: int,
        raw_data: Dict[str, Any],
        pages_in_batch: List[Dict[str, Any]],
        combined_markdown: str
    ) -> None:
        """
        Guarda resultados crudos de un batch para auditoría.
        
        Args:
            batch_num: Número del batch
            raw_data: Datos crudos de la extracción (respuesta del LLM, etc.)
            pages_in_batch: Lista de páginas procesadas en el batch
            combined_markdown: Markdown combinado enviado al LLM
        """
        from utils.file_manager import save_raw_crawl_results
        
        # Preparar datos completos para auditoría
        audit_data = {
            "metadata": {
                "batch_num": batch_num,
                "fecha_extraccion": datetime.now().isoformat(),
                "urls": raw_data.get("urls", []),
                "markdown_size": raw_data.get("markdown_size", 0),
                "llm_response_size": raw_data.get("llm_response_size", 0),
                "concursos_extraidos": raw_data.get("concursos_extraidos", 0)
            },
            "input": {
                "markdown": combined_markdown,
                "pages_info": [
                    {
                        "url": page.get("url", "unknown"),
                        "html_size": len(page.get("html", "")),
                        "markdown_size": len(page.get("markdown", ""))
                    }
                    for page in pages_in_batch
                ]
            },
            "llm_response": {
                "raw": raw_data.get("llm_response", ""),
                "parsed_concursos": raw_data.get("concursos", [])
            }
        }
        
        filepath = save_raw_crawl_results(audit_data, batch_num)
        return filepath
    
    def _re_extract_batch_with_powerful_model(
        self,
        combined_markdown: str,
        urls_in_batch: List[str],
        batch_num: int
    ) -> Optional[List[Concurso]]:
        """
        Re-extrae un batch usando un modelo más potente cuando se detecta pérdida de datos.
        
        Args:
            combined_markdown: Markdown combinado del batch
            urls_in_batch: Lista de URLs en el batch
            batch_num: Número del batch (para logging)
            
        Returns:
            Lista de concursos re-extraídos, o None si falla
        """
        # Usar el mismo modelo configurado (por defecto: gemini-2.5-flash-lite) para re-extracción
        # Esto evita cambiar a modelos más caros/pesados sin control explícito.
        powerful_model = GEMINI_CONFIG.get("model", "gemini-2.5-flash-lite")
        
        logger.info(
            f"🔄 Re-extrayendo batch {batch_num} con modelo {powerful_model} "
            f"({len(urls_in_batch)} páginas, {len(combined_markdown):,} caracteres)"
        )
        
        try:
            # Crear un extractor temporal con el modelo más potente
            powerful_extractor = LLMExtractor(
                api_key_manager=self.api_key_manager,
                model_name=powerful_model,
                config=self.extraction_config
            )
            
            # Re-extraer con el modelo más potente
            re_extracted_concursos, _ = powerful_extractor.extract_from_batch(
                combined_markdown,
                urls_in_batch
            )
            
            if re_extracted_concursos:
                logger.info(
                    f"✅ Re-extracción con {powerful_model} completada: "
                    f"{len(re_extracted_concursos)} concursos encontrados"
                )
                return re_extracted_concursos
            else:
                logger.warning(f"⚠️ Re-extracción con {powerful_model} no encontró concursos")
                return None
                
        except Exception as e:
            logger.error(
                f"❌ Error en re-extracción automática del batch {batch_num} con {powerful_model}: {e}",
                exc_info=True
            )
            return None
    
    def _scrape_url(
        self,
        url: str,
        follow_pagination: bool,
        max_pages: int
    ) -> List[Dict[str, Any]]:
        """
        Scrapea una URL, manejando paginación dinámica o tradicional.
        
        Args:
            url: URL a scrapear
            follow_pagination: Si True, detecta y procesa páginas adicionales
            max_pages: Número máximo de páginas a procesar
            
        Returns:
            Lista de resultados de scraping (una entrada por página)
        """
        import asyncio
        
        # Obtener estrategia apropiada para la URL
        strategy = get_strategy_for_url(url)
        
        if isinstance(strategy, CentroEstudiosStrategy):
            logger.info(f"Sitio {strategy.site_name}: forzando una sola página (sin paginación).")
            result = asyncio.run(self.scraper.scrape_url(url))
            if result.get("success"):
                return [result]
            return []
        
        if follow_pagination and strategy.supports_dynamic_pagination():
            # Paginación dinámica (requiere JavaScript)
            logger.info(f"Detectada paginación dinámica para {url}. Procesando hasta {max_pages} páginas...")
            return asyncio.run(
                self.scraper.scrape_url_with_pagination(url, max_pages=max_pages)
            )
        elif follow_pagination:
            # Paginación tradicional (enlaces HTML)
            logger.info(f"Usando paginación tradicional para {url}. Procesando hasta {max_pages} páginas...")
            return asyncio.run(
                self.scraper.scrape_url_with_pagination(url, max_pages=max_pages)
            )
        else:
            # Sin paginación
            result = asyncio.run(self.scraper.scrape_url(url))
            if result.get("success"):
                return [result]
            else:
                return []
    
    def _update_concurso_from_enriched(
        self,
        concurso: Concurso,
        enriched: Concurso,
        debug_info: Dict[str, Any],
        enriched_content_item: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Actualiza un concurso con información enriquecida del LLM.
        
        OPTIMIZACIÓN: Prefiere fechas determinísticas sobre las del LLM si están disponibles.
        
        Args:
            concurso: Concurso a actualizar
            enriched: Concurso enriquecido del LLM
            debug_info: Diccionario de debug para registrar cambios
            enriched_content_item: Item de enriched_content con fechas determinísticas (opcional)
        """
        # OPTIMIZACIÓN: Usar fechas determinísticas si están disponibles
        deterministic_data = enriched_content_item.get("deterministic_data") if enriched_content_item else None
        
        # Obtener subdirecciones conocidas desde la estrategia del sitio
        strategy = get_strategy_for_url(concurso.url)
        known_subdirecciones = strategy.get_known_subdirecciones()
        
        # OPTIMIZACIÓN: Usar nombre determinístico si está disponible
        if deterministic_data and deterministic_data.get("nombre"):
            if (
                not concurso.nombre or 
                concurso.nombre.strip().lower() == "concurso sin título" or
                concurso.nombre.strip().lower() in known_subdirecciones
            ):
                new_name = deterministic_data["nombre"].strip()
                if new_name.lower() not in known_subdirecciones:
                    logger.info(
                        f"📝 Usando nombre determinístico para {concurso.url}: '{new_name}'"
                    )
                    concurso.nombre = new_name
                    debug_info.setdefault("enrichment", {}).setdefault("name_updates", []).append({
                        "url": concurso.url,
                        "old_name": concurso.nombre or "Concurso sin título",
                        "new_name": new_name,
                        "source": "deterministic"
                    })
        
        # Actualizar nombre desde LLM si no hay determinístico y es genérico
        if (
            (not concurso.nombre or concurso.nombre.strip().lower() == "concurso sin título")
            and enriched.nombre
        ):
            new_name = enriched.nombre.strip()
            if new_name.lower() not in known_subdirecciones:
                logger.info(
                    f"📝 Actualizando nombre de concurso desde LLM: "
                    f"'{concurso.nombre}' -> '{new_name}' ({concurso.url})"
                )
                concurso.nombre = new_name
                debug_info.setdefault("enrichment", {}).setdefault("name_updates", []).append({
                    "url": concurso.url,
                    "old_name": "Concurso sin título",
                    "new_name": new_name,
                    "source": "llm"
                })
        
        # Actualizar financiamiento y descripción si están disponibles
        if enriched.financiamiento:
            concurso.financiamiento = enriched.financiamiento
        if enriched.descripcion:
            concurso.descripcion = enriched.descripcion
        
        # OPTIMIZACIÓN: Preferir fechas determinísticas sobre las del LLM
        if deterministic_data:
            # Usar fecha de apertura determinística si está disponible
            if deterministic_data.get("fecha_apertura") and not concurso.fecha_apertura:
                concurso.fecha_apertura = deterministic_data["fecha_apertura"]
                concurso.fecha_apertura_original = deterministic_data["fecha_apertura"]
                logger.debug(f"✅ Usando fecha de apertura determinística para {concurso.url}")
            # Usar fecha de cierre determinística si está disponible
            if deterministic_data.get("fecha_cierre") and not concurso.fecha_cierre:
                concurso.fecha_cierre = deterministic_data["fecha_cierre"]
                logger.debug(f"✅ Usando fecha de cierre determinística para {concurso.url}")
        else:
            # Fallback: usar fechas del LLM si no hay determinísticas
            if not concurso.fecha_apertura and enriched.fecha_apertura:
                concurso.fecha_apertura = enriched.fecha_apertura
                concurso.fecha_apertura_original = (
                    enriched.fecha_apertura_original or enriched.fecha_apertura
                )
            if not concurso.fecha_cierre and enriched.fecha_cierre:
                concurso.fecha_cierre = enriched.fecha_cierre
        
        # Completar estado si estaba vacío (el estado se calcula determinísticamente, pero
        # si el LLM lo detectó como suspendido, lo respetamos)
        if not concurso.estado and enriched.estado:
            concurso.estado = enriched.estado
        
        # Completar subdirección si estaba vacía
        if not concurso.subdireccion and enriched.subdireccion:
            concurso.subdireccion = enriched.subdireccion
    
    def _log_and_capture_error(
        self,
        error: Exception,
        context: str,
        urls: Optional[List[str]] = None,
        debug_info: Optional[Dict[str, Any]] = None,
        include_traceback: bool = True
    ) -> Dict[str, Any]:
        """
        Helper para loggear y capturar errores de forma consistente.
        
        Args:
            error: Excepción capturada
            context: Contexto donde ocurrió el error (ej: "enrichment", "batch_extraction")
            urls: URLs afectadas (opcional)
            debug_info: Diccionario de debug donde registrar el error (opcional)
            include_traceback: Si True, incluye traceback completo (excepto para timeouts)
            
        Returns:
            Diccionario con detalles del error
        """
        error_traceback = traceback.format_exc() if include_traceback else None
        is_timeout = "timeout" in str(error).lower()
        
        # Log detallado en consola
        logger.error(f"❌ Error en {context}:")
        if urls:
            logger.error(f"   URLs afectadas: {urls}")
        logger.error(f"   Tipo de error: {type(error).__name__}")
        logger.error(f"   Mensaje: {str(error)}")
        
        if include_traceback and not is_timeout:
            logger.error(f"   Traceback completo:\n{error_traceback}")
        
        # Construir detalles del error
        error_details = {
            "context": context,
            "error": str(error),
            "type": type(error).__name__,
            "timestamp": datetime.now().isoformat(),
            "traceback": error_traceback if (include_traceback and not is_timeout) else None
        }
        
        if urls:
            error_details["urls"] = urls
        
        # Capturar errores detallados del extractor si existen
        if hasattr(self.extractor, '_last_error_details') and self.extractor._last_error_details:
            error_details["llm_error_details"] = self.extractor._last_error_details
            self.extractor._last_error_details = []
        
        # Registrar en debug_info si se proporciona
        if debug_info:
            debug_info.setdefault("llm", {}).setdefault("errors", []).append(error_details)
            debug_info.setdefault("llm", {}).setdefault("total_failed", 0)
            debug_info["llm"]["total_failed"] += 1
        
        return error_details
    
    def _deduplicate_concursos(self, concursos: List[Concurso]) -> List[Concurso]:
        """
        Elimina concursos duplicados basándose en nombre y URL.
        
        Args:
            concursos: Lista de concursos a deduplicar
            
        Returns:
            Lista de concursos únicos
        """
        seen = set()
        unique_concursos = []
        
        for concurso in concursos:
            # Clave única: nombre + URL
            key = (concurso.nombre.lower().strip(), concurso.url.strip())
            if key not in seen:
                seen.add(key)
                unique_concursos.append(concurso)
        
        return unique_concursos

