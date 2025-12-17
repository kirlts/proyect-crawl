"""
Gestor de historial de concursos por sitio

Mantiene un registro histórico de todos los concursos detectados por sitio,
permitiendo:
- Detectar concursos nuevos vs existentes
- Actualizar historial incrementalmente
- Analizar patrones históricos para predicción de fechas
"""

import json
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set, Tuple
from pathlib import Path
from urllib.parse import urlparse

from models import Concurso
# Eliminado uso de similitud; solo comparación por URL

logger = logging.getLogger(__name__)


class HistoryManager:
    """Gestiona el historial de concursos por sitio"""
    
    def __init__(self, history_dir: Optional[str] = None):
        """
        Inicializa el gestor de historial.
        
        Args:
            history_dir: Directorio donde se guardan los archivos de historial
        """
        if history_dir is None:
            from config import DATA_DIR
            history_dir = os.path.join(DATA_DIR, "history")
        
        self.history_dir = history_dir
        Path(self.history_dir).mkdir(parents=True, exist_ok=True)
        
        # Caché simple en memoria para evitar recargas innecesarias del mismo historial
        # Estructura: { site: { "history": dict, "last_loaded": datetime.isoformat() } }
        self._cache: Dict[str, Dict[str, Any]] = {}
    
    def _get_site_from_url(self, url: str) -> str:
        """
        Extrae el nombre del sitio desde una URL.
        
        Args:
            url: URL del sitio
            
        Returns:
            Nombre del sitio (ej: "anid.cl")
        """
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split('/')[0]
        # Remover www. si existe
        domain = domain.replace("www.", "")
        return domain
    
    def _get_history_file_path(self, site: str) -> str:
        """
        Obtiene la ruta del archivo de historial para un sitio.
        
        Args:
            site: Nombre del sitio (ej: "anid.cl")
            
        Returns:
            Ruta completa del archivo de historial
        """
        # Normalizar nombre del sitio para nombre de archivo
        safe_site = site.replace(".", "_").replace("/", "_")
        filename = f"history_{safe_site}.json"
        return os.path.join(self.history_dir, filename)
    
    def load_history(self, site: str) -> Dict[str, Any]:
        """
        Carga el historial de un sitio.
        
        Args:
            site: Nombre del sitio (ej: "anid.cl")
            
        Returns:
            Diccionario con el historial (vacío si no existe)
        """
        filepath = self._get_history_file_path(site)
        
        # Intentar devolver desde caché si ya se cargó anteriormente en este proceso
        cached = self._cache.get(site)
        if cached is not None:
            # No logueamos como "cargado" de nuevo, es solo una lectura de memoria
            return cached["history"]
        
        if not os.path.exists(filepath):
            history = {
                "site": site,
                "created_at": datetime.now().isoformat(),
                "last_updated": None,
                "concursos": []
            }
            self._cache[site] = {"history": history, "last_loaded": datetime.now().isoformat()}
            return history
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                history = json.load(f)
            logger.info(f"📚 Historial cargado para {site}: {len(history.get('concursos', []))} concursos")
            self._cache[site] = {"history": history, "last_loaded": datetime.now().isoformat()}
            return history
        except Exception as e:
            logger.error(f"Error al cargar historial de {site}: {e}", exc_info=True)
            history = {
                "site": site,
                "created_at": datetime.now().isoformat(),
                "last_updated": None,
                "concursos": []
            }
            self._cache[site] = {"history": history, "last_loaded": datetime.now().isoformat()}
            return history
    
    def save_history(self, site: str, history: Dict[str, Any]) -> str:
        """
        Guarda el historial de un sitio.
        
        Args:
            site: Nombre del sitio
            history: Diccionario con el historial
            
        Returns:
            Ruta del archivo guardado
        """
        filepath = self._get_history_file_path(site)
        history["last_updated"] = datetime.now().isoformat()
        
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 Historial guardado para {site}: {len(history.get('concursos', []))} concursos")
            # Actualizar caché para que futuros load_history() no tengan que re-leer de disco
            self._cache[site] = {"history": history, "last_loaded": datetime.now().isoformat()}
            return filepath
        except Exception as e:
            logger.error(f"Error al guardar historial de {site}: {e}", exc_info=True)
            raise
    
    def _normalize_concurso_key(self, concurso: Concurso) -> Tuple[str, str]:
        """
        Genera una clave normalizada para identificar un concurso.
        
        Args:
            concurso: Objeto Concurso
            
        Returns:
            Tupla (nombre_normalizado, url_normalizada)
        """
        nombre = concurso.nombre.lower().strip()
        url = concurso.url.strip()
        return (nombre, url)
    
    def find_existing_concursos(
        self, 
        site: str, 
        new_concursos: List[Concurso]
    ) -> Tuple[List[Concurso], List[Concurso], Dict[str, Dict[str, Any]]]:
        """
        Compara concursos nuevos con el historial y separa en existentes y nuevos.
        Usa similitud para detectar concursos del mismo tipo pero diferentes años.
        
        Args:
            site: Nombre del sitio
            new_concursos: Lista de concursos recién extraídos
            
        Returns:
            Tupla (concursos_existentes, concursos_nuevos, historial_dict)
            donde historial_dict mapea (nombre, url) -> datos del historial
        """
        history = self.load_history(site)
        history_dict = {}
        history_url_index = {}
        
        # Crear índice del historial
        for hist_concurso in history.get("concursos", []):
            key = (
                hist_concurso.get("nombre", "").lower().strip(),
                hist_concurso.get("url", "").strip()
            )
            history_dict[key] = hist_concurso
            hist_url = (hist_concurso.get("url") or "").strip()
            if hist_url:
                history_url_index[hist_url] = hist_concurso
        
        # Separar concursos nuevos y existentes (solo por URL o clave exacta)
        existing_concursos = []
        new_concursos_list = []
        similar_matches = []  # Obsoleto; se mantiene para compatibilidad de logs
        
        for concurso in new_concursos:
            key = self._normalize_concurso_key(concurso)
            concurso_url = (concurso.url or "").strip()
            
            # 1) Coincidencia por URL (más robusta que el nombre)
            if concurso_url and concurso_url in history_url_index:
                existing_concursos.append(concurso)
                # Asegurar que history_dict también tenga la entrada para este concurso
                hist_concurso = history_url_index[concurso_url]
                hist_key = (
                    hist_concurso.get("nombre", "").lower().strip(),
                    hist_concurso.get("url", "").strip()
                )
                history_dict[hist_key] = hist_concurso
            # 2) Coincidencia exacta por (nombre, url)
            elif key in history_dict:
                existing_concursos.append(concurso)
            else:
                # Sin similitud: solo por URL/clave; si no existe, es nuevo
                new_concursos_list.append(concurso)
        
        logger.info(
            f"🔍 Análisis de historial para {site}: "
            f"{len(existing_concursos)} existentes ({len(similar_matches)} por similitud), "
            f"{len(new_concursos_list)} nuevos"
        )
        
        # Ya no se reportan coincidencias por similitud
        
        return existing_concursos, new_concursos_list, history_dict
    
    def update_history(
        self,
        site: str,
        concursos: List[Concurso],
        existing_keys: Optional[Set[Tuple[str, str]]] = None,
        enriched_content: Optional[Dict[str, Dict[str, Any]]] = None,
        skip_similarity_check: bool = False
    ) -> Dict[str, Any]:
        """
        Actualiza el historial con nuevos concursos y versiones.
        
        Args:
            site: Nombre del sitio
            concursos: Lista de concursos a agregar/actualizar
            existing_keys: Set de claves de concursos que ya existían (para evitar duplicar versiones)
            enriched_content: Diccionario con contenido completo de páginas individuales {url: {markdown, ...}}
            
        Returns:
            Historial actualizado
        """
        history = self.load_history(site)
        if existing_keys is None:
            existing_keys = set()
        
        if enriched_content is None:
            enriched_content = {}
        
        # Crear índice de concursos en historial (por clave exacta y por similitud)
        history_index = {}
        history_list = history.get("concursos", [])
        
        for i, hist_concurso in enumerate(history_list):
            key = (
                hist_concurso.get("nombre", "").lower().strip(),
                hist_concurso.get("url", "").strip()
            )
            history_index[key] = i
        
        # Procesar cada concurso
        detected_at = datetime.now().isoformat()
        
        for concurso in concursos:
            key = self._normalize_concurso_key(concurso)
            concurso_dict = concurso.model_dump()
            
            # Detectar concursos suspendidos por URL y forzar estado "Suspendido"
            concurso_url = (concurso.url or "").strip()
            if "concurso-suspendido" in concurso_url:
                concurso_dict["estado"] = "Suspendido"
                # También actualizar el objeto concurso si tiene el atributo
                if hasattr(concurso, "estado"):
                    concurso.estado = "Suspendido"
            
            # Obtener contenido completo de la página si está disponible
            page_content = enriched_content.get(concurso.url, {})
            page_markdown = page_content.get("markdown", "")
            previous_concursos = page_content.get("previous_concursos", [])
            
            # Buscar por clave exacta primero
            if key in history_index:
                # Concurso existente: actualizar last_seen y agregar versión
                idx = history_index[key]
                hist_concurso = history["concursos"][idx]
                
                # Actualizar last_seen
                hist_concurso["last_seen"] = detected_at
                
                # Si la URL indica "concurso-suspendido", forzar estado "Suspendido" a nivel de historial y versión
                hist_url = (hist_concurso.get("url") or "").strip()
                if "concurso-suspendido" in hist_url:
                    concurso_dict["estado"] = "Suspendido"
                    hist_concurso["estado"] = "Suspendido"
                
                # Agregar nueva versión solo si es diferente a la última
                versions = hist_concurso.get("versions", [])
                if versions:
                    last_version = versions[-1]
                    # Comparar fechas y estado para detectar cambios
                    if (last_version.get("fecha_apertura") != concurso_dict.get("fecha_apertura") or
                        last_version.get("fecha_cierre") != concurso_dict.get("fecha_cierre") or
                        last_version.get("estado") != concurso_dict.get("estado")):
                        # Hay cambios, agregar nueva versión
                        version_data = {
                            "fecha_apertura": concurso_dict.get("fecha_apertura"),
                            "fecha_cierre": concurso_dict.get("fecha_cierre"),
                            "estado": concurso_dict.get("estado"),
                            "financiamiento": concurso_dict.get("financiamiento"),
                            "descripcion": concurso_dict.get("descripcion"),
                            "subdireccion": concurso_dict.get("subdireccion"),
                            "detected_at": detected_at
                        }
                        # Agregar contenido completo de la página si está disponible
                        if page_markdown:
                            version_data["page_content"] = page_markdown
                        versions.append(version_data)
                        hist_concurso["versions"] = versions
                else:
                    # No hay versiones previas, crear primera
                    version_data = {
                        "fecha_apertura": concurso_dict.get("fecha_apertura"),
                        "fecha_cierre": concurso_dict.get("fecha_cierre"),
                        "estado": concurso_dict.get("estado"),
                        "financiamiento": concurso_dict.get("financiamiento"),
                        "descripcion": concurso_dict.get("descripcion"),
                        "subdireccion": concurso_dict.get("subdireccion"),
                        "detected_at": detected_at
                    }
                    # Agregar contenido completo de la página si está disponible
                    if page_markdown:
                        version_data["page_content"] = page_markdown
                    versions.append(version_data)
                    hist_concurso["versions"] = versions
                
                # Actualizar contenido completo más reciente si está disponible
                if page_markdown:
                    hist_concurso["latest_page_content"] = page_markdown
                    hist_concurso["latest_page_content_updated"] = detected_at
                
                # Guardar concursos anteriores (SIEMPRE, incluso si está vacío para indicar que ya se procesó)
                hist_concurso["previous_concursos"] = previous_concursos  # Puede ser [] si no tiene versiones anteriores
                hist_concurso["previous_concursos_updated"] = detected_at
                
                # Actualizar campos principales si han cambiado
                if not hist_concurso.get("organismo"):
                    hist_concurso["organismo"] = concurso_dict.get("organismo")
                if not hist_concurso.get("financiamiento") and concurso_dict.get("financiamiento"):
                    hist_concurso["financiamiento"] = concurso_dict.get("financiamiento")
                if not hist_concurso.get("descripcion") and concurso_dict.get("descripcion"):
                    hist_concurso["descripcion"] = concurso_dict.get("descripcion")
                if not hist_concurso.get("subdireccion") and concurso_dict.get("subdireccion"):
                    hist_concurso["subdireccion"] = concurso_dict.get("subdireccion")
            else:
                # Concurso completamente nuevo (sin similitud): agregar al historial
                version_data = {
                    "fecha_apertura": concurso_dict.get("fecha_apertura"),
                    "fecha_cierre": concurso_dict.get("fecha_cierre"),
                    "estado": concurso_dict.get("estado"),
                    "financiamiento": concurso_dict.get("financiamiento"),
                    "descripcion": concurso_dict.get("descripcion"),
                    "subdireccion": concurso_dict.get("subdireccion"),
                    "detected_at": detected_at
                }
                # Agregar contenido completo de la página si está disponible
                if page_markdown:
                    version_data["page_content"] = page_markdown
                
                new_entry = {
                    "nombre": concurso_dict.get("nombre"),
                    "url": concurso_dict.get("url"),
                    "organismo": concurso_dict.get("organismo"),
                    "financiamiento": concurso_dict.get("financiamiento"),
                    "descripcion": concurso_dict.get("descripcion"),
                    "subdireccion": concurso_dict.get("subdireccion"),
                    "first_seen": detected_at,
                    "last_seen": detected_at,
                    "versions": [version_data]
                }
                
                # Si la URL indica "concurso-suspendido", forzar estado "Suspendido" a nivel de historial
                if "concurso-suspendido" in concurso_url:
                    new_entry["estado"] = "Suspendido"
                    version_data["estado"] = "Suspendido"
                
                # Agregar contenido completo más reciente
                if page_markdown:
                    new_entry["latest_page_content"] = page_markdown
                    new_entry["latest_page_content_updated"] = detected_at
                
                # Guardar concursos anteriores (SIEMPRE, incluso si está vacío para indicar que ya se procesó)
                new_entry["previous_concursos"] = previous_concursos  # Puede ser [] si no tiene versiones anteriores
                new_entry["previous_concursos_updated"] = detected_at
                
                history["concursos"].append(new_entry)
        
        return history
    
    def fix_suspended_concursos_by_url(self, site: str) -> Dict[str, Any]:
        """
        Corrige concursos existentes en el historial que tienen "concurso-suspendido" 
        en la URL pero no tienen estado "Suspendido".
        
        Args:
            site: Nombre del sitio (ej: "anid.cl")
            
        Returns:
            Diccionario con estadísticas de la corrección:
            {
                "concursos_corregidos": int,
                "urls_corregidas": List[str]
            }
        """
        history = self.load_history(site)
        concursos = history.get("concursos", [])
        fixed_count = 0
        fixed_urls = []
        
        for hist_concurso in concursos:
            url = (hist_concurso.get("url") or "").strip()
            if not url or "concurso-suspendido" not in url:
                continue
            
            # Verificar si ya tiene estado "Suspendido"
            estado_actual = (hist_concurso.get("estado") or "").strip()
            versions = hist_concurso.get("versions", [])
            latest_version = versions[-1] if versions else {}
            estado_version = (latest_version.get("estado") or "").strip()
            
            if estado_actual.lower() == "suspendido" and estado_version.lower() == "suspendido":
                continue  # Ya está correcto
            
            # Corregir estado a nivel de historial
            hist_concurso["estado"] = "Suspendido"
            
            # Corregir estado en la última versión si existe
            if versions:
                latest_version["estado"] = "Suspendido"
                versions[-1] = latest_version
                hist_concurso["versions"] = versions
            else:
                # Si no hay versiones, crear una con estado "Suspendido"
                versions = [{
                    "estado": "Suspendido",
                    "detected_at": datetime.now().isoformat()
                }]
                hist_concurso["versions"] = versions
            
            fixed_count += 1
            fixed_urls.append(url)
        
        if fixed_count > 0:
            self.save_history(site, history)
        
        return {
            "concursos_corregidos": fixed_count,
            "urls_corregidas": fixed_urls
        }
    
    def analyze_historical_patterns(
        self,
        site: str,
        concurso_nombre: str,
        concurso_url: str,
        window_years: int = 2
    ) -> Optional[Dict[str, Any]]:
        """
        Analiza patrones históricos de un concurso para predecir fecha de apertura.
        
        Busca versiones pasadas del mismo concurso en una ventana de tiempo.
        
        Args:
            site: Nombre del sitio
            concurso_nombre: Nombre del concurso
            concurso_url: URL del concurso
            window_years: Ventana de años hacia atrás para buscar (default: 2)
            
        Returns:
            Diccionario con análisis de patrones o None si no hay suficiente historial
        """
        history = self.load_history(site)
        key = (concurso_nombre.lower().strip(), concurso_url.strip())
        
        # Buscar concurso en historial
        for hist_concurso in history.get("concursos", []):
            hist_key = (
                hist_concurso.get("nombre", "").lower().strip(),
                hist_concurso.get("url", "").strip()
            )
            
            if hist_key == key:
                versions = hist_concurso.get("versions", [])
                if not versions:
                    return None
                
                # Filtrar versiones dentro de la ventana de tiempo
                cutoff_date = datetime.now() - timedelta(days=window_years * 365)
                relevant_versions = []
                
                for version in versions:
                    detected_at_str = version.get("detected_at")
                    if detected_at_str:
                        try:
                            detected_at = datetime.fromisoformat(detected_at_str.replace("Z", "+00:00"))
                            if detected_at >= cutoff_date:
                                relevant_versions.append(version)
                        except (ValueError, TypeError):
                            # Si no se puede parsear la fecha, incluir de todas formas
                            relevant_versions.append(version)
                
                if len(relevant_versions) < 2:
                    # Necesitamos al menos 2 versiones para detectar patrón
                    return None
                
                # Analizar patrones de fechas
                aperturas = []
                cierres = []
                
                for version in relevant_versions:
                    fecha_apertura = version.get("fecha_apertura")
                    fecha_cierre = version.get("fecha_cierre")
                    
                    if fecha_apertura:
                        parsed = self._parse_date_for_analysis(fecha_apertura)
                        if parsed:
                            aperturas.append(parsed)
                    
                    if fecha_cierre:
                        parsed = self._parse_date_for_analysis(fecha_cierre)
                        if parsed:
                            cierres.append(parsed)
                
                # Calcular patrones
                pattern = self._calculate_date_pattern(aperturas, cierres)
                
                return {
                    "concurso_key": key,
                    "total_versions": len(versions),
                    "relevant_versions": len(relevant_versions),
                    "pattern": pattern,
                    "aperturas": [a.strftime("%Y-%m-%d") for a in aperturas if a],
                    "cierres": [c.strftime("%Y-%m-%d") for c in cierres if c]
                }
        
        return None
    
    def _parse_date_for_analysis(self, date_str: str) -> Optional[datetime]:
        """
        Parsea una fecha para análisis histórico.
        
        Args:
            date_str: String con la fecha
            
        Returns:
            datetime object o None
        """
        from utils.date_parser import parse_date
        return parse_date(date_str)
    
    def _calculate_date_pattern(
        self,
        aperturas: List[datetime],
        cierres: List[datetime]
    ) -> Dict[str, Any]:
        """
        Calcula patrones de fechas desde historial.
        
        Args:
            aperturas: Lista de fechas de apertura históricas
            cierres: Lista de fechas de cierre históricas
            
        Returns:
            Diccionario con información del patrón
        """
        pattern = {
            "has_pattern": False,
            "predicted_apertura": None,
            "predicted_cierre": None,
            "confidence": "Bajo",
            "pattern_type": None
        }
        
        if len(aperturas) >= 2:
            # Analizar intervalo entre aperturas
            intervals = []
            sorted_aperturas = sorted(aperturas)
            
            for i in range(1, len(sorted_aperturas)):
                interval = (sorted_aperturas[i] - sorted_aperturas[i-1]).days
                intervals.append(interval)
            
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                
                # Si el intervalo promedio es razonable (entre 300 y 400 días = ~1 año)
                if 300 <= avg_interval <= 400:
                    # Patrón anual
                    last_apertura = sorted_aperturas[-1]
                    predicted = last_apertura + timedelta(days=int(avg_interval))
                    
                    pattern["has_pattern"] = True
                    pattern["predicted_apertura"] = predicted.strftime("%Y-%m-%d")
                    pattern["pattern_type"] = "anual"
                    pattern["confidence"] = "Alto" if len(aperturas) >= 3 else "Medio"
        
        if len(cierres) >= 2:
            # Analizar intervalo entre cierres
            intervals = []
            sorted_cierres = sorted(cierres)
            
            for i in range(1, len(sorted_cierres)):
                interval = (sorted_cierres[i] - sorted_cierres[i-1]).days
                intervals.append(interval)
            
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                
                # Si el intervalo promedio es razonable
                if 300 <= avg_interval <= 400:
                    last_cierre = sorted_cierres[-1]
                    predicted = last_cierre + timedelta(days=int(avg_interval))
                    
                    if not pattern.get("predicted_apertura"):
                        # Si no hay patrón de apertura, usar cierre
                        pattern["has_pattern"] = True
                        pattern["predicted_apertura"] = predicted.strftime("%Y-%m-%d")
                        pattern["pattern_type"] = "anual_cierre"
                        pattern["confidence"] = "Medio"
        
        return pattern
    
    def get_historical_prediction(
        self,
        site: str,
        concurso: Concurso
    ) -> Optional[Tuple[str, str]]:
        """
        Obtiene predicción de fecha de apertura basada en historial.
        
        Args:
            site: Nombre del sitio
            concurso: Concurso para el cual predecir
            
        Returns:
            Tupla (fecha_predicha, confianza) o None si no hay suficiente historial
        """
        analysis = self.analyze_historical_patterns(
            site,
            concurso.nombre,
            concurso.url,
            window_years=2
        )
        
        if analysis and analysis.get("pattern", {}).get("has_pattern"):
            pattern = analysis["pattern"]
            predicted = pattern.get("predicted_apertura")
            confidence = pattern.get("confidence", "Bajo")
            
            if predicted:
                return (predicted, confidence)
        
        return None
    
    def delete_concurso(self, site: str, url: str) -> bool:
        """
        Elimina un concurso específico del historial por su URL.
        
        Args:
            site: Nombre del sitio (ej: "anid.cl")
            url: URL del concurso a eliminar
            
        Returns:
            True si se eliminó exitosamente, False en caso contrario
        """
        try:
            history = self.load_history(site)
            concursos = history.get("concursos", [])
            
            # Filtrar el concurso a eliminar
            original_count = len(concursos)
            concursos = [c for c in concursos if c.get("url", "").strip() != url.strip()]
            
            if len(concursos) == original_count:
                # No se encontró el concurso
                logger.warning(f"⚠️ No se encontró concurso con URL {url} en historial de {site}")
                return False
            
            # Actualizar historial
            history["concursos"] = concursos
            history["last_updated"] = datetime.now().isoformat()
            
            # Guardar
            self.save_history(site, history)
            
            logger.info(f"🗑️ Concurso eliminado del historial de {site}: {url}")
            return True
            
        except Exception as e:
            logger.error(f"Error al eliminar concurso de historial de {site}: {e}", exc_info=True)
            return False
    
    def clear_history(self, site: str) -> bool:
        """
        Limpia todos los concursos del historial de un sitio.
        
        Args:
            site: Nombre del sitio (ej: "anid.cl")
            
        Returns:
            True si se limpió exitosamente, False en caso contrario
        """
        try:
            history = self.load_history(site)
            count = len(history.get("concursos", []))
            
            # Limpiar concursos
            history["concursos"] = []
            history["last_updated"] = datetime.now().isoformat()
            
            # Guardar
            self.save_history(site, history)
            
            logger.info(f"🗑️ Historial limpiado para {site}: {count} concursos eliminados")
            return True
            
        except Exception as e:
            logger.error(f"Error al limpiar historial de {site}: {e}", exc_info=True)
            return False

    def find_incomplete_concurso_urls(self, site: str) -> List[Dict[str, Any]]:
        """
        Detecta concursos con datos esenciales incompletos en el historial de un sitio.
        
        Se consideran incompletos aquellos concursos que:
        - Tienen nombre vacío o igual a "Concurso sin título"
        - O no tienen estado
        - O no tienen fecha de apertura
        - O no tienen fecha de cierre
        
        La evaluación se hace sobre la última versión registrada del concurso.
        
        Args:
            site: Nombre del sitio (ej: "anid.cl")
        
        Returns:
            Lista de diccionarios con información mínima de los concursos incompletos:
            [
                {
                    "url": str,
                    "nombre": str,
                    "estado": str,
                    "fecha_apertura": str,
                    "fecha_cierre": str,
                },
                ...
            ]
        """
        history = self.load_history(site)
        concursos = history.get("concursos", [])
        incompletos: List[Dict[str, Any]] = []
        
        import re
        
        def _is_malformed_date(date_str: str) -> bool:
            """
            Retorna True si la fecha existe pero no cumple formato YYYY-MM-DD
            o contiene placeholders como '**'.
            """
            if not date_str:
                return False  # vacío ya se considera incompleto arriba
            if "**" in date_str:
                return True
            # Aceptar solo formato ISO simple
            return re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str.strip()) is None
        
        for hist_concurso in concursos:
            url = (hist_concurso.get("url") or "").strip()
            if not url:
                # Sin URL no podemos repararlo automáticamente
                continue

            # 💡 Regla adicional: si la URL contiene explícitamente "concurso-suspendido",
            # consideramos el concurso como correctamente procesado, aunque aún no se
            # haya propagado el estado "Suspendido" al historial.
            # Esto evita que reaparezca como "incompleto" y que el flujo de reparación
            # intente arreglar algo que, por definición, está suspendido.
            if "concurso-suspendido" in url:
                continue
            
            nombre = (hist_concurso.get("nombre") or "").strip()
            versions = hist_concurso.get("versions", []) or []
            latest = versions[-1] if versions else {}
            
            estado = (latest.get("estado") or hist_concurso.get("estado") or "").strip()
            fecha_apertura = (latest.get("fecha_apertura") or hist_concurso.get("fecha_apertura") or "").strip()
            fecha_cierre = (latest.get("fecha_cierre") or hist_concurso.get("fecha_cierre") or "").strip()
            
            # Si el concurso está marcado como suspendido, lo consideramos
            # correctamente procesado aunque no tenga fechas completas.
            if estado.lower() == "suspendido":
                continue
            
            nombre_generico = (not nombre) or (nombre.lower() == "concurso sin título")
            campos_faltantes = (not estado) or (not fecha_apertura) or (not fecha_cierre)
            fechas_mal_formateadas = _is_malformed_date(fecha_apertura) or _is_malformed_date(fecha_cierre)
            
            if nombre_generico or campos_faltantes or fechas_mal_formateadas:
                incompletos.append({
                    "url": url,
                    "nombre": nombre,
                    "estado": estado,
                    "fecha_apertura": fecha_apertura,
                    "fecha_cierre": fecha_cierre,
                })
        
        return incompletos

