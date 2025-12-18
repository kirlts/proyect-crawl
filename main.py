"""
Aplicación Streamlit para el MVP de Buscador de Oportunidades de Financiamiento

Interfaz rediseñada: separa scraping de visualización de información
"""

import streamlit as st
import pandas as pd
from urllib.parse import urlparse
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import os

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Silenciar loggers ruidosos
logging.getLogger("watchdog").setLevel(logging.WARNING)
logging.getLogger("watchdog.observers").setLevel(logging.WARNING)
logging.getLogger("watchdog.observers.inotify_buffer").setLevel(logging.WARNING)
logging.getLogger("crawl4ai").setLevel(logging.WARNING)
logging.getLogger("selenium").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Imports locales
from config import SEED_URLS, CRAWLER_CONFIG, GEMINI_CONFIG, EXTRACTION_CONFIG, AVAILABLE_MODELS
from config.sites import get_site_name_for_history
from services import ExtractionService
from models import Concurso
from utils import (
    save_results, 
    export_to_csv, 
    APIKeyManager,
    load_predictions,
    delete_prediction,
    delete_predictions_by_urls,
    load_unpredictable_concursos,
    save_unpredictable_concursos,
    clear_predictions,
    clear_unpredictable_concursos,
    HistoryManager
)
from utils.scraping_state import (
    save_scraping_state,
    load_scraping_state,
    clear_scraping_state,
    set_should_stop,
    get_should_stop,
    is_scraping_in_progress
)
from config import PROCESSED_DIR, PREDICTIONS_DIR

# Configurar página
st.set_page_config(
    page_title="Buscador de Oportunidades de Financiamiento",
    page_icon="🔍",
    layout="wide"
)

# Inicializar estado de sesión
if "concursos" not in st.session_state:
    st.session_state.concursos = {}
if "processing" not in st.session_state:
    st.session_state.processing = False

# Limpiar estado persistente al iniciar el sistema, PERO solo si es antiguo
# Si el estado es muy reciente (< 5 minutos), probablemente hay un scraping en curso
# y NO debemos limpiarlo
import time
persistent_state_check = load_scraping_state()
if persistent_state_check:
    timestamp = persistent_state_check.get("timestamp", 0)
    # Si el estado es muy antiguo (> 5 minutos), limpiarlo
    if time.time() - timestamp > 300:  # 5 minutos
        clear_scraping_state()
        persistent_state_check = None
    else:
        # Estado reciente: probablemente hay scraping en curso, sincronizar
        st.session_state.scraping_in_progress = persistent_state_check.get("in_progress", False)
        st.session_state.should_stop = persistent_state_check.get("should_stop", False)
        st.session_state.current_scraping_site = persistent_state_check.get("site", "")
else:
    clear_scraping_state()  # No hay estado, asegurar limpieza

# Inicializar estado de sesión
if "should_stop" not in st.session_state:
    st.session_state.should_stop = False
if "scraping_in_progress" not in st.session_state:
    st.session_state.scraping_in_progress = False
if "current_scraping_site" not in st.session_state:
    st.session_state.current_scraping_site = ""

# Nota: El estado persistente se creará nuevamente cuando se inicie un scraping
# y se limpiará cuando termine o se cancele

if "api_key_manager" not in st.session_state:
    st.session_state.api_key_manager = APIKeyManager()
if "history_manager" not in st.session_state:
    st.session_state.history_manager = HistoryManager()


def calculate_estado_from_fechas(fecha_cierre: Optional[str], fecha_apertura: Optional[str], estado_guardado: Optional[str] = None) -> Optional[str]:
    """
    Calcula el estado de un concurso basándose en las fechas de forma determinística.
    
    Args:
        fecha_cierre: Fecha de cierre en formato YYYY-MM-DD
        fecha_apertura: Fecha de apertura en formato YYYY-MM-DD
        estado_guardado: Estado guardado en historial (solo se respeta si es "Suspendido")
        
    Returns:
        Estado calculado: "Abierto", "Cerrado", "Próximo", "Suspendido" o None
    """
    from utils.date_parser import parse_date, is_past_date
    from datetime import datetime
    
    # Si el estado guardado es "Suspendido", mantenerlo (no recalcular)
    if estado_guardado == "Suspendido":
        return "Suspendido"
    
    # Calcular estado basándose en fechas
    if fecha_cierre:
        parsed_cierre = parse_date(fecha_cierre)
        if parsed_cierre:
            if parsed_cierre < datetime.now():
                return "Cerrado"
            else:
                return "Abierto"
        # Fallback: usar is_past_date si parse_date falla
        elif is_past_date(fecha_cierre):
            return "Cerrado"
        else:
            return "Abierto"
    elif fecha_apertura:
        parsed_apertura = parse_date(fecha_apertura)
        if parsed_apertura and parsed_apertura > datetime.now():
            return "Próximo"
        else:
            return "Abierto"
    
    # Si no hay fechas, retornar el estado guardado o None
    return estado_guardado


def load_concursos_from_site(site: str) -> List[Dict[str, Any]]:
    """
    Carga concursos desde el historial de un sitio.
    Recalcula el estado basándose en las fechas de forma determinística.
    
    Args:
        site: Nombre del sitio (ej: "anid.cl")
        
    Returns:
        Lista de concursos
    """
    history = st.session_state.history_manager.load_history(site)
    
    # Corregir automáticamente concursos suspendidos por URL (solo una vez por sesión)
    fix_key = f"fixed_suspended_{site}"
    if fix_key not in st.session_state:
        fix_result = st.session_state.history_manager.fix_suspended_concursos_by_url(site)
        if fix_result["concursos_corregidos"] > 0:
            logger.info(
                f"✅ Corregidos {fix_result['concursos_corregidos']} concursos suspendidos "
                f"por URL en {site}"
            )
            # Recargar historial después de la corrección
            history = st.session_state.history_manager.load_history(site)
        st.session_state[fix_key] = True
    
    concursos = []
    
    for hist_concurso in history.get("concursos", []):
        # Obtener la versión más reciente
        versions = hist_concurso.get("versions", [])
        if versions:
            latest = versions[-1]
            fecha_apertura = latest.get("fecha_apertura")
            fecha_cierre = latest.get("fecha_cierre")
            estado_guardado = hist_concurso.get("estado") or latest.get("estado")
            
            # Recalcular estado basándose en las fechas (determinístico)
            estado_calculado = calculate_estado_from_fechas(
                fecha_cierre=fecha_cierre,
                fecha_apertura=fecha_apertura,
                estado_guardado=estado_guardado
            )
            
            concurso = {
                "nombre": hist_concurso.get("nombre"),
                "url": hist_concurso.get("url"),
                "organismo": hist_concurso.get("organismo"),
                "fecha_apertura": fecha_apertura,
                "fecha_cierre": fecha_cierre,
                "estado": estado_calculado,  # Estado recalculado determinísticamente
                "financiamiento": hist_concurso.get("financiamiento") or latest.get("financiamiento"),
                "descripcion": hist_concurso.get("descripcion") or latest.get("descripcion"),
                "subdireccion": hist_concurso.get("subdireccion") or latest.get("subdireccion"),
                "first_seen": hist_concurso.get("first_seen"),
                "last_seen": hist_concurso.get("last_seen"),
                "fuente": site
            }
            concursos.append(concurso)
    
    return concursos


def test_gemini_connection(api_key: str, model_name: str) -> tuple[bool, str]:
    """
    Prueba la conexión con Gemini usando una API key y modelo específicos.
    
    Args:
        api_key: API key a probar
        model_name: Nombre del modelo a usar
        
    Returns:
        Tupla (éxito, mensaje)
    """
    try:
        import requests
        
        # Crear un key manager temporal para el test
        temp_key_manager = APIKeyManager()
        temp_key_manager.add_key(api_key)
        
        # Hacer una llamada de prueba simple directamente vía REST API
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        payload = {
            "contents": [{
                "parts": [{"text": "Responde solo con 'OK'"}]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 10
            }
        }
        params = {"key": api_key}
        
        response = requests.post(url, json=payload, params=params, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if "candidates" in result and len(result["candidates"]) > 0:
                return (True, f"✅ Conexión exitosa con {model_name}")
            else:
                return (False, f"❌ Respuesta inesperada de la API")
        else:
            error_data = response.json() if response.content else {}
            error_msg = error_data.get("error", {}).get("message", f"HTTP {response.status_code}")
            return (False, f"❌ Error: {error_msg}")
    except Exception as e:
        return (False, f"❌ Error: {str(e)}")


# ========== INTERFAZ PRINCIPAL ==========

st.title("🔍 Buscador de Oportunidades de Financiamiento")
st.caption("Repositorio de información sobre concursos de financiamiento para investigación académica en Chile")

# Tabs principales: Visualización, Explorar, Predicciones, Scraping y Manuales
tab0, tab1, tab2, tab3, tab4 = st.tabs([
    "👁️ Visualización",
    "📚 Explorar Concursos",
    "🔮 Predicciones",
    "⚙️ Scraping y Configuración",
    "📝 Concursos Manuales"
])

# ========== TAB 1: EXPLORAR CONCURSOS ==========
# ========== TAB 0: VISUALIZACIÓN (solo lectura) ==========
with tab0:
    st.header("👁️ Visualización unificada")
    st.caption("Lista de todos los concursos (todas las fuentes) con filtros, sin acciones destructivas.")
    
    # Cargar todos los concursos de todos los sitios
    all_items = []
    site_map = {
        "ANID": "anid.cl",
        "Centro Estudios MINEDUC": "centroestudios.mineduc.cl",
        "CNA": "cnachile.cl",
        "DFI MINEDUC": "dfi.mineduc.cl",
        "Manual": "manual.local",
    }
    for display, site_name in site_map.items():
        concursos_site = load_concursos_from_site(site_name)
        for c in concursos_site:
            c_copy = c.copy()
            c_copy["fuente"] = display
            all_items.append(c_copy)
    
    # Cargar todas las predicciones de todos los sitios
    predictions_map = {}  # URL -> predicción
    all_predictions = []  # Lista de todas las predicciones con info del concurso
    for display, site_name in site_map.items():
        predictions = load_predictions(site_name)
        for pred in predictions:
            url = pred.get("concurso_url", "")
            if url:
                predictions_map[url] = pred
                # Buscar el concurso correspondiente
                concurso = next((c for c in all_items if c.get("url") == url), None)
                if concurso:
                    all_predictions.append({
                        "prediccion": pred,
                        "concurso": concurso
                    })
    
    # Mostrar predicciones cercanas (dentro del próximo mes)
    from datetime import datetime, timedelta
    today = datetime.now().date()
    next_month = today + timedelta(days=30)
    
    cercanas = []
    for item in all_predictions:
        fecha_predicha_str = item["prediccion"].get("fecha_predicha", "")
        if fecha_predicha_str:
            try:
                fecha_predicha = datetime.strptime(fecha_predicha_str, "%Y-%m-%d").date()
                if today <= fecha_predicha <= next_month:
                    cercanas.append(item)
            except (ValueError, TypeError):
                pass
    
    if cercanas:
        st.subheader("📅 Predicciones cercanas (próximo mes)")
        st.caption(f"Predicciones que se contemplan para dentro del próximo mes ({len(cercanas)} encontradas)")
        
        # Ordenar por fecha predicha
        cercanas.sort(key=lambda x: datetime.strptime(x["prediccion"].get("fecha_predicha", "9999-12-31"), "%Y-%m-%d").date())
        
        cercanas_data = []
        for item in cercanas:
            pred = item["prediccion"]
            c = item["concurso"]
            fecha_predicha = pred.get("fecha_predicha", "")
            cercanas_data.append({
                "Nombre": c.get("nombre", ""),
                "Organismo": c.get("organismo", ""),
                "Fecha Predicha": fecha_predicha,
                "Estado Actual": c.get("estado", ""),
                "Fuente": c.get("fuente", ""),
                "URL": c.get("url", ""),
            })
        
        st.dataframe(
            pd.DataFrame(cercanas_data),
            width='stretch',
            hide_index=True,
            column_config={
                "URL": st.column_config.LinkColumn("URL")
            },
        )
        st.divider()
    
    if not all_items:
        st.info("No hay concursos cargados aún. Ejecuta scraping o agrega manuales.")
    else:
        
        # Preparar filtros
        estados = sorted({c.get("estado", "") for c in all_items if c.get("estado")})
        organismos = sorted({c.get("organismo", "") for c in all_items if c.get("organismo")})
        subdirs = sorted({(c.get("subdireccion") or "").strip() for c in all_items if c.get("subdireccion")})
        fuentes = sorted({c.get("fuente", "") for c in all_items if c.get("fuente")})
        
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            filtro_estado = st.selectbox("Estado", options=["(todos)"] + estados, index=0, key="vis_estado")
        with col_b:
            filtro_org = st.selectbox("Organismo", options=["(todos)"] + organismos, index=0, key="vis_org")
        with col_c:
            filtro_fuente = st.selectbox("Fuente", options=["(todas)"] + fuentes, index=0, key="vis_fuente")
        
        col_d, col_e, col_f = st.columns(3)
        with col_d:
            filtro_subdir = st.selectbox("Subdirección", options=["(todas)"] + subdirs, index=0, key="vis_subdir")
        with col_e:
            filtro_texto = st.text_input("Buscar por nombre", key="vis_buscar")
        with col_f:
            filtro_prediccion = st.selectbox(
                "Predicción", 
                options=["(todas)", "Con predicción", "Sin predicción"], 
                index=0, 
                key="vis_prediccion"
            )
        
        filtrados = all_items
        if filtro_estado != "(todos)":
            filtrados = [c for c in filtrados if c.get("estado") == filtro_estado]
        if filtro_org != "(todos)":
            filtrados = [c for c in filtrados if c.get("organismo") == filtro_org]
        if filtro_fuente != "(todas)":
            filtrados = [c for c in filtrados if c.get("fuente") == filtro_fuente]
        if filtro_subdir != "(todas)":
            filtrados = [c for c in filtrados if (c.get("subdireccion") or "").strip() == filtro_subdir]
        if filtro_texto:
            t = filtro_texto.lower().strip()
            filtrados = [c for c in filtrados if t in (c.get("nombre", "").lower())]
        if filtro_prediccion == "Con predicción":
            filtrados = [c for c in filtrados if c.get("url", "") in predictions_map]
        elif filtro_prediccion == "Sin predicción":
            filtrados = [c for c in filtrados if c.get("url", "") not in predictions_map]
        
        st.info(f"Mostrando {len(filtrados)} concursos filtrados (de {len(all_items)}).")
        
        table_data = []
        for c in filtrados:
            url = c.get("url", "")
            pred = predictions_map.get(url)
            fecha_predicha = pred.get("fecha_predicha", "") if pred else ""
            
            table_data.append({
                "Nombre": c.get("nombre", ""),
                "Estado": c.get("estado", ""),
                "Organismo": c.get("organismo", ""),
                "Subdirección": c.get("subdireccion", ""),
                "Fuente": c.get("fuente", ""),
                "Fecha Apertura": c.get("fecha_apertura", ""),
                "Fecha Cierre": c.get("fecha_cierre", ""),
                "Predicción": fecha_predicha,
                "URL": url,
            })
        st.dataframe(
            pd.DataFrame(table_data),
            width='stretch',
            hide_index=True,
            column_config={
                "URL": st.column_config.LinkColumn("URL")
            },
        )

# ========== TAB 1: EXPLORAR CONCURSOS ==========
with tab1:
    st.header("📚 Explorar Concursos por Sitio")
    
    # Verificar si se completó un scraping recientemente y mostrar notificación
    if "last_scraping_completed" in st.session_state:
        last_completed = st.session_state.get("last_scraping_completed")
        if last_completed:
            st.info("🔄 Los datos se han actualizado automáticamente tras el último scraping")
            # Limpiar el flag para evitar mostrar el mensaje repetidamente
            del st.session_state.last_scraping_completed
    
    # Selector de sitio y botón de actualización
    col1, col2 = st.columns([3, 1])
    with col1:
        available_sites = list(SEED_URLS.keys())
        selected_site = st.selectbox(
            "Seleccionar sitio:",
            options=available_sites,
            help="Selecciona un sitio para ver sus concursos disponibles"
        )
    with col2:
        st.write("")  # Espaciado
        st.write("")  # Espaciado
        if st.button("🔄 Actualizar", help="Recargar los datos del sitio seleccionado", key="refresh_button"):
            # Limpiar cache del historial para forzar recarga
            if hasattr(st.session_state.history_manager, '_cache'):
                st.session_state.history_manager._cache.clear()
            st.rerun()
    
    if selected_site:
        # Determinar nombre del sitio para historial
        site_name = None
        if selected_site == "ANID":
            site_name = "anid.cl"
        elif selected_site == "Centro Estudios MINEDUC":
            site_name = "centroestudios.mineduc.cl"
        elif selected_site == "CNA":
            site_name = "cnachile.cl"
        elif selected_site == "DFI MINEDUC":
            site_name = "dfi.mineduc.cl"
        
        if site_name:
            # Cargar concursos del sitio
            concursos = load_concursos_from_site(site_name)
            
            # Cargar predicciones
            predictions = load_predictions(site_name)
            
            # Detectar concursos con datos esenciales incompletos en el historial
            incomplete_entries = st.session_state.history_manager.find_incomplete_concurso_urls(site_name)
            incomplete_count = len(incomplete_entries)
            
            if incomplete_count > 0:
                st.warning(
                    f"⚠️ Hay {incomplete_count} concursos con datos incompletos "
                    f"(nombre sin título o estado/fechas vacíos) en el historial de {site_name}."
                )
            else:
                st.success("✅ Todos los concursos de este sitio tienen nombre, estado y fechas de apertura/cierre.")
            
            # Botón para revisar y reparar concursos incompletos
            from utils import save_debug_info_repair  # Import local para evitar ciclos
            from config import SEED_URLS, EXTRACTION_CONFIG, GEMINI_CONFIG
            
            if st.button(
                "🩺 Revisar y reparar concursos incompletos",
                disabled=incomplete_count == 0,
                help="Scrapea solo las páginas problemáticas y usa el LLM para intentar completar nombre, estado y fechas faltantes.",
                key="repair_incomplete_concursos_btn"
            ):
                with st.spinner("Revisando y reparando concursos incompletos..."):
                    from datetime import datetime
                    start_time = datetime.now()
                    
                    # Guardar snapshot inicial de concursos incompletos (solo URLs y campos clave)
                    initial_incomplete = incomplete_entries
                    
                    # Extraer solo las URLs de los concursos incompletos
                    incomplete_urls = [entry["url"] for entry in incomplete_entries if entry.get("url")]
                    
                    if not incomplete_urls:
                        st.error("❌ No se encontraron URLs válidas para reparar.")
                    else:
                        # Crear servicio de extracción
                        key_manager = st.session_state.api_key_manager
                        selected_model = GEMINI_CONFIG.get("model", "gemini-2.5-flash-lite")
                        
                        extraction_service = ExtractionService(
                            api_key_manager=key_manager,
                            model_name=selected_model
                        )
                        
                        # Callbacks para logging y UI
                        repair_status_messages = []
                        def repair_status(msg: str):
                            logger.info(f"[REPAIR] {msg}")
                            repair_status_messages.append(msg)
                        
                        repair_status(
                            f"Iniciando reparación: {incomplete_count} concursos incompletos detectados. "
                            f"Scrapeando {len(incomplete_urls)} URLs problemáticas..."
                        )
                        
                        try:
                            # Usar el nuevo método que solo scrapea las URLs problemáticas
                            repair_stats = extraction_service.repair_incomplete_concursos(
                                site=site_name,
                                incomplete_urls=incomplete_urls,
                                status_callback=repair_status,
                                should_stop_callback=lambda: False
                            )
                        except Exception as e:
                            logger.error(f"Error durante la reparación de concursos incompletos: {e}", exc_info=True)
                            st.error(f"❌ Error durante la reparación: {str(e)}")
                        else:
                            # Limpiar caché del historial para forzar recarga
                            if hasattr(st.session_state.history_manager, "_cache"):
                                st.session_state.history_manager._cache.clear()
                            
                            # Recalcular concursos incompletos después de la reparación
                            final_incomplete = st.session_state.history_manager.find_incomplete_concurso_urls(site_name)
                            final_count = len(final_incomplete)
                            
                            end_time = datetime.now()
                            duration_seconds = (end_time - start_time).total_seconds()
                            
                            # Construir debug específico de reparación con estadísticas del proceso
                            repair_debug = {
                                "execution": {
                                    "mode": "repair_incomplete_concursos",
                                    "site": site_name,
                                    "selected_site_label": selected_site,
                                    "start_time": start_time.isoformat(),
                                    "end_time": end_time.isoformat(),
                                    "duration_seconds": duration_seconds,
                                    "urls_processed": repair_stats.get("urls_processed", 0),
                                    "urls_successful": repair_stats.get("urls_successful", 0),
                                    "urls_failed": repair_stats.get("urls_failed", 0),
                                    "status_messages": repair_status_messages,
                                },
                                "repair": {
                                    "initial_incomplete_count": incomplete_count,
                                    "final_incomplete_count": final_count,
                                    "concursos_repaired": repair_stats.get("concursos_repaired", 0),
                                    "initial_incomplete": initial_incomplete,
                                    "final_incomplete": final_incomplete,
                                    "concursos_still_incomplete": repair_stats.get("concursos_still_incomplete", []),
                                    "errors": repair_stats.get("errors", []),
                                },
                            }
                            
                            try:
                                debug_path = save_debug_info_repair(repair_debug)
                                logger.info(f"[REPAIR] Debug de reparación guardado en {debug_path}")
                            except Exception as debug_error:
                                logger.error(f"No se pudo guardar el debug de reparación: {debug_error}", exc_info=True)
                            
                            # Feedback en UI
                            if final_count == 0:
                                st.success(
                                    f"✅ Reparación completada exitosamente. "
                                    f"Se repararon {repair_stats.get('concursos_repaired', 0)} concurso(s). "
                                    f"Todos los concursos de {site_name} tienen ahora nombre, estado y fechas de apertura/cierre."
                                )
                            else:
                                st.warning(
                                    f"⚠️ Reparación completada parcialmente. "
                                    f"Se repararon {repair_stats.get('concursos_repaired', 0)} de {incomplete_count} concurso(s). "
                                    f"{final_count} concurso(s) siguen con datos incompletos. "
                                    f"Revisa el archivo de debug de reparación para más detalles."
                                )
                            
                            # Mostrar estadísticas adicionales si hay errores
                            if repair_stats.get("errors"):
                                error_count = len(repair_stats["errors"])
                                st.info(f"ℹ️ {error_count} error(es) durante la reparación. Ver detalles en el archivo de debug.")
                            
                            # Forzar recarga de datos en la propia pestaña
                            st.rerun()
            
            if concursos:
                st.success(f"✅ {len(concursos)} concursos encontrados para {selected_site}")
                
                # Métricas
                col1, col2, col3, col4, col5 = st.columns(5)
                with col1:
                    st.metric("Total", len(concursos))
                with col2:
                    abiertos = sum(1 for c in concursos if c.get("estado") == "Abierto")
                    st.metric("Abiertos", abiertos)
                with col3:
                    suspendidos = sum(1 for c in concursos if c.get("estado") == "Suspendido")
                    st.metric("Suspendidos", suspendidos)
                with col4:
                    cerrados = sum(1 for c in concursos if c.get("estado") == "Cerrado")
                    st.metric("Cerrados", cerrados)
                with col5:
                    st.metric("Con Predicción", len(predictions))
                
                # Sección de Gestión
                st.subheader("🗑️ Gestión de Datos")
                with st.expander("⚠️ Limpiar Todos los Concursos", expanded=False):
                    st.warning("⚠️ Esta acción eliminará TODOS los concursos del historial de este sitio. Esta acción no se puede deshacer.")
                    confirm_clear_concursos = st.checkbox(f"Confirmo que quiero eliminar {len(concursos)} concurso(s)", key="confirm_clear_concursos")
                    if st.button("🗑️ Limpiar Todos los Concursos", disabled=not confirm_clear_concursos, type="primary"):
                        if st.session_state.history_manager.clear_history(site_name):
                            st.success(f"✅ {len(concursos)} concurso(s) eliminado(s) del historial")
                            # También eliminar todas las predicciones relacionadas
                            if predictions:
                                deleted_preds = delete_predictions_by_urls(site_name, [c.get("url") for c in concursos])
                                if deleted_preds > 0:
                                    st.info(f"🗑️ {deleted_preds} predicción(es) relacionada(s) también eliminada(s)")
                            st.rerun()
                        else:
                            st.error("❌ Error al limpiar el historial")
                
                # Filtros
                st.subheader("Filtros")
                col1, col2 = st.columns(2)
                with col1:
                    filter_option = st.selectbox(
                        "Filtrar por estado:",
                        ["Todos", "Abiertos Ahora", "Cerrados", "Con Predicción"]
                    )
                with col2:
                    search_term = st.text_input("Buscar por nombre:", "")
                
                # Aplicar filtros
                filtered_concursos = concursos.copy()
                
                if filter_option == "Abiertos Ahora":
                    filtered_concursos = [c for c in filtered_concursos if c.get("estado") == "Abierto"]
                elif filter_option == "Cerrados":
                    filtered_concursos = [c for c in filtered_concursos if c.get("estado") == "Cerrado"]
                elif filter_option == "Con Predicción":
                    pred_urls = {p.get("concurso_url") for p in predictions}
                    filtered_concursos = [c for c in filtered_concursos if c.get("url") in pred_urls]
                
                if search_term:
                    search_lower = search_term.lower()
                    filtered_concursos = [
                        c for c in filtered_concursos
                        if search_lower in c.get("nombre", "").lower()
                    ]
                
                # Mostrar tabla
                if filtered_concursos:
                    st.subheader("📊 Tabla de Concursos")
                    
                    # Ordenar por estado: Abiertos primero, luego Suspendidos, luego Cerrados
                    def estado_sort_key(concurso):
                        estado = concurso.get("estado", "")
                        orden = {
                            "Abierto": 0,
                            "Suspendido": 1,
                            "Cerrado": 2,
                            "Próximo": 3
                        }
                        return orden.get(estado, 99)
                    
                    filtered_concursos_sorted = sorted(
                        filtered_concursos,
                        key=estado_sort_key
                    )
                    
                    # Botones de exportación
                    col1, col2 = st.columns([1, 1])
                    with col1:
                        if st.button("💾 Exportar JSON", use_container_width=True, key="export_json_concursos"):
                            filepath = save_results(filtered_concursos_sorted)
                            st.success(f"Guardado en: {filepath}")
                    with col2:
                        if st.button("📥 Exportar CSV", use_container_width=True, key="export_csv_concursos"):
                            filepath = export_to_csv(filtered_concursos_sorted)
                            st.success(f"Guardado en: {filepath}")
                    
                    # Tabla con st.dataframe
                    import pandas as pd
                    df_data = []
                    for idx, concurso in enumerate(filtered_concursos_sorted):
                        # Buscar predicción para este concurso
                        pred = next((p for p in predictions if p.get("concurso_url") == concurso.get("url")), None)
                        
                        df_data.append({
                            "Nombre": concurso.get("nombre", ""),
                            "Estado": concurso.get("estado", ""),
                            "Fecha Apertura": concurso.get("fecha_apertura", ""),
                            "Fecha Cierre": concurso.get("fecha_cierre", ""),
                            "Próxima Apertura": pred.get("fecha_predicha", "") if pred else "",
                            # Confianza eliminada del modelo; no se muestra
                            "URL": concurso.get("url", "")
                        })
                    
                    df = pd.DataFrame(df_data)
                    st.dataframe(
                        df,
                        width='stretch',
                        hide_index=True,
                        column_config={
                            "URL": st.column_config.LinkColumn("URL")
                        },
                    )
                    
                    st.info("Las acciones destructivas se realizan en la vista de administración.")
                else:
                    st.info("No hay concursos que coincidan con los filtros.")
            else:
                st.info(f"📭 No hay concursos disponibles para {selected_site}. Ejecuta un scraping primero desde la pestaña 'Scraping y Configuración'.")

# ========== TAB 2: PREDICCIONES ==========
with tab2:
    st.header("🔮 Generar Predicciones")
    st.caption("Genera predicciones de fechas de apertura para concursos cerrados basándose en 'Concursos anteriores'")
    
    # Selector de sitio
    available_sites = [s for s in SEED_URLS.keys() if s != "Manual"]
    selected_site = st.selectbox(
        "Seleccionar sitio:",
        options=available_sites,
        help="Selecciona un sitio para generar predicciones",
        key="prediction_site_selector"
    )
    
    if selected_site:
        # Determinar nombre del sitio para historial
        site_name = None
        if selected_site == "ANID":
            site_name = "anid.cl"
        elif selected_site == "Centro Estudios MINEDUC":
            site_name = "centroestudios.mineduc.cl"
        elif selected_site == "CNA":
            site_name = "cnachile.cl"
        elif selected_site == "DFI MINEDUC":
            site_name = "dfi.mineduc.cl"
        
        if site_name:
            # Cargar concursos del sitio para mostrar estadísticas
            concursos = load_concursos_from_site(site_name)
            closed_concursos = [c for c in concursos if c.get("estado") == "Cerrado"]
            suspended_concursos = [c for c in concursos if (c.get("estado") or "").lower() == "suspendido"]
            
            # Estadísticas detalladas
            total_concursos = len(concursos)
            total_cerrados = len(closed_concursos)
            total_suspendidos = len(suspended_concursos)
            
            # Cargar historial para análisis
            history = st.session_state.history_manager.load_history(site_name)
            history_index_by_url = {
                hc.get("url"): hc 
                for hc in history.get("concursos", []) 
                if hc.get("url")
            }
            
            # Analizar concursos cerrados
            cerrados_con_versiones = 0
            cerrados_sin_versiones = 0
            cerrados_sin_historial = 0
            cerrados_no_scrapeados = 0
            cerrados_scrapeados_sin_versiones = 0
            
            # Listas para análisis detallado
            sin_versiones_detalle = []
            
            for concurso in closed_concursos:
                concurso_url = concurso.get("url")
                if not concurso_url:
                    continue
                
                hist_concurso = history_index_by_url.get(concurso_url)
                if not hist_concurso:
                    cerrados_sin_historial += 1
                else:
                    previous_concursos = hist_concurso.get("previous_concursos", [])
                    # Verificar si tiene previous_concursos_updated (indica que se scrapeó)
                    tiene_previous_updated = "previous_concursos_updated" in hist_concurso
                    
                    if previous_concursos:
                        cerrados_con_versiones += 1
                    else:
                        cerrados_sin_versiones += 1
                        # Analizar por qué no tiene versiones
                        if tiene_previous_updated:
                            # Fue scrapeado pero no tiene versiones anteriores (normal)
                            cerrados_scrapeados_sin_versiones += 1
                            sin_versiones_detalle.append({
                                "nombre": concurso.get("nombre", ""),
                                "url": concurso_url,
                                "razon": "Scrapeado pero sin versiones anteriores (normal)"
                            })
                        else:
                            # No fue scrapeado individualmente (problema potencial)
                            cerrados_no_scrapeados += 1
                            sin_versiones_detalle.append({
                                "nombre": concurso.get("nombre", ""),
                                "url": concurso_url,
                                "razon": "No scrapeado individualmente"
                            })
            
            # Mostrar estadísticas detalladas
            with st.expander("📊 Estadísticas Detalladas", expanded=False):
                st.write(f"**Total de concursos:** {total_concursos}")
                st.write(f"**Concursos cerrados:** {total_cerrados}")
                st.write(f"**Concursos suspendidos:** {total_suspendidos}")
                st.write(f"**Cerrados con versiones anteriores:** {cerrados_con_versiones} ✅")
                st.write(f"**Cerrados sin versiones anteriores:** {cerrados_sin_versiones} ⚠️")
                st.write(f"  - Scrapeados pero sin versiones (normal): {cerrados_scrapeados_sin_versiones} ✅")
                st.write(f"  - No scrapeados individualmente: {cerrados_no_scrapeados} ⚠️")
                st.write(f"**Cerrados sin historial:** {cerrados_sin_historial} ❌")
                
                if cerrados_sin_versiones > 0:
                    st.info(
                        f"📊 **Análisis de {cerrados_sin_versiones} concursos sin versiones anteriores:**\n\n"
                        f"- **{cerrados_scrapeados_sin_versiones} fueron scrapeados** pero no tienen versiones anteriores. "
                        f"Esto es **normal** si realmente no tienen una sección 'Concursos anteriores' en su página.\n\n"
                        f"- **{cerrados_no_scrapeados} no fueron scrapeados individualmente**. "
                        f"Esto puede indicar que no se procesaron sus páginas durante el scraping inicial."
                    )
                    
                    # Mostrar muestra de concursos sin versiones
                    if sin_versiones_detalle:
                        st.subheader("🔍 Muestra de concursos sin versiones anteriores (primeros 10)")
                        import pandas as pd
                        df_sin_versiones = pd.DataFrame(sin_versiones_detalle[:10])
                        st.dataframe(
                            df_sin_versiones,
                            width='stretch',
                            hide_index=True,
                            column_config={
                                "url": st.column_config.LinkColumn("URL")
                            }
                        )
            
            # Resumen superior: cerrados + suspendidos deben sumar el total
            st.info(
                f"📊 {total_concursos} concursos totales, "
                f"{total_cerrados} cerrados, "
                f"{total_suspendidos} suspendidos, "
                f"{cerrados_con_versiones} con versiones anteriores disponibles para predicción"
            )
            
            # Filtros
            st.subheader("🔍 Filtros")
            col1, col2 = st.columns(2)
            with col1:
                filter_subdireccion = st.selectbox(
                    "Filtrar por subdirección:",
                    options=["Todas"] + list(set(c.get("subdireccion", "") for c in closed_concursos if c.get("subdireccion"))),
                    key="prediction_filter_subdireccion"
                )
            with col2:
                filter_search = st.text_input(
                    "Buscar por nombre:",
                    key="prediction_filter_search"
                )
            
            # Preparar filtros
            filters = {}
            if filter_subdireccion != "Todas":
                filters["subdireccion"] = filter_subdireccion
            if filter_search:
                filters["search_term"] = filter_search
            
            # Cargar predicciones existentes y concursos no predecibles para filtrar
            existing_predictions = load_predictions(site_name)
            existing_pred_urls = {p.get("concurso_url") for p in existing_predictions}
            
            unpredictable_concursos = load_unpredictable_concursos(site_name)
            unpredictable_urls = {u.get("concurso_url") for u in unpredictable_concursos}
            
            # Cargar historial para verificar qué concursos tienen versiones previas
            history = st.session_state.history_manager.load_history(site_name)
            
            # Crear índice del historial por URL para búsqueda eficiente O(1)
            history_index_by_url = {
                hc.get("url"): hc 
                for hc in history.get("concursos", []) 
                if hc.get("url")
            }
            
            special_domains_allow_without_previous = {"centroestudios.mineduc.cl"}
            
            # Filtrar concursos que tienen previous_concursos (versiones anteriores)
            concursos_con_versiones_previas = []
            for concurso in closed_concursos:
                concurso_url = concurso.get("url")
                if not concurso_url:
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
            
            # Aplicar filtros para mostrar preview
            filtered_preview = concursos_con_versiones_previas.copy()
            if filters.get("subdireccion"):
                filtered_preview = [c for c in filtered_preview if c.get("subdireccion") == filters["subdireccion"]]
            if filters.get("search_term"):
                search_term = filters["search_term"].lower()
                filtered_preview = [c for c in filtered_preview if search_term in c.get("nombre", "").lower()]
            
            # Filtrar concursos que ya tienen predicción O que están marcados como no predecibles
            filtered_preview = [
                c for c in filtered_preview 
                if c.get("url") not in existing_pred_urls and c.get("url") not in unpredictable_urls
            ]
            
            st.info(f"📋 {len(filtered_preview)} concursos cerrados disponibles para predicción (excluyendo los que ya tienen predicción o están marcados como no predecibles)")
            
            # Tabla de concursos con st.dataframe
            if filtered_preview:
                st.subheader("📋 Concursos disponibles para predicción")
                
                import pandas as pd
                df_data = []
                for concurso in filtered_preview:
                    df_data.append({
                        "Nombre": concurso.get("nombre", ""),
                        "URL": concurso.get("url", ""),
                        "Estado": concurso.get("estado", ""),
                        "Subdirección": concurso.get("subdireccion", "")
                    })
                
                df = pd.DataFrame(df_data)
                st.dataframe(
                    df,
                    width='stretch',
                    hide_index=True,
                    column_config={
                        "URL": st.column_config.LinkColumn("URL")
                    },
                )
                
                # Botón de predicción individual
                selected_concurso_pred = st.selectbox(
                    "Seleccionar concurso para predecir individualmente:",
                    options=[""] + [f"{idx + 1}. {c.get('nombre', '')}" for idx, c in enumerate(filtered_preview)],
                    key="select_concurso_predict",
                    format_func=lambda x: "Seleccionar..." if x == "" else x
                )
                if st.button("🔮 Predecir seleccionado", disabled=not selected_concurso_pred):
                    if selected_concurso_pred:
                        idx = int(selected_concurso_pred.split(".")[0]) - 1
                        if 0 <= idx < len(filtered_preview):
                            concurso = filtered_preview[idx]
                            if len(st.session_state.api_key_manager.api_keys) == 0:
                                st.error("⚠️ Necesitas configurar al menos una API key")
                            else:
                                from services.prediction_service import PredictionService
                                import asyncio
                                
                                prediction_service = PredictionService(
                                    history_manager=st.session_state.history_manager,
                                    api_key_manager=st.session_state.api_key_manager,
                                    model_name=GEMINI_CONFIG.get("model")
                                )
                                
                                status_placeholder = st.empty()
                                
                                def status_callback_individual(message: str):
                                    status_placeholder.info(message)
                                
                                try:
                                    result = asyncio.run(
                                        prediction_service.generate_prediction_for_concurso(
                                            concurso,
                                            status_callback=status_callback_individual
                                        )
                                    )
                                    
                                    if result:
                                        from utils.file_manager import load_predictions, load_unpredictable_concursos, save_predictions
                                        existing = load_predictions(site_name)
                                        existing.append(result)
                                        save_predictions(site_name, existing)
                                        st.success(f"✅ Predicción generada para '{concurso.get('nombre')}'")
                                        st.rerun()
                                    else:
                                        st.warning("⚠️ No se pudo generar una predicción válida")
                                except Exception as e:
                                    st.error(f"❌ Error: {str(e)}")
                                    logger.error(f"Error en predicción individual: {e}", exc_info=True)
            
            # Sección de Concursos No Predecibles
            if unpredictable_concursos:
                st.subheader("⚠️ Concursos No Predecibles")
                st.info(f"📋 {len(unpredictable_concursos)} concursos marcados como no predecibles")
                
                import pandas as pd
                unpred_data = []
                for idx, unpred in enumerate(unpredictable_concursos):
                    unpred_data.append({
                        "Nombre": unpred.get("concurso_nombre", ""),
                        "URL": unpred.get("concurso_url", ""),
                        "Razón": "Referencias a sí mismo" if unpred.get("reason") == "self_reference" else "LLM rechazó",
                        "Ver": f"view_unpred_{idx}",
                        "Reintentar": f"retry_unpred_{idx}"
                    })
                
                df_unpred = pd.DataFrame(unpred_data)
                st.dataframe(
                    df_unpred[["Nombre", "URL", "Razón"]],
                    width='stretch',
                    hide_index=True,
                    column_config={
                        "URL": st.column_config.LinkColumn("URL")
                    },
                )
                
                # Selector para ver detalles
                selected_unpred = st.selectbox(
                    "Seleccionar concurso para ver detalles:",
                    options=[""] + [f"{idx + 1}. {u.get('concurso_nombre', '')}" for idx, u in enumerate(unpredictable_concursos)],
                    key="select_unpred_details",
                    format_func=lambda x: "Seleccionar..." if x == "" else x
                )
                
                if selected_unpred:
                    idx = int(selected_unpred.split(".")[0]) - 1
                    if 0 <= idx < len(unpredictable_concursos):
                        unpred = unpredictable_concursos[idx]
                        
                        with st.expander(f"📋 Detalles: {unpred.get('concurso_nombre', 'N/A')}", expanded=True):
                            st.write("**Justificación:**")
                            st.write(unpred.get("justificacion", "No disponible"))
                            
                            # Mostrar previous_concursos si están disponibles
                            prev_concursos = unpred.get("previous_concursos", [])
                            if prev_concursos:
                                st.write("**Concursos anteriores encontrados:**")
                                prev_df_data = []
                                for prev in prev_concursos:
                                    prev_df_data.append({
                                        "Nombre": prev.get("nombre", ""),
                                        "Año": prev.get("año", ""),
                                        "Fecha Apertura": prev.get("fecha_apertura", ""),
                                        "Fecha Cierre": prev.get("fecha_cierre", ""),
                                        "URL": prev.get("url", "")
                                    })
                                prev_df = pd.DataFrame(prev_df_data)
                                st.dataframe(prev_df, hide_index=True, column_config={"URL": st.column_config.LinkColumn("URL")})
                            
                            # Botón de reintentar
                            col1, col2 = st.columns([3, 1])
                            with col2:
                                if st.button("🔄 Reintentar", key=f"retry_unpred_{idx}"):
                                    # Buscar el concurso en el historial
                                    concurso_data = None
                                    for hist_concurso in history.get("concursos", []):
                                        if hist_concurso.get("url") == unpred.get("concurso_url"):
                                            # Reconstruir objeto concurso
                                            versions = hist_concurso.get("versions", [])
                                            if versions:
                                                latest = versions[-1]
                                                concurso_data = {
                                                    "nombre": hist_concurso.get("nombre"),
                                                    "url": hist_concurso.get("url"),
                                                    "organismo": hist_concurso.get("organismo"),
                                                    "fecha_apertura": latest.get("fecha_apertura"),
                                                    "fecha_cierre": latest.get("fecha_cierre"),
                                                    "estado": latest.get("estado"),
                                                    "subdireccion": hist_concurso.get("subdireccion") or latest.get("subdireccion")
                                                }
                                            break
                                    
                                    if concurso_data:
                                        if len(st.session_state.api_key_manager.api_keys) == 0:
                                            st.error("⚠️ Necesitas configurar al menos una API key")
                                        else:
                                            from services.prediction_service import PredictionService
                                            import asyncio
                                            
                                            prediction_service = PredictionService(
                                                history_manager=st.session_state.history_manager,
                                                api_key_manager=st.session_state.api_key_manager,
                                                model_name=GEMINI_CONFIG.get("model")
                                            )
                                            
                                            status_placeholder_retry = st.empty()
                                            
                                            def status_callback_retry(message: str):
                                                status_placeholder_retry.info(message)
                                            
                                            try:
                                                result = asyncio.run(
                                                    prediction_service.generate_prediction_for_concurso(
                                                        concurso_data,
                                                        status_callback_retry
                                                    )
                                                )
                                                
                                                if result:
                                                    from utils.file_manager import load_predictions, save_predictions, load_unpredictable_concursos, save_unpredictable_concursos
                                                    # Eliminar de no predecibles
                                                    unpred_list = load_unpredictable_concursos(site_name)
                                                    unpred_list = [u for u in unpred_list if u.get("concurso_url") != unpred.get("concurso_url")]
                                                    save_unpredictable_concursos(site_name, unpred_list)
                                                    
                                                    # Agregar a predicciones
                                                    existing = load_predictions(site_name)
                                                    existing.append(result)
                                                    save_predictions(site_name, existing)
                                                    
                                                    st.success("✅ Predicción generada exitosamente")
                                                    st.rerun()
                                                else:
                                                    st.warning("⚠️ No se pudo generar predicción. El concurso puede seguir siendo no predecible.")
                                            except Exception as e:
                                                st.error(f"❌ Error al generar predicción: {e}")
                                    else:
                                        st.error("❌ No se encontró el concurso en el historial")
            
            # Botón de ejecución masiva
            if "prediction_processing" not in st.session_state:
                st.session_state.prediction_processing = False
            
            if "prediction_should_stop" not in st.session_state:
                st.session_state.prediction_should_stop = False
            
            col1, col2 = st.columns([3, 1])
            with col1:
                process_button = st.button(
                    "🚀 Realizar Predicciones",
                    disabled=st.session_state.prediction_processing or len(filtered_preview) == 0,
                    type="primary",
                    use_container_width=True,
                    key="prediction_process_button"
                )
            with col2:
                stop_button = st.button(
                    "⏹️ Detener",
                    disabled=not st.session_state.prediction_processing,
                    use_container_width=True,
                    key="prediction_stop_button"
                )
            
            if stop_button:
                st.session_state.prediction_should_stop = True
                st.warning("🛑 Deteniendo proceso de predicciones...")
            
            if process_button and len(filtered_preview) > 0:
                if len(st.session_state.api_key_manager.api_keys) == 0:
                    st.error("⚠️ Necesitas configurar al menos una API key en la pestaña 'Scraping y Configuración'")
                    st.stop()
                
                st.session_state.prediction_processing = True
                st.session_state.prediction_should_stop = False
                
                # Inicializar servicio de predicciones
                from services.prediction_service import PredictionService
                
                prediction_service = PredictionService(
                    history_manager=st.session_state.history_manager,
                    api_key_manager=st.session_state.api_key_manager,
                    model_name=GEMINI_CONFIG.get("model")
                )
                
                # Contenedor para actualizaciones
                status_placeholder = st.empty()
                progress_placeholder = st.progress(0)
                
                def status_callback(message: str):
                    status_placeholder.info(message)
                
                def should_stop_callback():
                    return st.session_state.prediction_should_stop
                
                try:
                    # Ejecutar predicciones
                    results = prediction_service.generate_predictions(
                        site=site_name,
                        filters=filters,
                        status_callback=status_callback,
                        should_stop_callback=should_stop_callback
                    )
                    
                    st.session_state.prediction_processing = False
                    
                    # Mostrar resultados
                    predictions = results.get("predictions", [])
                    stats = results.get("stats", {})
                    
                    if predictions:
                        st.success(f"✅ Generadas {len(predictions)} predicciones exitosamente")
                        st.info(f"💡 Ve a la pestaña 'Explorar Concursos' para ver las predicciones")
                    else:
                        st.warning("⚠️ No se generaron predicciones. Revisa el archivo de debug para más detalles.")
                    
                    # Mostrar estadísticas
                    with st.expander("📊 Estadísticas de ejecución", expanded=False):
                        st.json(stats)
                    
                    # Limpiar cache del historial
                    if hasattr(st.session_state.history_manager, '_cache'):
                        st.session_state.history_manager._cache.clear()
                    
                    st.rerun()
                    
                except Exception as e:
                    st.session_state.prediction_processing = False
                    st.error(f"❌ Error durante la generación de predicciones: {str(e)}")
                    logger.error(f"Error en generación de predicciones: {e}", exc_info=True)
            
            # Mostrar estado actual si está procesando
            if st.session_state.prediction_processing:
                status_placeholder.info("🔄 Generando predicciones...")
            
            # Mostrar predicciones existentes
            existing_predictions = load_predictions(site_name)
            if existing_predictions:
                st.subheader("📋 Predicciones Existentes")
                
                import pandas as pd
                
                # Crear DataFrame para la tabla
                pred_data = []
                for idx, pred in enumerate(existing_predictions):
                    pred_data.append({
                        "Concurso": pred.get("concurso_nombre", ""),
                        "URL": pred.get("concurso_url", ""),
                        "Fecha Predicha": pred.get("fecha_predicha", ""),
                        # Confianza eliminada del modelo; no se muestra
                        "Fuente": pred.get("source", "unknown")
                    })
                
                df_pred = pd.DataFrame(pred_data)
                
                # Mostrar tabla con st.dataframe
                st.dataframe(
                    df_pred,
                    width='stretch',
                    hide_index=True,
                    column_config={
                        "URL": st.column_config.LinkColumn("URL")
                    },
                )
                
                # Botones de acción
                col1, col2 = st.columns([8, 2])
                with col1:
                    selected_pred = st.selectbox(
                        "Seleccionar predicción para ver detalles:",
                        options=[""] + [f"{idx + 1}. {p.get('concurso_nombre', '')}" for idx, p in enumerate(existing_predictions)],
                        key="select_pred_details",
                        format_func=lambda x: "Seleccionar..." if x == "" else x
                    )
                with col2:
                    if st.button("🗑️ Eliminar seleccionada", disabled=not selected_pred, key="delete_pred_details_btn"):
                        if selected_pred:
                            idx = int(selected_pred.split(".")[0]) - 1
                            if 0 <= idx < len(existing_predictions):
                                if delete_prediction(site_name, existing_predictions[idx].get("concurso_url", "")):
                                    st.success("✅ Predicción eliminada")
                                    st.rerun()
                                else:
                                    st.error("❌ Error al eliminar")
                
                # Mostrar detalles si se seleccionó una predicción
                if selected_pred and selected_pred != "":
                    idx = int(selected_pred.split(".")[0]) - 1
                    if 0 <= idx < len(existing_predictions):
                        pred = existing_predictions[idx]
                        st.markdown("---")
                        st.markdown(f"### 🔮 Detalles de Predicción: {pred.get('concurso_nombre', 'N/A')}")
                        
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.markdown(f"**URL:** [{pred.get('concurso_url', 'N/A')}]({pred.get('concurso_url', '')})")
                            st.markdown(f"**Fecha Predicha:** {pred.get('fecha_predicha', 'N/A')}")
                            # Confianza eliminada del modelo
                        with col2:
                            if st.button("✕ Cerrar", key="close_pred_details"):
                                st.session_state.select_pred_details = ""
                                st.rerun()
                        
                        # Concursos anteriores - mostrar en tabla
                        previous_concursos = pred.get("previous_concursos", [])
                        if previous_concursos:
                            st.markdown("#### 📚 Concursos Anteriores")
                            prev_data = []
                            for prev in previous_concursos:
                                prev_data.append({
                                    "Nombre": prev.get('nombre', 'N/A'),
                                    "Año": prev.get('año', '') or '',
                                    "Fecha Apertura": prev.get('fecha_apertura', '') or '',
                                    "Fecha Cierre": prev.get('fecha_cierre', '') or '',
                                    "URL": prev.get('url', '') or ''
                                })
                            df_prev = pd.DataFrame(prev_data)
                            st.dataframe(
                                df_prev,
                                width='stretch',
                                hide_index=True,
                                column_config={
                                    "URL": st.column_config.LinkColumn("URL")
                                },
                            )
                        else:
                            st.info("No hay información de concursos anteriores guardada.")
                        
                        # Justificación
                        st.markdown("#### 💭 Justificación")
                        st.write(pred.get("justificacion", "No disponible"))
                        
                        st.markdown("---")
                
                # Sección de limpiar todas las predicciones
                st.markdown("---")
                st.subheader("🗑️ Limpiar Todas las Predicciones")
                st.warning("⚠️ Esta acción eliminará TODAS las predicciones de este sitio. Esta acción no se puede deshacer.")
                
                # Confirmación con checkbox
                confirm_clear = st.checkbox(
                    "Confirmo que quiero eliminar todas las predicciones",
                    key="confirm_clear_predictions"
                )
                
                if st.button(
                    "🗑️ Limpiar Todas las Predicciones",
                    disabled=not confirm_clear,
                    type="primary",
                    key="clear_all_predictions_btn"
                ):
                    if clear_predictions(site_name):
                        st.success(f"✅ Todas las predicciones de {site_name} han sido eliminadas")
                        st.rerun()
                    else:
                        st.error("❌ Error al limpiar las predicciones")
            else:
                st.info("📭 No hay predicciones disponibles para este sitio.")
            
            # Sección de limpiar todo (predicciones + no predecibles) - siempre visible
            st.markdown("---")
            st.subheader("🗑️ Limpiar Todo")
            st.error("⚠️ Esta acción eliminará TODOS los datos de predicciones de este sitio (predicciones y concursos no predecibles). Esta acción no se puede deshacer.")
            
            # Verificar si hay datos para limpiar
            has_predictions = len(load_predictions(site_name)) > 0
            has_unpredictable = len(load_unpredictable_concursos(site_name)) > 0
            
            if has_predictions or has_unpredictable:
                # Confirmación con checkbox
                confirm_clear_all = st.checkbox(
                    "Confirmo que quiero eliminar TODOS los datos de predicciones",
                    key="confirm_clear_all_predictions"
                )
                
                if st.button(
                    "🗑️ Limpiar Todo",
                    disabled=not confirm_clear_all,
                    type="primary",
                    key="clear_all_data_btn"
                ):
                    success_predictions = clear_predictions(site_name)
                    success_unpredictable = clear_unpredictable_concursos(site_name)
                    
                    if success_predictions and success_unpredictable:
                        st.success(f"✅ Todos los datos de predicciones de {site_name} han sido eliminados")
                        st.rerun()
                    elif success_predictions:
                        st.warning("⚠️ Se limpiaron las predicciones, pero hubo un error al limpiar los concursos no predecibles")
                        st.rerun()
                    elif success_unpredictable:
                        st.warning("⚠️ Se limpiaron los concursos no predecibles, pero hubo un error al limpiar las predicciones")
                        st.rerun()
                    else:
                        st.error("❌ Error al limpiar los datos")
            else:
                st.info("📭 No hay datos de predicciones para limpiar en este sitio.")

# ========== TAB 3: SCRAPING Y CONFIGURACIÓN ==========
with tab3:
    st.header("⚙️ Scraping y Configuración")
    st.caption("Configura el scraping y ejecuta extracciones de nuevos concursos")
    
    # Expander para configuración técnica
    with st.expander("⚙️ Configuración Técnica", expanded=False):
        # API Key Manager
        st.subheader("🔑 Gestión de Múltiples API Keys")
        key_manager = st.session_state.api_key_manager
        status = key_manager.get_status()
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Keys", status["total_keys"])
        with col2:
            st.metric("Disponibles", status["available_keys"])
        with col3:
            st.metric("Agotadas", status["exhausted_keys"])
        
        with st.expander("➕ Agregar API Keys"):
            new_keys_input = st.text_area(
                "Ingresa API keys (una por línea):",
                height=150,
                key="api_keys_input"
            )
            if st.button("Agregar Keys", key="add_api_keys_btn"):
                if new_keys_input:
                    keys_list = [k.strip() for k in new_keys_input.split("\n") if k.strip()]
                    added = key_manager.add_keys(keys_list)
                    if added > 0:
                        st.success(f"✅ {added} API key(s) agregada(s)")
                        st.rerun()
        
        # Modelo
        st.subheader("🤖 Modelo LLM")
        model_options = {}
        for model_id, model_info in AVAILABLE_MODELS.items():
            label = model_info["name"]
            if model_info.get("recommended"):
                label += " ⭐"
            model_options[label] = model_id
        
        default_model = None
        for model_id, model_info in AVAILABLE_MODELS.items():
            if model_info.get("recommended"):
                default_model = model_id
                break
        if not default_model:
            default_model = list(AVAILABLE_MODELS.keys())[0]
        
        selected_model_label = st.selectbox(
            "Modelo:",
            options=list(model_options.keys()),
            index=list(model_options.values()).index(default_model) if default_model in model_options.values() else 0,
            key="scraping_model_select"
        )
        selected_model = model_options[selected_model_label]
        
        # Mostrar estado detallado de la API key actual
        with st.expander("📊 Estado actual de API Keys", expanded=False):
            current_key_masked = status.get("current_key")
            st.write(f"**Key actual:** {current_key_masked}" if current_key_masked else "Sin API key activa")
        
        # Opciones de scraping
        st.subheader("📑 Opciones de Scraping")
        follow_pagination = st.checkbox("Seguir paginación", value=True, key="scrape_follow_pagination")
        max_pages = st.number_input(
            "Máximo de páginas",
            min_value=1,
            max_value=100,
            value=2,
            key="scrape_max_pages"
        ) if follow_pagination else 1
        debug_mode = st.checkbox("Modo Debug", value=False, key="scrape_debug_mode")
    
    # Selección de sitio único para scraping
    st.subheader("🌐 Seleccionar Sitio para Scraping (uno a la vez)")
    available_scrape_sites = list(SEED_URLS.keys())
    selected_site_for_scraping = st.selectbox(
        "Sitio a procesar:",
        options=["(elige un sitio)"] + available_scrape_sites,
        index=0,
        key="scrape_site_selector"
    )
    
    # URLs personalizadas
    custom_urls = st.text_area(
        "URLs adicionales (una por línea):",
        height=100,
        help="Agrega URLs personalizadas además del sitio seleccionado (debe ser el mismo dominio)"
    )
    
    # Construir lista de URLs
    urls_to_process = []
    if selected_site_for_scraping and selected_site_for_scraping != "(elige un sitio)":
        urls_to_process.extend(SEED_URLS[selected_site_for_scraping])
    if custom_urls:
        urls_to_process.extend([url.strip() for url in custom_urls.split("\n") if url.strip()])
    
    if urls_to_process:
        st.info(f"✅ {len(urls_to_process)} URL(s) a procesar")
    
    # Verificar estado persistente para determinar si hay scraping en progreso
    persistent_scraping = is_scraping_in_progress()
    scraping_active = st.session_state.scraping_in_progress or persistent_scraping
    
    # Obtener estado persistente para mostrar información
    persistent_state = load_scraping_state()
    site_info = ""
    if persistent_state:
        site_info = f" ({persistent_state.get('site', '')})"
    elif st.session_state.current_scraping_site:
        site_info = f" ({st.session_state.current_scraping_site})"
    
    # Botones de acción (solo scraping aquí)
    if scraping_active:
        col_info, col_clear = st.columns([3, 1])
        with col_info:
            st.info(f"🔄 Scraping en progreso{site_info}... Puedes cancelar con el botón de abajo.")
        with col_clear:
            if st.button("🧹 Limpiar estado", help="Si el scraping está 'colgado', limpia el estado manualmente", key="clear_state_btn"):
                clear_scraping_state()
                st.session_state.scraping_in_progress = False
                st.session_state.should_stop = False
                st.session_state.current_scraping_site = ""
                st.success("✅ Estado limpiado. Puedes iniciar un nuevo scraping.")
                st.rerun()
    
    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        run_scraping = st.button(
            "🚀 Ejecutar Scraping", 
            type="primary", 
            key="run_scraping_btn",
            disabled=scraping_active
        )
    with col_btn2:
        # El botón de cancelar SIEMPRE está habilitado si hay scraping activo
        # Verificamos directamente el estado persistente JUSTO ANTES de renderizar
        # para asegurar que funcione incluso si el estado se limpió al inicio
        current_persistent_state = load_scraping_state()
        cancel_enabled = (current_persistent_state and current_persistent_state.get("in_progress", False)) or st.session_state.scraping_in_progress
        cancel_scraping = st.button(
            "⏹️ Cancelar Scraping",
            disabled=not cancel_enabled,
            key="cancel_scraping_btn",
            help="El scraping se detendrá casi inmediatamente (máximo 500ms)"
        )
    
    if cancel_scraping:
        # Actualizar estado persistente y de sesión
        set_should_stop(True)
        st.session_state.should_stop = True
        # Mantener scraping_in_progress en True hasta que el proceso lo detecte
        st.warning("⚠️ Cancelación solicitada. El proceso se detendrá en la siguiente verificación (puede tardar unos segundos mientras termina de procesar la página actual).")
        # No hacer rerun aquí porque el scraping está en progreso y lo detectará
    
    if run_scraping:
        if not selected_site_for_scraping or selected_site_for_scraping == "(elige un sitio)":
            st.error("❌ Debes seleccionar un sitio para ejecutar el scraping.")
        elif not urls_to_process:
            st.error("❌ Debes especificar al menos una URL para procesar.")
        else:
            # Determinar el sitio desde las URLs
            site_name = selected_site_for_scraping
            if urls_to_process:
                try:
                    parsed = urlparse(urls_to_process[0])
                    site_name = (parsed.netloc or parsed.path.split('/')[0]).replace("www.", "")
                except Exception:
                    pass
            
            # Resetear estado de cancelación y guardar estado persistente
            # IMPORTANTE: Hacer esto ANTES de cualquier operación que pueda tardar
            st.session_state.should_stop = False
            st.session_state.scraping_in_progress = True
            st.session_state.current_scraping_site = site_name
            save_scraping_state(site_name, True, False)
            
            # Crear contenedores para mensajes de estado (usar empty para poder actualizar)
            status_placeholder = st.empty()
            status_placeholder.info("🔄 Iniciando scraping...")
            
            try:
                # Crear servicio de extracción
                key_manager = st.session_state.api_key_manager
                selected_model = GEMINI_CONFIG.get("model", "gemini-2.5-flash-lite")
                extraction_service = ExtractionService(
                    api_key_manager=key_manager,
                    model_name=selected_model
                )
                
                # Callback para verificar si debe detenerse (lee del estado persistente)
                def should_stop_callback():
                    # Verificar tanto el estado de sesión como el persistente
                    should_stop = st.session_state.should_stop or get_should_stop()
                    # Si se canceló, actualizar UI
                    if should_stop:
                        try:
                            status_placeholder.warning("⚠️ Cancelación detectada. Deteniendo proceso...")
                        except:
                            pass  # Ignorar errores de UI durante cancelación
                    return should_stop
                
                # Callback para mostrar estado
                status_messages = []
                def status_callback(msg: str):
                    status_messages.append(msg)
                    if len(status_messages) > 10:  # Mantener solo los últimos 10
                        status_messages.pop(0)
                    # Actualizar estado persistente periódicamente
                    save_scraping_state(site_name, True, get_should_stop())
                    # Actualizar UI con el último mensaje
                    if status_messages:
                        last_msg = status_messages[-1]
                        status_placeholder.info(f"🔄 {last_msg}")
                
                # Ejecutar scraping con callbacks
                concursos = extraction_service.extract_from_urls(
                    urls=urls_to_process,
                    follow_pagination=follow_pagination,
                    max_pages=max_pages,
                    should_stop_callback=should_stop_callback,
                    status_callback=status_callback
                )
                
                # Limpiar placeholder y mostrar resultado final
                status_placeholder.empty()
                
                was_cancelled = st.session_state.should_stop or get_should_stop()
                if was_cancelled:
                    st.warning("⚠️ Scraping cancelado por el usuario. Resultados parciales guardados.")
                else:
                    st.success(f"✅ Scraping completado: {len(concursos)} concursos extraídos")
                    if concursos:
                        st.info(f"💡 Ve a la pestaña 'Explorar Concursos' para ver los resultados")
            except RuntimeError as e:
                st.warning(str(e))
                st.session_state.scraping_in_progress = False
                st.session_state.should_stop = False
                clear_scraping_state()
            except KeyboardInterrupt:
                st.warning("⚠️ Scraping interrumpido por el usuario.")
                logger.info("Scraping interrumpido por KeyboardInterrupt")
                st.session_state.scraping_in_progress = False
                st.session_state.should_stop = False
                clear_scraping_state()
            except Exception as e:
                st.error(f"❌ Error durante el scraping: {e}")
                logger.error(f"Error durante el scraping: {e}", exc_info=True)
                st.session_state.scraping_in_progress = False
                st.session_state.should_stop = False
                clear_scraping_state()
            finally:
                # Siempre resetear el estado al finalizar (doble seguridad)
                st.session_state.scraping_in_progress = False
                st.session_state.should_stop = False
                clear_scraping_state()
    
    
# ========== TAB 4: CONCURSOS MANUALES ==========
with tab4:
    st.header("📝 Concursos Manuales")
    st.caption("Agrega concursos que no provienen de un sitio y visualiza sus predicciones deterministas (anual +1 año)")
    
    # Estado inicial de los campos manuales
    if "manual_nombre" not in st.session_state:
        st.session_state.manual_nombre = ""
    if "manual_organismo" not in st.session_state:
        st.session_state.manual_organismo = "Ingreso manual"
    if "manual_fecha_apertura" not in st.session_state:
        st.session_state.manual_fecha_apertura = ""
    if "manual_fecha_cierre" not in st.session_state:
        st.session_state.manual_fecha_cierre = ""
    if "manual_descripcion" not in st.session_state:
        st.session_state.manual_descripcion = ""
    if "manual_subdireccion" not in st.session_state:
        st.session_state.manual_subdireccion = ""
    
    # Ejemplo real (FONIDE 16)
    ejemplo_manual = {
        "nombre": "FONIDE 16 - Fondo de Investigación y Desarrollo en Educación 2025",
        "organismo": "MINEDUC",
        "fecha_apertura": "2025-05-15",
        "fecha_cierre": "2025-06-26",
        "descripcion": "Convocatoria anual FONIDE para investigación educativa.",
        "subdireccion": "Educación"
    }
    
    history_manual = st.session_state.history_manager.load_history("manual.local")
    concursos_manual = history_manual.get("concursos", [])
    predictions_manual = load_predictions("manual.local")
    pred_index = {p.get("concurso_url"): p for p in predictions_manual}
    
    st.subheader("Listado")
    if not concursos_manual:
        st.info("No hay concursos manuales aún.")
    else:
        table = []
        for c in concursos_manual:
            versions = c.get("versions", [])
            latest = versions[-1] if versions else {}
            url = c.get("url", "")
            pred = pred_index.get(url)
            table.append({
                "Nombre": c.get("nombre", ""),
                "Organismo": c.get("organismo", ""),
                "Fecha Apertura": latest.get("fecha_apertura") or c.get("fecha_apertura") or "",
                "Fecha Cierre": latest.get("fecha_cierre") or c.get("fecha_cierre") or "",
                "Estado": latest.get("estado") or c.get("estado") or "",
                "Predicción (+1 año)": pred.get("fecha_predicha", "") if pred else "",
                "URL": url,
            })
        st.dataframe(
            pd.DataFrame(table),
            width='stretch',
            hide_index=True,
            column_config={
                "URL": st.column_config.LinkColumn("URL")
            },
        )
        
        # Eliminación individual
        nombres_lista = [f"{idx + 1}. {c.get('nombre', 'Sin nombre')}" for idx, c in enumerate(concursos_manual)]
        selected_delete = st.selectbox(
            "Selecciona un concurso manual para eliminar:",
            options=["(ninguno)"] + nombres_lista,
            key="manual_delete_selector"
        )
        if st.button("🗑️ Eliminar concurso manual seleccionado", disabled=selected_delete == "(ninguno)", key="delete_manual_single_btn"):
            idx = None
            if selected_delete != "(ninguno)":
                try:
                    idx = int(selected_delete.split(".")[0]) - 1
                except Exception:
                    idx = None
            if idx is not None and 0 <= idx < len(concursos_manual):
                url_to_delete = concursos_manual[idx].get("url")
                if url_to_delete:
                    st.session_state.history_manager.delete_concurso("manual.local", url_to_delete)
                    try:
                        from utils.file_manager import delete_predictions_by_urls
                        delete_predictions_by_urls("manual.local", [url_to_delete])
                    except Exception:
                        pass
                    if hasattr(st.session_state.history_manager, "_cache"):
                        st.session_state.history_manager._cache.clear()
                    st.success("✅ Concurso manual eliminado.")
                    st.rerun()
                else:
                    st.error("❌ No se encontró la URL del concurso a eliminar.")
        
        # Eliminación total
        st.markdown("---")
        st.subheader("🗑️ Eliminar todos los concursos manuales")
        confirm_clear_manual = st.checkbox(
            "Confirmo que deseo eliminar TODOS los concursos manuales y sus predicciones",
            key="confirm_clear_manual_history"
        )
        if st.button("🗑️ Limpiar concursos manuales", disabled=not confirm_clear_manual, type="primary", key="clear_manual_history_btn"):
            try:
                st.session_state.history_manager.clear_history("manual.local")
                try:
                    from utils.file_manager import clear_predictions
                    clear_predictions("manual.local")
                except Exception:
                    pass
                if hasattr(st.session_state.history_manager, "_cache"):
                    st.session_state.history_manager._cache.clear()
                st.success("✅ Concursos manuales y predicciones eliminados.")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Error al limpiar concursos manuales: {e}")
    
    st.subheader("Agregar concurso manual")
    
    # Botón de ejemplo fuera del form para evitar conflictos de estado
    if st.button("Rellenar con datos de ejemplo (FONIDE 16)"):
        st.session_state.manual_nombre = ejemplo_manual["nombre"]
        st.session_state.manual_organismo = ejemplo_manual["organismo"]
        st.session_state.manual_fecha_apertura = ejemplo_manual["fecha_apertura"]
        st.session_state.manual_fecha_cierre = ejemplo_manual["fecha_cierre"]
        st.session_state.manual_descripcion = ejemplo_manual["descripcion"]
        st.session_state.manual_subdireccion = ejemplo_manual["subdireccion"]
        st.info("Campos rellenados con datos de ejemplo.")
        st.rerun()
    
    with st.form("add_manual_concurso_form"):
        manual_nombre = st.text_input("Nombre del concurso", key="manual_nombre")
        manual_organismo = st.text_input("Organismo", key="manual_organismo")
        manual_fecha_apertura = st.text_input("Fecha de apertura (YYYY-MM-DD)", key="manual_fecha_apertura")
        manual_fecha_cierre = st.text_input("Fecha de cierre (YYYY-MM-DD)", key="manual_fecha_cierre")
        manual_descripcion = st.text_area("Descripción (opcional)", key="manual_descripcion")
        manual_subdireccion = st.text_input("Subdirección/área (opcional)", key="manual_subdireccion")
        
        submitted_manual = st.form_submit_button("Guardar concurso manual", type="primary")
    
    if submitted_manual:
        from datetime import datetime, date
        import re
        from models import Concurso as ConcursoModel
        from utils.file_manager import save_predictions
        from utils.date_parser import parse_date
        
        def _slugify(text: str) -> str:
            slug = re.sub(r"[^a-zA-Z0-9\\-]+", "-", text.strip().lower()).strip("-")
            return slug or "concurso-manual"
        
        def _parse_iso_required(label: str, value: str) -> date:
            try:
                parsed = datetime.fromisoformat(value).date()
                return parsed
            except Exception:
                raise ValueError(f"{label} debe tener formato YYYY-MM-DD")
        
        try:
            if not manual_nombre.strip():
                raise ValueError("El nombre es obligatorio")
            if not manual_fecha_apertura.strip():
                raise ValueError("La fecha de apertura es obligatoria")
            if not manual_fecha_cierre.strip():
                raise ValueError("La fecha de cierre es obligatoria")
            
            fa_dt = _parse_iso_required("Fecha de apertura", manual_fecha_apertura.strip())
            fc_dt = _parse_iso_required("Fecha de cierre", manual_fecha_cierre.strip())
            if fc_dt <= fa_dt:
                raise ValueError("La fecha de cierre debe ser posterior a la fecha de apertura")
            
            slug = _slugify(manual_nombre or "concurso-manual")
            manual_url = f"https://manual.local/agregado-manualmente/{slug}"
            
            today = date.today()
            if fc_dt < today:
                estado = "Cerrado"
            elif fa_dt > today:
                estado = "Próximo"
            else:
                estado = "Abierto"
            
            concurso_manual = ConcursoModel(
                nombre=manual_nombre,
                fecha_apertura=fa_dt.isoformat(),
                fecha_cierre=fc_dt.isoformat(),
                organismo=manual_organismo or "Ingreso manual",
                financiamiento=None,
                url=manual_url,
                estado=estado,
                descripcion=manual_descripcion or None,
                subdireccion=manual_subdireccion or None,
                extraido_en=datetime.now().isoformat(),
                fuente="manual.local",
            )
            
            history_updated = st.session_state.history_manager.update_history(
                site="manual.local",
                concursos=[concurso_manual],
                enriched_content={
                    manual_url: {
                        "markdown": manual_descripcion or "",
                        "html": manual_descripcion or "",
                    }
                }
            )
            st.session_state.history_manager.save_history("manual.local", history_updated)
            if hasattr(st.session_state.history_manager, "_cache"):
                st.session_state.history_manager._cache.clear()
            
            # Predicción determinista (apertura + 1 año)
            base_date = parse_date(fa_dt.isoformat())
            if base_date is None:
                raise ValueError("No se pudo interpretar la fecha de apertura para la predicción")
            target = base_date.replace(year=base_date.year + 1)
            pred_entry = {
                "concurso_nombre": manual_nombre,
                "concurso_url": manual_url,
                "fecha_predicha": target.strftime("%Y-%m-%d"),
                "justificacion": "Concurso agregado manualmente; se asume recurrencia anual en la fecha de apertura.",
                "predicted_at": datetime.now().isoformat(),
                "source": "manual_rule",
                "previous_concursos": [],
            }
            save_predictions("manual.local", [pred_entry])
            
            st.success(f"✅ Concurso manual guardado y predicción asignada ({pred_entry['fecha_predicha']})")
            st.rerun()
        except Exception as e:
            st.error(f"❌ Error al guardar concurso manual: {e}")
            logger.error(f"Error al guardar concurso manual: {e}", exc_info=True)
