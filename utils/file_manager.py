"""
Utilidades para guardar y cargar datos localmente
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
import pandas as pd
from pathlib import Path


def ensure_directories():
    """Asegura que los directorios necesarios existan"""
    from config import (
        DATA_DIR, RAW_DIR, PROCESSED_DIR, CACHE_DIR, HISTORY_DIR,
        PREDICTIONS_DIR, DEBUG_SCRAPING_DIR, DEBUG_PREDICTIONS_DIR,
        DEBUG_INDIVIDUAL_PREDICTIONS_DIR, RAW_PAGES_DIR, RAW_PAGES_INDEX_DIR
    )
    for directory in [
        DATA_DIR, RAW_DIR, PROCESSED_DIR, CACHE_DIR, HISTORY_DIR,
        PREDICTIONS_DIR, DEBUG_SCRAPING_DIR, DEBUG_PREDICTIONS_DIR,
        DEBUG_INDIVIDUAL_PREDICTIONS_DIR, RAW_PAGES_DIR, RAW_PAGES_INDEX_DIR
    ]:
        Path(directory).mkdir(parents=True, exist_ok=True)


def _safe_site(site: str) -> str:
    """Normaliza el nombre del sitio para nombres de archivo/carpetas."""
    return site.replace("www.", "").replace(".", "_").replace("/", "_").strip()


def _slugify_url(url: str) -> str:
    """
    Genera un slug determinístico a partir de la URL de un concurso.
    Usa path + query (sanitizado) para minimizar colisiones.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    base = (parsed.path or "/").strip("/")
    query = parsed.query.strip()
    # Reemplazar separadores problemáticos
    slug_base = base.replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "-")
    if not slug_base:
        slug_base = "root"
    if query:
        slug_base = f"{slug_base}_{query}"
    # Fallback: limitar longitud
    if len(slug_base) > 180:
        import hashlib
        slug_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        slug_base = f"{slug_base[:170]}_{slug_hash}"
    return slug_base


def _get_page_cache_paths(site: str, url: str) -> Dict[str, str]:
    """Calcula rutas de archivo para HTML/MD de una URL dada."""
    from config import RAW_PAGES_DIR
    safe_site = _safe_site(site)
    slug = _slugify_url(url)
    site_dir = Path(RAW_PAGES_DIR) / safe_site
    site_dir.mkdir(parents=True, exist_ok=True)
    html_path = site_dir / f"{slug}.html"
    md_path = site_dir / f"{slug}.md"
    return {"html_path": str(html_path), "md_path": str(md_path)}


def _load_page_cache_index(site: str) -> Dict[str, Any]:
    """Carga el índice de cache de páginas para un sitio."""
    from config import RAW_PAGES_INDEX_DIR
    ensure_directories()
    safe_site = _safe_site(site)
    index_path = Path(RAW_PAGES_INDEX_DIR) / f"index_{safe_site}.json"
    if not index_path.exists():
        return {}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_page_cache_index(site: str, index: Dict[str, Any]) -> None:
    """Guarda el índice de cache de páginas para un sitio."""
    from config import RAW_PAGES_INDEX_DIR
    ensure_directories()
    safe_site = _safe_site(site)
    index_path = Path(RAW_PAGES_INDEX_DIR) / f"index_{safe_site}.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def save_page_cache(site: str, url: str, html: str, markdown: str) -> Dict[str, Any]:
    """
    Guarda HTML y Markdown completos de una página individual (sin compresión).
    Si ya existe, sobrescribe el contenido y actualiza el índice.
    """
    ensure_directories()
    paths = _get_page_cache_paths(site, url)
    Path(paths["html_path"]).write_text(html or "", encoding="utf-8")
    Path(paths["md_path"]).write_text(markdown or "", encoding="utf-8")

    index = _load_page_cache_index(site)
    entry = {
        "url": url,
        "html_path": paths["html_path"],
        "markdown_path": paths["md_path"],
        "captured_at": datetime.now().isoformat(),
        "html_size": len(html or ""),
        "markdown_size": len(markdown or ""),
    }
    index[url] = entry
    _save_page_cache_index(site, index)
    return entry


def load_page_cache(site: str, url: str) -> Optional[Dict[str, Any]]:
    """
    Carga HTML y Markdown de cache para una URL dada, si existe.
    Retorna None si no hay entrada o faltan archivos.
    """
    index = _load_page_cache_index(site)
    entry = index.get(url)
    if not entry:
        return None
    html_path = entry.get("html_path")
    md_path = entry.get("markdown_path")
    if not html_path or not md_path:
        return None
    if not Path(html_path).exists() or not Path(md_path).exists():
        return None
    try:
        html = Path(html_path).read_text(encoding="utf-8")
        markdown = Path(md_path).read_text(encoding="utf-8")
        return {
            **entry,
            "html": html,
            "markdown": markdown,
        }
    except Exception:
        return None


def save_results(concursos: List[Dict[str, Any]], filename: Optional[str] = None) -> str:
    """
    Guarda los resultados en un archivo JSON
    
    Args:
        concursos: Lista de diccionarios con información de concursos
        filename: Nombre del archivo (si None, genera uno automático)
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import PROCESSED_DIR
    
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"concursos_{timestamp}.json"
    
    filepath = os.path.join(PROCESSED_DIR, filename)
    
    # Agregar metadata
    data = {
        "metadata": {
            "fecha_extraccion": datetime.now().isoformat(),
            "total_concursos": len(concursos)
        },
        "concursos": concursos
    }
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return filepath


def save_raw_crawl_results(audit_data: Dict[str, Any], batch_num: Optional[int] = None) -> str:
    """
    Guarda resultados crudos de un crawl para auditoría.
    
    Incluye:
    - Markdown enviado al LLM
    - Respuesta cruda del LLM
    - Concursos parseados
    - Metadatos del batch
    
    Args:
        audit_data: Diccionario con datos de auditoría
        batch_num: Número del batch (opcional, para incluir en nombre de archivo)
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import RAW_DIR
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if batch_num is not None:
        filename = f"crawl_raw_batch{batch_num}_{timestamp}.json"
    else:
        filename = f"crawl_raw_{timestamp}.json"
    
    filepath = os.path.join(RAW_DIR, filename)
    
    # Guardar datos de auditoría
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(audit_data, f, ensure_ascii=False, indent=2)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"💾 Resultados crudos guardados en: {filepath}")
    
    return filepath


def load_results(filename: str) -> List[Dict[str, Any]]:
    """
    Carga resultados desde un archivo JSON
    
    Args:
        filename: Nombre del archivo
        
    Returns:
        Lista de concursos
    """
    from config import PROCESSED_DIR
    
    filepath = os.path.join(PROCESSED_DIR, filename)
    
    if not os.path.exists(filepath):
        return []
    
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return data.get("concursos", [])


def export_to_csv(concursos: List[Dict[str, Any]], filename: Optional[str] = None) -> str:
    """
    Exporta los concursos a un archivo CSV
    
    Args:
        concursos: Lista de diccionarios con información de concursos
        filename: Nombre del archivo (si None, genera uno automático)
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import PROCESSED_DIR
    
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"concursos_{timestamp}.csv"
    
    filepath = os.path.join(PROCESSED_DIR, filename)
    
    # Convertir a DataFrame
    df = pd.DataFrame(concursos)
    
    # Guardar CSV
    df.to_csv(filepath, index=False, encoding="utf-8-sig")
    
    return filepath


def _optimize_debug_info(debug_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Optimiza el debug_info para hacerlo más conciso sin perder información importante.
    
    Elimina:
    - Contenido completo de markdown (solo mantiene previews)
    - Contenido raw completo (solo mantiene metadata)
    - Información redundante
    
    Agrega:
    - Resumen de predicciones con detalles sobre fuentes
    - Estadísticas de "Concursos anteriores"
    
    Args:
        debug_data: Diccionario con información de debug
        
    Returns:
        Diccionario optimizado
    """
    optimized = {}
    
    # 1. Resumen ejecutivo (ya existe, mantenerlo)
    optimized["summary"] = debug_data.get("summary", {})
    
    # 2. Información de ejecución (simplificada)
    execution = debug_data.get("execution", {})
    optimized["execution"] = {
        "start_time": execution.get("start_time"),
        "end_time": execution.get("end_time"),
        "duration_seconds": execution.get("duration_seconds"),
        "urls": execution.get("urls", []),
        "model_name": execution.get("model_name"),
        "config": {
            "batch_size": execution.get("config", {}).get("extraction", {}).get("batch_size"),
            "api_timeout": execution.get("config", {}).get("extraction", {}).get("api_timeout"),
        }
    }
    
    # 3. Estadísticas de scraping (simplificadas)
    scraping = debug_data.get("scraping", {})
    optimized["scraping"] = {
        "pages_scraped": scraping.get("pages_scraped", 0),
        "pages_failed": scraping.get("pages_failed", 0),
        "individual_pages_scraped": scraping.get("individual_pages_scraped", 0),
        "individual_pages_failed": scraping.get("individual_pages_failed", 0),
        "concursos_html_detectados_total": scraping.get("concursos_html_detectados_total", 0),
        "concursos_html_por_pagina": scraping.get("concursos_html_por_pagina", []),
        "total_html_size_mb": round(scraping.get("total_html_size", 0) / (1024 * 1024), 2),
        "total_markdown_size_mb": round(scraping.get("total_markdown_size", 0) / (1024 * 1024), 2),
        "errors_count": len(scraping.get("errors", [])),
        "errors": scraping.get("errors", [])[:10]  # Solo primeros 10 errores
    }
    
    # 4. Estadísticas de LLM (simplificadas)
    llm = debug_data.get("llm", {})
    optimized["llm"] = {
        "batches_processed": llm.get("batches_processed", 0),
        "total_calls": llm.get("total_calls", 0),
        "total_failed": llm.get("total_failed", 0),
        "api_keys_used_count": len(llm.get("api_keys_used", [])),
        "api_keys_used": llm.get("api_keys_used", []),
        "errors_count": len(llm.get("errors", [])),
        "errors": llm.get("errors", [])[:10],  # Solo primeros 10 errores
        "raw_files": llm.get("raw_files", [])
    }
    
    # 5. Estadísticas de extracción
    extraction = debug_data.get("extraction", {})
    optimized["extraction"] = extraction.copy()
    
    # 6. Información de enriquecimiento (simplificada)
    enrichment = debug_data.get("enrichment", {})
    if enrichment:
        optimized["enrichment"] = {
            "date_retry_candidates": enrichment.get("date_retry_candidates", 0),
            "date_retry_attempted": enrichment.get("date_retry_attempted", False),
            "date_retry_success": enrichment.get("date_retry_success", 0),
            "date_retry_remaining": enrichment.get("date_retry_remaining", 0),
            "name_updates": enrichment.get("name_updates", [])
        }
    
    # 7. Información de historial (simplificada)
    history = debug_data.get("history", {})
    if history:
        optimized["history"] = {
            "site": history.get("site"),
            "existing_concursos": history.get("existing_concursos", 0),
            "new_concursos": history.get("new_concursos", 0),
            "total_concursos_in_history": history.get("total_concursos_in_history", 0),
            "history_file": history.get("history_file")
        }
    
    # 8. PREDICCIONES: Información detallada y optimizada
    predictions = debug_data.get("predictions", {})
    optimized["predictions"] = {}
    
    # Estadísticas de predicciones
    filters = predictions.get("filters", [])
    optimized["predictions"]["summary"] = {
        "total_filtered": len(filters),
        "by_source": {},
        "by_reason": {}
    }
    
    # Contar por fuente
    for filter_item in filters:
        source = filter_item.get("source", "unknown")
        reason = filter_item.get("filter_reason", "unknown")
        
        optimized["predictions"]["summary"]["by_source"][source] = \
            optimized["predictions"]["summary"]["by_source"].get(source, 0) + 1
        optimized["predictions"]["summary"]["by_reason"][reason] = \
            optimized["predictions"]["summary"]["by_reason"].get(reason, 0) + 1
    
    # Información sobre "Concursos anteriores" extraídos
    scraping_data = debug_data.get("scraping", {})
    previous_concursos_extracted = scraping_data.get("previous_concursos_extracted", {})
    if previous_concursos_extracted:
        optimized["predictions"]["previous_concursos_extracted"] = {
            "total_urls_with_previous": len(previous_concursos_extracted),
            "total_previous_concursos": sum(previous_concursos_extracted.values()),
            "by_url": previous_concursos_extracted
        }
    
    # Filtros (solo primeros 20 para no hacer el archivo muy grande)
    optimized["predictions"]["filters"] = filters[:20]
    if len(filters) > 20:
        optimized["predictions"]["filters_truncated"] = True
        optimized["predictions"]["total_filters"] = len(filters)
    
    # 9. Warnings (solo primeros 30)
    warnings = debug_data.get("warnings", [])
    optimized["warnings"] = warnings[:30]
    if len(warnings) > 30:
        optimized["warnings_truncated"] = True
        optimized["total_warnings"] = len(warnings)
    
    # 10. Información de contenido (solo metadata, no contenido completo)
    optimized["content"] = {
        "raw_content_available": debug_data.get("raw_content") is not None,
        "processed_file": debug_data.get("processed_content", {}).get("file"),
        "processed_concursos_count": len(debug_data.get("processed_content", {}).get("concursos", [])),
        "individual_pages_count": len(debug_data.get("individual_pages_content", {}))
    }
    
    # 11. Timeouts y configuración (mantener)
    optimized["timeouts"] = debug_data.get("timeouts", {})
    
    # 12. Metadata del archivo
    optimized["debug_file_created_at"] = datetime.now().isoformat()
    
    return optimized


def save_debug_info_scraping(debug_data: Dict[str, Any]) -> str:
    """
    Guarda un archivo de debug optimizado para scraping.
    
    Args:
        debug_data: Diccionario con toda la información de debug
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import DEBUG_SCRAPING_DIR
    
    # Crear directorio de debug si no existe
    Path(DEBUG_SCRAPING_DIR).mkdir(parents=True, exist_ok=True)
    
    # Generar nombre de archivo con hora y minuto
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"debug_scraping_{timestamp}.json"
    filepath = os.path.join(DEBUG_SCRAPING_DIR, filename)
    
    # Optimizar datos de debug
    optimized = _optimize_debug_info(debug_data)
    
    # Guardar archivo
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, indent=2)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"🐛 Archivo de debug de scraping guardado: {filepath}")
    
    return filepath


def save_debug_info_repair(debug_data: Dict[str, Any]) -> str:
    """
    Guarda un archivo de debug específico para procesos de reparación de concursos incompletos.
    
    Este debug se centra en:
    - Concursos detectados con datos faltantes antes de la reparación
    - Concursos que siguieron incompletos después de la reparación
    - Estadísticas básicas del proceso
    """
    ensure_directories()
    
    from config import DATA_DIR
    
    # Directorio dedicado para reparaciones
    repair_dir = os.path.join(DATA_DIR, "debug", "repair")
    Path(repair_dir).mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"debug_repair_{timestamp}.json"
    filepath = os.path.join(repair_dir, filename)
    
    # No aplicamos _optimize_debug_info aquí para mantener toda la información
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(debug_data, f, ensure_ascii=False, indent=2)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"🐛 Archivo de debug de reparación guardado: {filepath}")
    
    return filepath


def save_debug_info_predictions(debug_data: Dict[str, Any]) -> str:
    """
    Guarda un archivo de debug optimizado para predicciones.
    
    Args:
        debug_data: Diccionario con toda la información de debug
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import DEBUG_PREDICTIONS_DIR
    
    # Crear directorio de debug si no existe
    Path(DEBUG_PREDICTIONS_DIR).mkdir(parents=True, exist_ok=True)
    
    # Generar nombre de archivo con hora y minuto
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"debug_predictions_{timestamp}.json"
    filepath = os.path.join(DEBUG_PREDICTIONS_DIR, filename)
    
    # Optimizar datos de debug para predicciones
    optimized = {
        "execution": debug_data.get("execution", {}),
        "scraping": {
            "urls_scraped": debug_data.get("scraping", {}).get("urls_scraped", 0),
            "urls_failed": debug_data.get("scraping", {}).get("urls_failed", 0),
            "previous_concursos_extracted": debug_data.get("scraping", {}).get("previous_concursos_extracted", {})
        },
        "predictions": {
            "total_analyzed": debug_data.get("predictions", {}).get("total_analyzed", 0),
            "successful": debug_data.get("predictions", {}).get("successful", 0),
            "failed": debug_data.get("predictions", {}).get("failed", 0),
            "filtered": debug_data.get("predictions", {}).get("filtered", 0),
            "errors": debug_data.get("predictions", {}).get("errors", [])[:20],  # Limitar errores
            "filters": debug_data.get("predictions", {}).get("filters", [])[:20]  # Limitar filtros
        },
        "stats": debug_data.get("stats", {}),
        "debug_file_created_at": datetime.now().isoformat()
    }
    
    # Guardar archivo
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, indent=2)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"🐛 Archivo de debug de predicciones guardado: {filepath}")
    
    return filepath


def save_debug_info_individual_prediction(debug_data: Dict[str, Any]) -> str:
    """
    Guarda un archivo de debug optimizado para predicciones individuales.
    
    Args:
        debug_data: Diccionario con información de debug de una predicción individual
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import DEBUG_INDIVIDUAL_PREDICTIONS_DIR
    
    # Crear directorio de debug si no existe
    Path(DEBUG_INDIVIDUAL_PREDICTIONS_DIR).mkdir(parents=True, exist_ok=True)
    
    # Generar nombre de archivo con timestamp completo (incluyendo segundos)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    concurso_nombre = debug_data.get("concurso", {}).get("nombre", "unknown")
    # Limpiar nombre del concurso para usar en nombre de archivo
    safe_nombre = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in concurso_nombre)[:50]
    safe_nombre = safe_nombre.replace(' ', '_')
    filename = f"debug_individual_{safe_nombre}_{timestamp}.json"
    filepath = os.path.join(DEBUG_INDIVIDUAL_PREDICTIONS_DIR, filename)
    
    # Optimizar datos de debug para predicción individual
    optimized = {
        "execution": {
            "start_time": debug_data.get("start_time"),
            "end_time": debug_data.get("end_time"),
            "duration_seconds": debug_data.get("duration_seconds"),
            "concurso": debug_data.get("concurso", {})
        },
        "scraping": {
            "success": debug_data.get("scraping", {}).get("success", False),
            "url": debug_data.get("scraping", {}).get("url", ""),
            "error": debug_data.get("scraping", {}).get("error")
        },
        "previous_concursos": {
            "extracted_count": debug_data.get("previous_concursos", {}).get("extracted_count", 0),
            "items": debug_data.get("previous_concursos", {}).get("items", [])
        },
        "prediction": {
            "success": debug_data.get("prediction", {}).get("success", False),
            "fecha_predicha": debug_data.get("prediction", {}).get("fecha_predicha"),
            "confianza": debug_data.get("prediction", {}).get("confianza"),
            "justificacion": debug_data.get("prediction", {}).get("justificacion"),
            "error": debug_data.get("prediction", {}).get("error"),
            "filtered": debug_data.get("prediction", {}).get("filtered", False),
            "filter_reason": debug_data.get("prediction", {}).get("filter_reason")
        },
        "debug_file_created_at": datetime.now().isoformat()
    }
    
    # Guardar archivo
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, indent=2)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"🐛 Archivo de debug de predicción individual guardado: {filepath}")
    
    return filepath


def save_debug_info(debug_data: Dict[str, Any]) -> str:
    """
    Guarda un archivo de debug optimizado con información relevante de una ejecución.
    
    Incluye:
    - Resumen ejecutivo
    - Estadísticas de scraping, LLM y extracción
    - Información detallada sobre predicciones (especialmente "Concursos anteriores")
    - Errores y warnings (limitados)
    - Links a archivos generados
    
    Args:
        debug_data: Diccionario con toda la información de debug
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import DATA_DIR
    
    # Crear directorio de debug si no existe
    debug_dir = os.path.join(DATA_DIR, "debug")
    Path(debug_dir).mkdir(parents=True, exist_ok=True)
    
    # Generar nombre de archivo con hora y minuto
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"debug_{timestamp}.json"
    filepath = os.path.join(debug_dir, filename)
    
    # Agregar resumen ejecutivo al inicio (si no existe)
    if "summary" not in debug_data:
        summary = {
            "status": "success" if len(debug_data.get("llm", {}).get("errors", [])) == 0 else "partial",
            "duration_seconds": debug_data.get("execution", {}).get("duration_seconds", 0),
            "pages_scraped": debug_data.get("scraping", {}).get("pages_scraped", 0),
            "pages_failed": debug_data.get("scraping", {}).get("pages_failed", 0),
            "batches_processed": debug_data.get("llm", {}).get("batches_processed", 0),
            "llm_calls": debug_data.get("llm", {}).get("total_calls", 0),
            "llm_failed": debug_data.get("llm", {}).get("total_failed", 0),
            "concursos_found": debug_data.get("extraction", {}).get("concursos_found", 0),
            "concursos_final": debug_data.get("extraction", {}).get("concursos_after_dedup", 0),
            "duplicates_removed": debug_data.get("extraction", {}).get("duplicates_removed", 0),
            "total_errors": len(debug_data.get("scraping", {}).get("errors", [])) + len(debug_data.get("llm", {}).get("errors", [])),
            "total_warnings": len(debug_data.get("warnings", []))
        }
        debug_data["summary"] = summary
    
    # Optimizar debug_info antes de guardar
    optimized_data = _optimize_debug_info(debug_data)
    
    # Guardar archivo de debug optimizado
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(optimized_data, f, ensure_ascii=False, indent=2)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"🐛 Archivo de debug optimizado guardado en: {filepath}")
    
    return filepath


def save_predictions(site: str, predictions: List[Dict[str, Any]]) -> str:
    """
    Guarda predicciones de concursos en un archivo JSON único por sitio.
    
    Args:
        site: Nombre del sitio (ej: "anid.cl")
        predictions: Lista de diccionarios con predicciones
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import PREDICTIONS_DIR
    
    # Normalizar nombre del sitio para nombre de archivo
    safe_site = site.replace(".", "_").replace("/", "_")
    filename = f"predictions_{safe_site}.json"
    filepath = os.path.join(PREDICTIONS_DIR, filename)
    
    # Cargar predicciones existentes si hay
    existing_predictions = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing_predictions = data.get("predictions", [])
        except (IOError, OSError, json.JSONDecodeError):
            # Si el archivo está corrupto o no se puede leer, empezar con lista vacía
            pass
    
    # Combinar predicciones (evitar duplicados por URL)
    existing_urls = {p.get("concurso_url") for p in existing_predictions if p.get("concurso_url")}
    
    for pred in predictions:
        if pred.get("concurso_url") not in existing_urls:
            existing_predictions.append(pred)
            existing_urls.add(pred.get("concurso_url"))
    
    # Guardar
    data = {
        "site": site,
        "last_updated": datetime.now().isoformat(),
        "total_predictions": len(existing_predictions),
        "predictions": existing_predictions
    }
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"🔮 Predicciones guardadas para {site}: {len(existing_predictions)} predicciones")
    
    return filepath


def save_unpredictable_concursos(site: str, unpredictable_concursos: List[Dict[str, Any]]) -> str:
    """
    Guarda concursos no predecibles en un archivo JSON único por sitio.
    
    Args:
        site: Nombre del sitio (ej: "anid.cl")
        unpredictable_concursos: Lista de diccionarios con concursos no predecibles
        
    Returns:
        Ruta del archivo guardado
    """
    ensure_directories()
    
    from config import PREDICTIONS_DIR
    
    # Normalizar nombre del sitio para nombre de archivo
    safe_site = site.replace(".", "_").replace("/", "_")
    filename = f"unpredictable_{safe_site}.json"
    filepath = os.path.join(PREDICTIONS_DIR, filename)
    
    # Cargar concursos no predecibles existentes si hay
    existing_unpredictable = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing_unpredictable = data.get("unpredictable_concursos", [])
        except (IOError, OSError, json.JSONDecodeError):
            # Si el archivo está corrupto o no se puede leer, empezar con lista vacía
            pass
    
    # Combinar (evitar duplicados por URL)
    existing_urls = {u.get("concurso_url") for u in existing_unpredictable if u.get("concurso_url")}
    
    for unpred in unpredictable_concursos:
        if unpred.get("concurso_url") not in existing_urls:
            existing_unpredictable.append(unpred)
            existing_urls.add(unpred.get("concurso_url"))
    
    # Guardar
    data = {
        "site": site,
        "last_updated": datetime.now().isoformat(),
        "total_unpredictable": len(existing_unpredictable),
        "unpredictable_concursos": existing_unpredictable
    }
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"⚠️ Concursos no predecibles guardados para {site}: {len(existing_unpredictable)} concursos")
    
    return filepath


def load_unpredictable_concursos(site: str) -> List[Dict[str, Any]]:
    """
    Carga concursos no predecibles de un sitio.
    
    Args:
        site: Nombre del sitio (ej: "anid.cl")
        
    Returns:
        Lista de diccionarios con concursos no predecibles
    """
    ensure_directories()
    
    from config import PREDICTIONS_DIR
    
    safe_site = site.replace(".", "_").replace("/", "_")
    filename = f"unpredictable_{safe_site}.json"
    filepath = os.path.join(PREDICTIONS_DIR, filename)
    
    if not os.path.exists(filepath):
        return []
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("unpredictable_concursos", [])
    except (IOError, OSError, json.JSONDecodeError) as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error al cargar concursos no predecibles de {site}: {e}", exc_info=True)
        return []


def load_predictions(site: str) -> List[Dict[str, Any]]:
    """
    Carga predicciones de un sitio.
    
    Args:
        site: Nombre del sitio
        
    Returns:
        Lista de predicciones
    """
    from config import PREDICTIONS_DIR
    
    safe_site = site.replace(".", "_").replace("/", "_")
    filename = f"predictions_{safe_site}.json"
    filepath = os.path.join(PREDICTIONS_DIR, filename)
    
    if not os.path.exists(filepath):
        return []
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("predictions", [])
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error al cargar predicciones de {site}: {e}")
        return []


def delete_prediction(site: str, concurso_url: str) -> bool:
    """
    Elimina una predicción específica por URL del concurso.
    
    Args:
        site: Nombre del sitio (ej: "anid.cl")
        concurso_url: URL del concurso cuya predicción se desea eliminar
        
    Returns:
        True si se eliminó exitosamente, False en caso contrario
    """
    ensure_directories()
    
    from config import PREDICTIONS_DIR
    import logging
    logger = logging.getLogger(__name__)
    
    safe_site = site.replace(".", "_").replace("/", "_")
    filename = f"predictions_{safe_site}.json"
    filepath = os.path.join(PREDICTIONS_DIR, filename)
    
    if not os.path.exists(filepath):
        logger.warning(f"⚠️ No se encontró archivo de predicciones para {site}")
        return False
    
    try:
        # Cargar predicciones existentes
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        predictions = data.get("predictions", [])
        original_count = len(predictions)
        
        # Filtrar la predicción a eliminar
        predictions = [p for p in predictions if p.get("concurso_url", "").strip() != concurso_url.strip()]
        
        if len(predictions) == original_count:
            logger.warning(f"⚠️ No se encontró predicción para URL {concurso_url} en {site}")
            return False
        
        # Actualizar y guardar
        data["predictions"] = predictions
        data["total_predictions"] = len(predictions)
        data["last_updated"] = datetime.now().isoformat()
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"🗑️ Predicción eliminada para {site}: {concurso_url}")
        return True
        
    except Exception as e:
        logger.error(f"Error al eliminar predicción de {site}: {e}", exc_info=True)
        return False


def delete_predictions_by_urls(site: str, urls: List[str]) -> int:
    """
    Elimina múltiples predicciones por lista de URLs.
    
    Args:
        site: Nombre del sitio (ej: "anid.cl")
        urls: Lista de URLs de concursos cuyas predicciones se desean eliminar
        
    Returns:
        Número de predicciones eliminadas
    """
    ensure_directories()
    
    from config import PREDICTIONS_DIR
    import logging
    logger = logging.getLogger(__name__)
    
    safe_site = site.replace(".", "_").replace("/", "_")
    filename = f"predictions_{safe_site}.json"
    filepath = os.path.join(PREDICTIONS_DIR, filename)
    
    if not os.path.exists(filepath):
        logger.warning(f"⚠️ No se encontró archivo de predicciones para {site}")
        return 0
    
    try:
        # Cargar predicciones existentes
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        predictions = data.get("predictions", [])
        original_count = len(predictions)
        
        # Normalizar URLs para comparación
        urls_set = {url.strip() for url in urls}
        
        # Filtrar predicciones a eliminar
        predictions = [p for p in predictions if p.get("concurso_url", "").strip() not in urls_set]
        
        deleted_count = original_count - len(predictions)
        
        if deleted_count == 0:
            logger.warning(f"⚠️ No se encontraron predicciones para las URLs proporcionadas en {site}")
            return 0
        
        # Actualizar y guardar
        data["predictions"] = predictions
        data["total_predictions"] = len(predictions)
        data["last_updated"] = datetime.now().isoformat()
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"🗑️ {deleted_count} predicción(es) eliminada(s) para {site}")
        return deleted_count
        
    except Exception as e:
        logger.error(f"Error al eliminar predicciones de {site}: {e}", exc_info=True)
        return 0


def clear_unpredictable_concursos(site: str) -> bool:
    """
    Limpia todos los concursos no predecibles de un sitio.
    
    Args:
        site: Nombre del sitio (ej: "anid.cl")
        
    Returns:
        True si se limpió exitosamente, False en caso contrario
    """
    ensure_directories()
    
    from config import PREDICTIONS_DIR
    import logging
    logger = logging.getLogger(__name__)
    
    safe_site = site.replace(".", "_").replace("/", "_")
    filename = f"unpredictable_{safe_site}.json"
    filepath = os.path.join(PREDICTIONS_DIR, filename)
    
    if not os.path.exists(filepath):
        logger.warning(f"⚠️ No se encontró archivo de concursos no predecibles para {site}")
        return False
    
    try:
        # Cargar para obtener el conteo
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        count = len(data.get("unpredictable_concursos", []))
        
        # Limpiar concursos no predecibles
        data["unpredictable_concursos"] = []
        data["total_unpredictable"] = 0
        data["last_updated"] = datetime.now().isoformat()
        
        # Guardar
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"🗑️ Todos los concursos no predecibles eliminados para {site}: {count} concursos")
        return True
        
    except Exception as e:
        logger.error(f"Error al limpiar concursos no predecibles de {site}: {e}", exc_info=True)
        return False


def clear_predictions(site: str) -> bool:
    """
    Limpia todas las predicciones de un sitio.
    
    Args:
        site: Nombre del sitio (ej: "anid.cl")
        
    Returns:
        True si se limpió exitosamente, False en caso contrario
    """
    ensure_directories()
    
    from config import PREDICTIONS_DIR
    import logging
    logger = logging.getLogger(__name__)
    
    safe_site = site.replace(".", "_").replace("/", "_")
    filename = f"predictions_{safe_site}.json"
    filepath = os.path.join(PREDICTIONS_DIR, filename)
    
    if not os.path.exists(filepath):
        logger.warning(f"⚠️ No se encontró archivo de predicciones para {site}")
        return False
    
    try:
        # Cargar para obtener el conteo
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        count = len(data.get("predictions", []))
        
        # Limpiar predicciones
        data["predictions"] = []
        data["total_predictions"] = 0
        data["last_updated"] = datetime.now().isoformat()
        
        # Guardar
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"🗑️ Todas las predicciones eliminadas para {site}: {count} predicción(es)")
        return True
        
    except Exception as e:
        logger.error(f"Error al limpiar predicciones de {site}: {e}", exc_info=True)
        return False
