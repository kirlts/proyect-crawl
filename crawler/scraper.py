"""
Wrapper para Crawl4AI que optimiza la extracción de contenido para análisis con LLM

NOTA: Este módulo ahora usa estrategias para manejar diferentes sitios.
La lógica específica de ANID se ha movido a crawler/strategies/anid_strategy.py
"""

import asyncio
from typing import Optional, Dict, Any, List
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawler.strategies import get_strategy_for_url
import logging

logger = logging.getLogger(__name__)


class WebScraper:
    """Clase para realizar scraping de sitios web usando Crawl4AI"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Inicializa el scraper con configuración personalizada
        
        Args:
            config: Diccionario con configuración (headless, page_timeout, etc.)
        """
        self.config = config or {}
        self.headless = self.config.get("headless", True)
        self.page_timeout = self.config.get("page_timeout", 60000)
        self.wait_for = self.config.get("wait_for", "css:body")
        self.word_count_threshold = self.config.get("word_count_threshold", 10)
        self.verbose = self.config.get("verbose", False)
        self.cache_mode_str = self.config.get("cache_mode", "BYPASS")
        
        # Convertir string a enum
        self.cache_mode = CacheMode.BYPASS if self.cache_mode_str == "BYPASS" else CacheMode.ENABLED
        
    async def scrape_url_with_pagination(self, url: str, max_pages: int = 2) -> List[Dict[str, Any]]:
        """
        Scrapea una URL con paginación usando la estrategia apropiada para el sitio.
        
        Este método reemplaza scrape_url_with_dynamic_pagination() y ahora
        usa estrategias para manejar diferentes tipos de paginación.
        
        Args:
            url: URL inicial a scrapear
            max_pages: Número máximo de páginas a procesar (límite duro)
            
        Returns:
            Lista de diccionarios con el resultado de cada página
        """
        # Obtener estrategia apropiada para la URL
        strategy = get_strategy_for_url(url)
        
        # Combinar configuración base con configuración específica del sitio
        from config.global_config import CRAWLER_CONFIG
        base_config = {**CRAWLER_CONFIG, **self.config}
        site_config = strategy.get_crawler_config()
        combined_config = {**base_config, **site_config}
        
        # Crear instancia de crawler
        browser_config = BrowserConfig(
            headless=self.headless,
            verbose=self.verbose
        )
        
        async with AsyncWebCrawler(config=browser_config) as crawler:
            # Usar estrategia para scrapear con paginación
            return await strategy.scrape_with_pagination(url, max_pages, crawler, combined_config)
    
    async def scrape_url_with_dynamic_pagination(self, url: str, max_pages: int = 2) -> List[Dict[str, Any]]:
        """
        DEPRECATED: Usar scrape_url_with_pagination() en su lugar.
        
        Este método se mantiene para compatibilidad hacia atrás pero ahora
        delega a scrape_url_with_pagination() que usa estrategias.
        
        La lógica original de paginación dinámica se ha movido a
        crawler/pagination/anid_pagination.py (AnidPagination).
        """
        logger.warning("scrape_url_with_dynamic_pagination() está deprecado. Usar scrape_url_with_pagination() en su lugar.")
        return await self.scrape_url_with_pagination(url, max_pages)
    
    async def scrape_url(self, url: str) -> Dict[str, Any]:
        """
        Realiza el scraping de una URL y retorna el markdown
        
        Args:
            url: URL a scrapear
            
        Returns:
            Dict con:
                - success: bool
                - markdown: str (markdown extraído)
                - url: str (URL final después de redirecciones)
                - error: str (mensaje de error si falla)
        """
        try:
            # Configurar el navegador
            browser_config = BrowserConfig(
                headless=self.headless,
                verbose=self.verbose
            )
            
            # Configurar el generador de markdown
            # CRÍTICO: Usar raw_html como fuente para asegurar que tenemos TODO el contenido
            # incluyendo el contenido AJAX cargado dinámicamente que capturamos directamente
            md_generator = DefaultMarkdownGenerator(
                content_source="raw_html",  # Usar raw_html en lugar de cleaned_html (default)
                content_filter=PruningContentFilter(
                    threshold=0.3,  # Más bajo = menos agresivo, más contenido
                    threshold_type="dynamic",  # Ajuste dinámico
                    min_word_threshold=5  # Mínimo muy bajo para no perder información
                ),
                options={
                    "ignore_links": False,  # Mantener links para contexto
                    "escape_html": True,
                }
            )
            
            # Configurar el crawler run
            # NO usar delay_before_return_html - la espera se maneja inteligentemente en el hook
            scan_full_page = self.config.get("scan_full_page", True)
            wait_until = self.config.get("wait_until", "domcontentloaded")
            wait_for_images = self.config.get("wait_for_images", False)
            
            # JavaScript que ESPERA ACTIVAMENTE a que el contenido AJAX se cargue
            # ANID usa JetEngine/Elementor que carga contenido dinámicamente vía AJAX
            # El contenido está dentro de elementos con data-elementor-type="jet-listing-items"
            js_code = """
            (async () => {
                // Esperar a que la página esté completamente cargada
                await new Promise(resolve => { 
                    if (document.readyState === 'complete') resolve(); 
                    else window.addEventListener('load', resolve); 
                });
                
                // Función para verificar si los items tienen contenido real
                const checkItemsHaveContent = () => {
                    const items = document.querySelectorAll('.jet-listing-grid__item');
                    if (items.length < 6) {
                        console.log('Items encontrados:', items.length);
                        return false;
                    }
                    
                    let itemsWithContent = 0;
                    items.forEach((item, idx) => {
                        // Buscar contenido dentro del item (títulos, fechas, etc.)
                        const text = (item.innerText || item.textContent || '').trim();
                        const hasElementorContent = item.querySelector('[data-elementor-type="jet-listing-items"]');
                        const hasTitle = item.querySelector('h1, h2, h3, h4, h5, h6, .elementor-heading-title');
                        const hasDate = /\\d{1,2}\\s+de\\s+\\w+\\s*,\\s*\\d{4}|\\d{4}-\\d{2}-\\d{2}|noviembre|diciembre|octubre|cierre|apertura|inicio/i.test(text);
                        const hasLongText = text.length > 100;
                        const hasImage = item.querySelector('img[src]');
                        
                        // El contenido debe tener al menos un título o fecha Y texto largo
                        if (hasElementorContent && hasLongText && (hasTitle || hasDate || hasImage)) {
                            itemsWithContent++;
                            console.log(`Item ${idx} tiene contenido:`, text.substring(0, 80));
                        }
                    });
                    
                    console.log('Total items con contenido:', itemsWithContent, 'de', items.length);
                    return itemsWithContent >= 6;
                };
                
                // Esperar activamente hasta que tengamos contenido (máximo 30 segundos)
                const maxWaitTime = 30000; // 30 segundos
                const checkInterval = 500; // Verificar cada 500ms
                const startTime = Date.now();
                
                while (!checkItemsHaveContent() && (Date.now() - startTime) < maxWaitTime) {
                    // Scroll para activar lazy loading
                    window.scrollTo(0, document.body.scrollHeight);
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    window.scrollTo(0, 0);
                    await new Promise(resolve => setTimeout(resolve, checkInterval));
                    
                    // Disparar eventos que JetEngine podría estar esperando
                    window.dispatchEvent(new Event('resize'));
                    window.dispatchEvent(new Event('scroll'));
                    
                    // Intentar disparar eventos de JetEngine si existen
                    if (window.jetSmartFilters) {
                        try { window.jetSmartFilters.trigger('updated'); } catch(e) {}
                    }
                    if (window.jetListing) {
                        try { window.jetListing.trigger('updated'); } catch(e) {}
                    }
                }
                
                // Verificación final
                const finalCheck = checkItemsHaveContent();
                console.log('Verificación final - Items con contenido:', finalCheck);
                
                // Esperar un poco más para asegurar que todo se renderizó
                await new Promise(resolve => setTimeout(resolve, 2000));
            })();
            """
            
            run_config = CrawlerRunConfig(
                cache_mode=self.cache_mode,
                markdown_generator=md_generator,
                wait_for=self.wait_for,
                wait_until=wait_until,
                page_timeout=self.page_timeout,
                word_count_threshold=self.word_count_threshold,
                remove_overlay_elements=True,  # Remover modales/popups
                exclude_external_links=False,  # Mantener todos los links para contexto
                # NO usar delay_before_return_html - la espera se maneja en el hook
                scan_full_page=scan_full_page,  # Hacer scroll completo para contenido lazy
                js_code=js_code,  # Ejecutar JS para cargar contenido dinámico
                wait_for_images=wait_for_images,
                capture_network_requests=True,  # Capturar requests para debug
                capture_console_messages=True,  # Capturar console para ver logs de JS
                verbose=self.verbose
            )
            
            # Realizar el crawling
            # Variable para almacenar el HTML capturado directamente desde la página
            captured_html = None
            
            async with AsyncWebCrawler(config=browser_config) as crawler:
                # Hook optimizado: espera inteligente basada en estado, no timeouts fijos
                async def before_retrieve_html_hook(page, context, **kwargs):
                    """Hook que espera inteligentemente a que el contenido AJAX se cargue"""
                    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
                    nonlocal captured_html
                    
                    try:
                        # Función de verificación optimizada: verifica estado del contenido
                        check_content_ready = """() => {
                            const items = document.querySelectorAll('.jet-listing-grid__item');
                            if (items.length === 0) return {ready: false, reason: 'no_items'};
                            
                            let itemsWithContent = 0;
                            let totalTextLength = 0;
                            
                            items.forEach((item) => {
                                const text = (item.innerText || item.textContent || '').trim();
                                const hasElementorContent = item.querySelector('[data-elementor-type="jet-listing-items"]');
                                const hasTitle = item.querySelector('h1, h2, h3, h4, h5, h6, .elementor-heading-title');
                                const hasDate = /\\d{1,2}\\s+de\\s+\\w+\\s*,\\s*\\d{4}|\\d{4}-\\d{2}-\\d{2}|noviembre|diciembre|octubre|cierre|apertura|inicio/i.test(text);
                                const hasLongText = text.length > 100;
                                
                                if (hasElementorContent && hasLongText && (hasTitle || hasDate)) {
                                    itemsWithContent++;
                                    totalTextLength += text.length;
                                }
                            });
                            
                            // Considerar listo si tenemos al menos 6 items con contenido
                            // O si tenemos suficiente contenido total (más de 5000 caracteres de texto)
                            const isReady = itemsWithContent >= 6 || totalTextLength > 5000;
                            
                            return {
                                ready: isReady,
                                itemsCount: items.length,
                                itemsWithContent: itemsWithContent,
                                totalTextLength: totalTextLength
                            };
                        }"""
                        
                        # Polling inteligente: verificar cada 500ms, máximo 60 segundos
                        max_attempts = 120  # 60 segundos / 0.5 segundos
                        attempt = 0
                        last_state = None
                        
                        while attempt < max_attempts:
                            state = await page.evaluate(check_content_ready)
                            
                            if state['ready']:
                                logger.info(f"✅ Contenido listo después de {attempt * 0.5:.1f}s: {state['itemsWithContent']} items con contenido, {state['totalTextLength']} chars")
                                break
                            
                            # Si el estado no cambió después de varios intentos, puede que esté cargando
                            if last_state and last_state == state:
                                # Esperar un poco más y hacer scroll para activar lazy loading
                                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                                await page.wait_for_timeout(500)
                                await page.evaluate("window.scrollTo(0, 0);")
                            
                            last_state = state
                            attempt += 1
                            await page.wait_for_timeout(500)
                        else:
                            # Si llegamos aquí, no se cargó en el tiempo esperado
                            final_state = await page.evaluate(check_content_ready)
                            logger.warning(f"⚠️ Timeout esperando contenido. Estado final: {final_state}")
                        
                        # Esperar a que la red esté inactiva (sin requests pendientes)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except:
                            pass  # Continuar aunque no esté completamente idle
                        
                        # CAPTURAR EL HTML DIRECTAMENTE
                        captured_html = await page.content()
                        
                        # Verificar que el HTML capturado tiene contenido
                        from bs4 import BeautifulSoup
                        soup_check = BeautifulSoup(captured_html, 'html.parser')
                        items = soup_check.select('.jet-listing-grid__item')
                        items_with_elementor = sum(1 for item in items if item.select_one('[data-elementor-type="jet-listing-items"]'))
                        logger.info(f"✅ HTML capturado: {len(captured_html)} chars, {len(items)} items, {items_with_elementor} con Elementor")
                        
                    except Exception as e:
                        logger.warning(f"⚠️ Error en before_retrieve_html hook: {e}")
                        # Intentar capturar HTML de todas formas
                        try:
                            captured_html = await page.content()
                        except:
                            pass
                    
                    return page
                
                # Hook adicional para interceptar el HTML antes de retornarlo
                async def before_return_html_hook(page, context, html, **kwargs):
                    """Hook que reemplaza el HTML con el capturado directamente si está disponible"""
                    nonlocal captured_html
                    if captured_html:
                        logger.info(f"🔄 Reemplazando HTML con versión capturada directamente ({len(captured_html)} chars)")
                        # Retornar el HTML capturado directamente
                        # Nota: según la documentación, before_return_html recibe html como parámetro
                        # pero no podemos modificarlo directamente. En su lugar, usaremos captured_html después
                        return page
                    return page
                
                # Registrar los hooks
                crawler.crawler_strategy.set_hook("before_retrieve_html", before_retrieve_html_hook)
                crawler.crawler_strategy.set_hook("before_return_html", before_return_html_hook)
                
                result = await crawler.arun(url=url, config=run_config)
                
                if result.success:
                    # Si capturamos HTML directamente, usarlo en lugar del result.html
                    # Esto asegura que tenemos el HTML con el contenido AJAX cargado
                    raw_html = captured_html if captured_html else (result.html if result.html else "")
                    
                    if captured_html:
                        logger.info(f"✅ Usando HTML capturado directamente ({len(captured_html)} chars)")
                        # REGENERAR markdown desde el HTML capturado directamente usando html2text
                        # para asegurar que contiene el contenido AJAX cargado
                        import html2text
                        h = html2text.HTML2Text()
                        h.ignore_links = False
                        h.escape_html = True
                        h.body_width = 0  # Sin límite de ancho
                        markdown_content = h.handle(captured_html)
                        logger.info(f"✅ Markdown regenerado desde HTML capturado: {len(markdown_content)} chars")
                    else:
                        logger.warning("⚠️ No se capturó HTML directamente, usando result.html")
                        # Usar raw_markdown para tener más contenido disponible
                        # fit_markdown puede ser demasiado agresivo y eliminar información importante
                        if result.markdown:
                            if hasattr(result.markdown, 'raw_markdown') and result.markdown.raw_markdown:
                                markdown_content = result.markdown.raw_markdown
                            elif hasattr(result.markdown, 'fit_markdown') and result.markdown.fit_markdown:
                                markdown_content = result.markdown.fit_markdown
                            else:
                                markdown_content = str(result.markdown)
                        else:
                            markdown_content = ""
                    
                    # Sanitizar HTML antes de guardarlo
                    from utils.html_sanitizer import sanitize_html
                    sanitized_html = sanitize_html(raw_html, preserve_structure=True)
                    
                    # Verificar que tenemos contenido de concursos en el HTML
                    from bs4 import BeautifulSoup
                    soup_check = BeautifulSoup(raw_html, 'html.parser')
                    items = soup_check.select('.jet-listing-grid__item')
                    items_with_content = 0
                    items_with_elementor = 0
                    for item in items:
                        text = item.get_text(strip=True)
                        # Verificar si tiene el elemento Elementor que contiene el contenido AJAX
                        has_elementor = item.select_one('[data-elementor-type="jet-listing-items"]')
                        has_title = item.select_one('h1, h2, h3, h4, h5, h6, .elementor-heading-title')
                        has_date = bool(item.get_text() and any(word in text.lower() for word in ['noviembre', 'diciembre', 'octubre', 'cierre', 'apertura', 'inicio']))
                        
                        if has_elementor:
                            items_with_elementor += 1
                        if len(text) > 100 and (has_title or has_date):
                            items_with_content += 1
                    
                    logger.info(f"HTML para {url}: {len(raw_html)} caracteres")
                    logger.info(f"Items encontrados: {len(items)}, Items con Elementor: {items_with_elementor}, Items con contenido: {items_with_content}")
                    
                    # Log de network requests si están disponibles
                    if hasattr(result, 'network_requests') and result.network_requests:
                        ajax_requests = [r for r in result.network_requests if 'ajax' in r.get('url', '').lower() or 'jet' in r.get('url', '').lower()]
                        logger.info(f"Requests AJAX/JetEngine detectados: {len(ajax_requests)}")
                    
                    # Log de console messages si están disponibles
                    if hasattr(result, 'console_messages') and result.console_messages:
                        content_logs = [m for m in result.console_messages if 'contenido' in m.get('text', '').lower() or 'item' in m.get('text', '').lower()]
                        if content_logs:
                            logger.info(f"Logs de contenido en consola: {len(content_logs)} mensajes")
                    logger.debug(f"Markdown extraído para {url}: {len(markdown_content)} caracteres")
                    logger.debug(f"HTML sanitizado: {len(raw_html)} -> {len(sanitized_html)} caracteres")
                    
                    if items_with_content < 6:
                        logger.warning(f"⚠️ Solo {items_with_content} items con contenido de {len(items)} items encontrados para {url}")
                    if len(markdown_content) < 500:
                        logger.warning(f"⚠️ Markdown muy corto para {url} ({len(markdown_content)} chars). Puede que falte contenido.")
                    
                    return {
                        "success": True,
                        "markdown": markdown_content,
                        "html": sanitized_html,  # HTML sanitizado
                        "html_raw": raw_html,  # HTML original para paginación y debug
                        "url": result.url,
                        "html_length": len(raw_html),
                        "html_sanitized_length": len(sanitized_html),
                        "markdown_length": len(markdown_content)
                    }
                else:
                    error_msg = result.error_message or "Error desconocido en el crawling"
                    logger.error(f"Error al scrapear {url}: {error_msg}")
                    return {
                        "success": False,
                        "markdown": "",
                        "url": url,
                        "error": error_msg
                    }
                    
        except asyncio.TimeoutError:
            error_msg = f"Timeout al scrapear {url}"
            logger.error(error_msg)
            return {
                "success": False,
                "markdown": "",
                "url": url,
                "error": error_msg
            }
        except Exception as e:
            error_msg = f"Error inesperado al scrapear {url}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "markdown": "",
                "url": url,
                "error": error_msg
            }
    
    async def scrape_url_simple(self, url: str) -> Dict[str, Any]:
        """
        Realiza el scraping de una URL y retorna el markdown
        
        Args:
            url: URL a scrapear
            
        Returns:
            Dict con:
                - success: bool
                - markdown: str (markdown extraído)
                - url: str (URL final después de redirecciones)
                - error: str (mensaje de error si falla)
        """
        try:
            # Configurar el navegador
            browser_config = BrowserConfig(
                headless=self.headless,
                verbose=self.verbose
            )
            
            # Configurar el generador de markdown
            # CRÍTICO: Usar raw_html como fuente para asegurar que tenemos TODO el contenido
            # incluyendo el contenido AJAX cargado dinámicamente que capturamos directamente
            md_generator = DefaultMarkdownGenerator(
                content_source="raw_html",  # Usar raw_html en lugar de cleaned_html (default)
                content_filter=PruningContentFilter(
                    threshold=0.3,  # Más bajo = menos agresivo, más contenido
                    threshold_type="dynamic",  # Ajuste dinámico
                    min_word_threshold=5  # Mínimo muy bajo para no perder información
                ),
                options={
                    "ignore_links": False,  # Mantener links para contexto
                    "escape_html": True,
                }
            )
            
            # Configurar el crawler run
            # NO usar delay_before_return_html - la espera se maneja inteligentemente en el hook
            scan_full_page = self.config.get("scan_full_page", True)
            wait_until = self.config.get("wait_until", "domcontentloaded")
            wait_for_images = self.config.get("wait_for_images", False)
            
            # JavaScript que ESPERA ACTIVAMENTE a que el contenido AJAX se cargue
            # ANID usa JetEngine/Elementor que carga contenido dinámicamente vía AJAX
            # El contenido está dentro de elementos con data-elementor-type="jet-listing-items"
            js_code = """
            (async () => {
                // Esperar a que la página esté completamente cargada
                await new Promise(resolve => { 
                    if (document.readyState === 'complete') resolve(); 
                    else window.addEventListener('load', resolve); 
                });
                
                // Función para verificar si los items tienen contenido real
                const checkItemsHaveContent = () => {
                    const items = document.querySelectorAll('.jet-listing-grid__item');
                    if (items.length < 6) {
                        console.log('Items encontrados:', items.length);
                        return false;
                    }
                    
                    let itemsWithContent = 0;
                    items.forEach((item, idx) => {
                        // Buscar contenido dentro del item (títulos, fechas, etc.)
                        const text = (item.innerText || item.textContent || '').trim();
                        const hasElementorContent = item.querySelector('[data-elementor-type="jet-listing-items"]');
                        const hasTitle = item.querySelector('h1, h2, h3, h4, h5, h6, .elementor-heading-title');
                        const hasDate = /\\d{1,2}\\s+de\\s+\\w+\\s*,\\s*\\d{4}|\\d{4}-\\d{2}-\\d{2}|noviembre|diciembre|octubre|cierre|apertura|inicio/i.test(text);
                        const hasLongText = text.length > 100;
                        const hasImage = item.querySelector('img[src]');
                        
                        // El contenido debe tener al menos un título o fecha Y texto largo
                        if (hasElementorContent && hasLongText && (hasTitle || hasDate || hasImage)) {
                            itemsWithContent++;
                            console.log(`Item ${idx} tiene contenido:`, text.substring(0, 80));
                        }
                    });
                    
                    console.log('Total items con contenido:', itemsWithContent, 'de', items.length);
                    return itemsWithContent >= 6;
                };
                
                // Esperar activamente hasta que tengamos contenido (máximo 30 segundos)
                const maxWaitTime = 30000; // 30 segundos
                const checkInterval = 500; // Verificar cada 500ms
                const startTime = Date.now();
                
                while (!checkItemsHaveContent() && (Date.now() - startTime) < maxWaitTime) {
                    // Scroll para activar lazy loading
                    window.scrollTo(0, document.body.scrollHeight);
                    await new Promise(resolve => setTimeout(resolve, 1000));
                    window.scrollTo(0, 0);
                    await new Promise(resolve => setTimeout(resolve, checkInterval));
                    
                    // Disparar eventos que JetEngine podría estar esperando
                    window.dispatchEvent(new Event('resize'));
                    window.dispatchEvent(new Event('scroll'));
                    
                    // Intentar disparar eventos de JetEngine si existen
                    if (window.jetSmartFilters) {
                        try { window.jetSmartFilters.trigger('updated'); } catch(e) {}
                    }
                    if (window.jetListing) {
                        try { window.jetListing.trigger('updated'); } catch(e) {}
                    }
                }
                
                // Verificación final
                const finalCheck = checkItemsHaveContent();
                console.log('Verificación final - Items con contenido:', finalCheck);
                
                // Esperar un poco más para asegurar que todo se renderizó
                await new Promise(resolve => setTimeout(resolve, 2000));
            })();
            """
            
            run_config = CrawlerRunConfig(
                cache_mode=self.cache_mode,
                markdown_generator=md_generator,
                wait_for=self.wait_for,
                wait_until=wait_until,
                page_timeout=self.page_timeout,
                word_count_threshold=self.word_count_threshold,
                remove_overlay_elements=True,  # Remover modales/popups
                exclude_external_links=False,  # Mantener todos los links para contexto
                # NO usar delay_before_return_html - la espera se maneja en el hook
                scan_full_page=scan_full_page,  # Hacer scroll completo para contenido lazy
                js_code=js_code,  # Ejecutar JS para cargar contenido dinámico
                wait_for_images=wait_for_images,
                capture_network_requests=True,  # Capturar requests para debug
                capture_console_messages=True,  # Capturar console para ver logs de JS
                verbose=self.verbose
            )
            
            # Realizar el crawling
            # Variable para almacenar el HTML capturado directamente desde la página
            captured_html = None
            
            async with AsyncWebCrawler(config=browser_config) as crawler:
                # Hook optimizado: espera inteligente basada en estado, no timeouts fijos
                async def before_retrieve_html_hook(page, context, **kwargs):
                    """Hook que espera inteligentemente a que el contenido AJAX se cargue"""
                    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
                    nonlocal captured_html
                    
                    try:
                        # Función de verificación optimizada: verifica estado del contenido
                        check_content_ready = """() => {
                            const items = document.querySelectorAll('.jet-listing-grid__item');
                            if (items.length === 0) return {ready: false, reason: 'no_items'};
                            
                            let itemsWithContent = 0;
                            let totalTextLength = 0;
                            
                            items.forEach((item) => {
                                const text = (item.innerText || item.textContent || '').trim();
                                const hasElementorContent = item.querySelector('[data-elementor-type="jet-listing-items"]');
                                const hasTitle = item.querySelector('h1, h2, h3, h4, h5, h6, .elementor-heading-title');
                                const hasDate = /\\d{1,2}\\s+de\\s+\\w+\\s*,\\s*\\d{4}|\\d{4}-\\d{2}-\\d{2}|noviembre|diciembre|octubre|cierre|apertura|inicio/i.test(text);
                                const hasLongText = text.length > 100;
                                
                                if (hasElementorContent && hasLongText && (hasTitle || hasDate)) {
                                    itemsWithContent++;
                                    totalTextLength += text.length;
                                }
                            });
                            
                            // Considerar listo si tenemos al menos 6 items con contenido
                            // O si tenemos suficiente contenido total (más de 5000 caracteres de texto)
                            const isReady = itemsWithContent >= 6 || totalTextLength > 5000;
                            
                            return {
                                ready: isReady,
                                itemsCount: items.length,
                                itemsWithContent: itemsWithContent,
                                totalTextLength: totalTextLength
                            };
                        }"""
                        
                        # Polling inteligente: verificar cada 500ms, máximo 60 segundos
                        max_attempts = 120  # 60 segundos / 0.5 segundos
                        attempt = 0
                        last_state = None
                        
                        while attempt < max_attempts:
                            state = await page.evaluate(check_content_ready)
                            
                            if state['ready']:
                                logger.info(f"✅ Contenido listo después de {attempt * 0.5:.1f}s: {state['itemsWithContent']} items con contenido, {state['totalTextLength']} chars")
                                break
                            
                            # Si el estado no cambió después de varios intentos, puede que esté cargando
                            if last_state and last_state == state:
                                # Esperar un poco más y hacer scroll para activar lazy loading
                                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                                await page.wait_for_timeout(500)
                                await page.evaluate("window.scrollTo(0, 0);")
                            
                            last_state = state
                            attempt += 1
                            await page.wait_for_timeout(500)
                        else:
                            # Si llegamos aquí, no se cargó en el tiempo esperado
                            final_state = await page.evaluate(check_content_ready)
                            logger.warning(f"⚠️ Timeout esperando contenido. Estado final: {final_state}")
                        
                        # Esperar a que la red esté inactiva (sin requests pendientes)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except:
                            pass  # Continuar aunque no esté completamente idle
                        
                        # CAPTURAR EL HTML DIRECTAMENTE
                        captured_html = await page.content()
                        
                        # Verificar que el HTML capturado tiene contenido
                        from bs4 import BeautifulSoup
                        soup_check = BeautifulSoup(captured_html, 'html.parser')
                        items = soup_check.select('.jet-listing-grid__item')
                        items_with_elementor = sum(1 for item in items if item.select_one('[data-elementor-type="jet-listing-items"]'))
                        logger.info(f"✅ HTML capturado: {len(captured_html)} chars, {len(items)} items, {items_with_elementor} con Elementor")
                        
                    except Exception as e:
                        logger.warning(f"⚠️ Error en before_retrieve_html hook: {e}")
                        # Intentar capturar HTML de todas formas
                        try:
                            captured_html = await page.content()
                        except:
                            pass
                    
                    return page
                
                # Hook adicional para interceptar el HTML antes de retornarlo
                async def before_return_html_hook(page, context, html, **kwargs):
                    """Hook que reemplaza el HTML con el capturado directamente si está disponible"""
                    nonlocal captured_html
                    if captured_html:
                        logger.info(f"🔄 Reemplazando HTML con versión capturada directamente ({len(captured_html)} chars)")
                        # Retornar el HTML capturado directamente
                        # Nota: según la documentación, before_return_html recibe html como parámetro
                        # pero no podemos modificarlo directamente. En su lugar, usaremos captured_html después
                        return page
                    return page
                
                # Registrar los hooks
                crawler.crawler_strategy.set_hook("before_retrieve_html", before_retrieve_html_hook)
                crawler.crawler_strategy.set_hook("before_return_html", before_return_html_hook)
                
                result = await crawler.arun(url=url, config=run_config)
                
                if result.success:
                    # Si capturamos HTML directamente, usarlo en lugar del result.html
                    # Esto asegura que tenemos el HTML con el contenido AJAX cargado
                    raw_html = captured_html if captured_html else (result.html if result.html else "")
                    
                    if captured_html:
                        logger.info(f"✅ Usando HTML capturado directamente ({len(captured_html)} chars)")
                        # REGENERAR markdown desde el HTML capturado directamente usando html2text
                        # para asegurar que contiene el contenido AJAX cargado
                        import html2text
                        h = html2text.HTML2Text()
                        h.ignore_links = False
                        h.escape_html = True
                        h.body_width = 0  # Sin límite de ancho
                        markdown_content = h.handle(captured_html)
                        logger.info(f"✅ Markdown regenerado desde HTML capturado: {len(markdown_content)} chars")
                    else:
                        logger.warning("⚠️ No se capturó HTML directamente, usando result.html")
                        # Usar raw_markdown para tener más contenido disponible
                        # fit_markdown puede ser demasiado agresivo y eliminar información importante
                        if result.markdown:
                            if hasattr(result.markdown, 'raw_markdown') and result.markdown.raw_markdown:
                                markdown_content = result.markdown.raw_markdown
                            elif hasattr(result.markdown, 'fit_markdown') and result.markdown.fit_markdown:
                                markdown_content = result.markdown.fit_markdown
                            else:
                                markdown_content = str(result.markdown)
                        else:
                            markdown_content = ""
                    
                    # Sanitizar HTML antes de guardarlo
                    from utils.html_sanitizer import sanitize_html
                    sanitized_html = sanitize_html(raw_html, preserve_structure=True)
                    
                    # Verificar que tenemos contenido de concursos en el HTML
                    from bs4 import BeautifulSoup
                    soup_check = BeautifulSoup(raw_html, 'html.parser')
                    items = soup_check.select('.jet-listing-grid__item')
                    items_with_content = 0
                    items_with_elementor = 0
                    for item in items:
                        text = item.get_text(strip=True)
                        # Verificar si tiene el elemento Elementor que contiene el contenido AJAX
                        has_elementor = item.select_one('[data-elementor-type="jet-listing-items"]')
                        has_title = item.select_one('h1, h2, h3, h4, h5, h6, .elementor-heading-title')
                        has_date = bool(item.get_text() and any(word in text.lower() for word in ['noviembre', 'diciembre', 'octubre', 'cierre', 'apertura', 'inicio']))
                        
                        if has_elementor:
                            items_with_elementor += 1
                        if len(text) > 100 and (has_title or has_date):
                            items_with_content += 1
                    
                    logger.info(f"HTML para {url}: {len(raw_html)} caracteres")
                    logger.info(f"Items encontrados: {len(items)}, Items con Elementor: {items_with_elementor}, Items con contenido: {items_with_content}")
                    
                    # Log de network requests si están disponibles
                    if hasattr(result, 'network_requests') and result.network_requests:
                        ajax_requests = [r for r in result.network_requests if 'ajax' in r.get('url', '').lower() or 'jet' in r.get('url', '').lower()]
                        logger.info(f"Requests AJAX/JetEngine detectados: {len(ajax_requests)}")
                    
                    # Log de console messages si están disponibles
                    if hasattr(result, 'console_messages') and result.console_messages:
                        content_logs = [m for m in result.console_messages if 'contenido' in m.get('text', '').lower() or 'item' in m.get('text', '').lower()]
                        if content_logs:
                            logger.info(f"Logs de contenido en consola: {len(content_logs)} mensajes")
                    logger.debug(f"Markdown extraído para {url}: {len(markdown_content)} caracteres")
                    logger.debug(f"HTML sanitizado: {len(raw_html)} -> {len(sanitized_html)} caracteres")
                    
                    if items_with_content < 6:
                        logger.warning(f"⚠️ Solo {items_with_content} items con contenido de {len(items)} items encontrados para {url}")
                    if len(markdown_content) < 500:
                        logger.warning(f"⚠️ Markdown muy corto para {url} ({len(markdown_content)} chars). Puede que falte contenido.")
                    
                    return {
                        "success": True,
                        "markdown": markdown_content,
                        "html": sanitized_html,  # HTML sanitizado
                        "html_raw": raw_html,  # HTML original para paginación y debug
                        "url": result.url,
                        "html_length": len(raw_html),
                        "html_sanitized_length": len(sanitized_html),
                        "markdown_length": len(markdown_content)
                    }
                else:
                    error_msg = result.error_message or "Error desconocido en el crawling"
                    logger.error(f"Error al scrapear {url}: {error_msg}")
                    return {
                        "success": False,
                        "markdown": "",
                        "url": url,
                        "error": error_msg
                    }
                    
        except asyncio.TimeoutError:
            error_msg = f"Timeout al scrapear {url}"
            logger.error(error_msg)
            return {
                "success": False,
                "markdown": "",
                "url": url,
                "error": error_msg
            }
        except Exception as e:
            error_msg = f"Error inesperado al scrapear {url}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "markdown": "",
                "url": url,
                "error": error_msg
            }
    
    async def scrape_url_simple(self, url: str) -> Dict[str, Any]:
        """
        Scrapea una URL individual de forma simple, sin hooks complejos.
        Útil para páginas de concursos individuales que no requieren espera de contenido AJAX.
        
        Args:
            url: URL a scrapear
            
        Returns:
            Diccionario con el resultado del scraping
        """
        try:
            session_id = f"simple_{id(self)}_{int(asyncio.get_event_loop().time())}"
            
            browser_config = BrowserConfig(
                headless=self.headless,
                verbose=self.verbose
            )
            
            run_config = CrawlerRunConfig(
                page_timeout=self.page_timeout,
                wait_for=self.wait_for,
                cache_mode=self.cache_mode,
                session_id=session_id,
                word_count_threshold=self.word_count_threshold,
                wait_until=self.config.get("wait_until", "domcontentloaded"),
                wait_for_images=self.config.get("wait_for_images", False),
                screenshot=False
            )
            
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=url, config=run_config)
                
                if result.success:
                    # Usar markdown directamente (más simple para páginas individuales)
                    if result.markdown:
                        if hasattr(result.markdown, 'raw_markdown') and result.markdown.raw_markdown:
                            markdown_content = result.markdown.raw_markdown
                        elif hasattr(result.markdown, 'fit_markdown') and result.markdown.fit_markdown:
                            markdown_content = result.markdown.fit_markdown
                        else:
                            markdown_content = str(result.markdown)
                    else:
                        markdown_content = ""
                    
                    # Sanitizar HTML
                    raw_html = result.html if result.html else ""
                    from utils.html_sanitizer import sanitize_html
                    sanitized_html = sanitize_html(raw_html, preserve_structure=True)
                    
                    return {
                        "success": True,
                        "markdown": markdown_content,
                        "html": sanitized_html,
                        "html_raw": raw_html,
                        "url": result.url,
                        "html_length": len(raw_html),
                        "html_sanitized_length": len(sanitized_html),
                        "markdown_length": len(markdown_content)
                    }
                else:
                    error_msg = result.error_message or "Error desconocido en el crawling"
                    logger.error(f"Error al scrapear {url}: {error_msg}")
                    return {
                        "success": False,
                        "markdown": "",
                        "url": url,
                        "error": error_msg
                    }
                    
        except asyncio.TimeoutError:
            error_msg = f"Timeout al scrapear {url}"
            logger.error(error_msg)
            return {
                "success": False,
                "markdown": "",
                "url": url,
                "error": error_msg
            }
        except Exception as e:
            error_msg = f"Error inesperado al scrapear {url}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "markdown": "",
                "url": url,
                "error": error_msg
            }
    
    async def scrape_multiple_urls(self, urls: list[str]) -> list[Dict[str, Any]]:
        """
        Scrapea múltiples URLs en paralelo
        
        Args:
            urls: Lista de URLs a scrapear
            
        Returns:
            Lista de resultados (mismo formato que scrape_url)
        """
        tasks = [self.scrape_url(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Procesar excepciones
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append({
                    "success": False,
                    "markdown": "",
                    "url": urls[i],
                    "error": str(result)
                })
            else:
                processed_results.append(result)
        
        return processed_results

