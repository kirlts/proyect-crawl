"""
Paginación dinámica específica para ANID usando JetEngine/Elementor.

Esta implementación maneja la paginación dinámica de ANID que requiere
hacer click en botones JavaScript y esperar a que el contenido AJAX se cargue.
"""

import asyncio
import logging
from typing import List, Dict, Any
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class AnidPagination:
    """
    Implementación de paginación dinámica para ANID.
    
    Maneja la paginación específica de ANID que usa JetEngine/Elementor
    con carga dinámica de contenido vía AJAX.
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        Inicializa la paginación ANID.
        
        Args:
            config: Configuración de Crawl4AI (opcional)
        """
        self.config = config or {}
        self.headless = self.config.get("headless", True)
        self.verbose = self.config.get("verbose", False)
        self.cache_mode_str = self.config.get("cache_mode", "BYPASS")
        self.cache_mode = CacheMode.BYPASS if self.cache_mode_str == "BYPASS" else CacheMode.ENABLED
    
    async def scrape_pages(
        self,
        url: str,
        max_pages: int,
        crawler: AsyncWebCrawler,
        config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Scrapea múltiples páginas usando paginación dinámica de ANID.
        
        Args:
            url: URL inicial
            max_pages: Número máximo de páginas
            crawler: Instancia de AsyncWebCrawler
            config: Configuración de Crawl4AI
            
        Returns:
            Lista de resultados (una entrada por página)
        """
        all_results = []
        session_id = f"pagination_{id(self)}_{int(asyncio.get_event_loop().time())}"
        
        # Scrapear primera página usando sesión
        logger.info(f"📄 Procesando página 1 de {max_pages} para {url}")
        
        # Configuración para primera página (con sesión)
        first_run_config = CrawlerRunConfig(
            session_id=session_id,
            cache_mode=self.cache_mode,
            markdown_generator=DefaultMarkdownGenerator(
                content_source="raw_html",
                content_filter=PruningContentFilter(
                    threshold=0.3,
                    threshold_type="dynamic",
                    min_word_threshold=5
                ),
                options={
                    "ignore_links": False,
                    "escape_html": True,
                }
            ),
            wait_for=config.get("wait_for", "css:.jet-listing-grid__item"),
            wait_until=config.get("wait_until", "domcontentloaded"),
            scan_full_page=config.get("scan_full_page", True),
        )
        
        # Agregar hook para primera página también
        captured_html_first = None
        async def before_retrieve_html_hook_first(page, context, **kwargs):
            nonlocal captured_html_first
            try:
                await page.wait_for_function(
                    """() => {
                        const items = document.querySelectorAll('.jet-listing-grid__item');
                        if (items.length < 6) return false;
                        let itemsWithContent = 0;
                        items.forEach((item) => {
                            const text = (item.innerText || item.textContent || '').trim();
                            const hasElementorContent = item.querySelector('[data-elementor-type="jet-listing-items"]');
                            const hasTitle = item.querySelector('h1, h2, h3, h4, h5, h6, .elementor-heading-title');
                            const hasDate = /\\d{1,2}\\s+de\\s+\\w+\\s*,\\s*\\d{4}|\\d{4}-\\d{2}-\\d{2}|noviembre|diciembre|octubre|cierre|apertura|inicio/i.test(text);
                            const hasLongText = text.length > 100;
                            if (hasElementorContent && hasLongText && (hasTitle || hasDate)) {
                                itemsWithContent++;
                            }
                        });
                        return itemsWithContent >= 6;
                    }""",
                    timeout=60000
                )
                await page.wait_for_timeout(2000)
                captured_html_first = await page.content()
            except:
                try:
                    captured_html_first = await page.content()
                except:
                    pass
            return page
        
        crawler.crawler_strategy.set_hook("before_retrieve_html", before_retrieve_html_hook_first)
        
        first_result_crawl = await crawler.arun(url=url, config=first_run_config)
        
        if first_result_crawl.success:
            raw_html = captured_html_first if captured_html_first else (first_result_crawl.html if first_result_crawl.html else "")
            
            if captured_html_first:
                import html2text
                h = html2text.HTML2Text()
                h.ignore_links = False
                h.escape_html = True
                h.body_width = 0
                markdown_content = h.handle(captured_html_first)
            else:
                markdown_content = first_result_crawl.markdown.raw_markdown if first_result_crawl.markdown else ""
            
            # Sanitizar HTML antes de guardarlo
            from utils.html_sanitizer import sanitize_html
            sanitized_html = sanitize_html(raw_html, preserve_structure=True)
            
            first_result = {
                "success": True,
                "markdown": markdown_content,
                "html": sanitized_html,
                "html_raw": raw_html,
                "url": url,
                "html_length": len(raw_html),
                "html_sanitized_length": len(sanitized_html),
                "markdown_length": len(markdown_content)
            }
            all_results.append(first_result)
            logger.info(f"✅ Página 1 procesada correctamente: {len(markdown_content)} chars markdown, {len(sanitized_html)} chars HTML sanitizado")
        else:
            logger.warning(f"⚠️ Error al procesar página 1: {first_result_crawl.error_message}")
            return all_results
        
        # Para páginas siguientes, usar sesión y hacer click en botones
        last_page_detected = False
        
        for page_num in range(2, max_pages + 1):
            # Verificar cancelación antes de procesar cada página
            from utils.scraping_state import get_should_stop
            if get_should_stop():
                logger.info(f"⚠️ Cancelación detectada. Deteniendo paginación en página {page_num}")
                break
            
            if last_page_detected:
                logger.info(f"⏹️ Última página ya detectada. Deteniendo paginación.")
                break
            
            logger.info(f"📄 Procesando página {page_num} de {max_pages} para {url}")
            
            # Verificar si existe el botón ">" ANTES de intentar hacer click
            if page_num >= 2 and len(all_results) > 0:
                previous_result = all_results[-1]
                previous_html = previous_result.get("html_raw") or previous_result.get("html", "")
                
                if previous_html:
                    try:
                        soup = BeautifulSoup(previous_html, 'html.parser')
                        pagination = soup.select_one('.jet-filters-pagination, .jet-smart-filters-pagination')
                        if pagination:
                            links = pagination.select('.jet-filters-pagination__link')
                            next_button = None
                            
                            for link in links:
                                text = link.get_text(strip=True)
                                if text in ['>', '»', '&gt;']:
                                    next_button = link
                                    break
                            
                            if not next_button:
                                page_numbers = []
                                for link in links:
                                    text = link.get_text(strip=True)
                                    try:
                                        num = int(text)
                                        page_numbers.append(num)
                                    except ValueError:
                                        pass
                                
                                max_page_available = max(page_numbers) if page_numbers else 0
                                current_page_item = pagination.select_one('.jet-filters-pagination__item.jet-filters-pagination__current, .jet-filters-pagination__item.active')
                                current_page_text = current_page_item.select_one('.jet-filters-pagination__link').get_text(strip=True) if current_page_item else ''
                                current_page_num = int(current_page_text) if current_page_text.isdigit() else page_num - 1
                                
                                logger.info(
                                    f"⏹️ Última página detectada en página {current_page_num}. "
                                    f"Máxima disponible: {max_page_available}. "
                                    f"No se encontró botón '>' (siguiente página)."
                                )
                                last_page_detected = True
                                break
                    except Exception as e:
                        logger.debug(f"⚠️ Error al verificar botón siguiente desde HTML anterior: {e}. Continuando...")
            
            # JavaScript para hacer click en el botón de la página siguiente
            js_click_next = f"""
            (() => {{
                const pagination = document.querySelector('.jet-filters-pagination, .jet-smart-filters-pagination');
                if (!pagination) {{
                    return {{success: false, reason: 'no_pagination'}};
                }}
                
                const firstItemBefore = document.querySelector('.jet-listing-grid__item');
                const firstTitleBefore = firstItemBefore ? (firstItemBefore.querySelector('h3, h2, .elementor-heading-title')?.textContent?.trim() || '') : '';
                const firstItemTextBefore = firstItemBefore ? ((firstItemBefore.innerText || firstItemBefore.textContent || '').trim().substring(0, 100)) : '';
                
                window.clickResult = {{
                    firstTitleBefore: firstTitleBefore,
                    firstItemTextBefore: firstItemTextBefore,
                    page: {page_num}
                }};
                
                const links = pagination.querySelectorAll('.jet-filters-pagination__link');
                let targetLink = null;
                
                targetLink = Array.from(links).find(link => {{
                    const text = link.textContent.trim();
                    return text === '{page_num}' || text === '{page_num}.';
                }});
                
                if (!targetLink && page_num > 1) {{
                    targetLink = Array.from(links).find(link => {{
                        const text = link.textContent.trim();
                        return text === '>' || text === '»' || text === '&gt;';
                    }});
                }}
                
                if (!targetLink) {{
                    const allLinks = Array.from(links).map(link => link.textContent.trim());
                    const pageNumbers = Array.from(links)
                        .map(link => {{
                            const text = link.textContent.trim();
                            const num = parseInt(text);
                            return isNaN(num) ? null : num;
                        }})
                        .filter(num => num !== null);
                    
                    const maxPageAvailable = pageNumbers.length > 0 ? Math.max(...pageNumbers) : 0;
                    const currentPageItem = pagination.querySelector('.jet-filters-pagination__item.jet-filters-pagination__current, .jet-filters-pagination__item.active');
                    const currentPageText = currentPageItem ? currentPageItem.querySelector('.jet-filters-pagination__link')?.textContent?.trim() : '';
                    const currentPageNum = parseInt(currentPageText) || {page_num};
                    const nextButtonExists = Array.from(links).some(link => {{
                        const text = link.textContent.trim();
                        return text === '>' || text === '»' || text === '&gt;';
                    }});
                    
                    const isLastPage = !nextButtonExists || (maxPageAvailable > 0 && currentPageNum >= maxPageAvailable);
                    
                    return {{
                        success: false, 
                        reason: isLastPage ? 'last_page_reached' : 'no_button', 
                        page: {page_num}, 
                        availableLinks: allLinks,
                        maxPageAvailable: maxPageAvailable,
                        currentPageNum: currentPageNum,
                        nextButtonExists: nextButtonExists,
                        isLastPage: isLastPage
                    }};
                }}
                
                const parentItem = targetLink.closest('.jet-filters-pagination__item');
                if (parentItem && (parentItem.classList.contains('jet-filters-pagination__current') || 
                                   parentItem.classList.contains('active'))) {{
                    return {{success: false, reason: 'already_on_page', page: {page_num}}};
                }}
                
                targetLink.click();
                
                return {{
                    success: true,
                    page: {page_num},
                    firstTitleBefore: firstTitleBefore,
                    firstItemTextBefore: firstItemTextBefore
                }};
            }})();
            """
            
            wait_for_change = """
            () => {
                const firstItem = document.querySelector('.jet-listing-grid__item');
                if (!firstItem) return false;
                
                const firstTitle = firstItem.querySelector('h3, h2, .elementor-heading-title')?.textContent?.trim() || '';
                const firstItemText = (firstItem.innerText || firstItem.textContent || '').trim().substring(0, 100);
                
                if (firstItemText.length < 100) return false;
                
                if (window.clickResult && window.clickResult.firstTitleBefore) {
                    const changed = firstTitle !== window.clickResult.firstTitleBefore && firstTitle.length > 0;
                    if (changed) {
                        console.log('Contenido cambió! Título antes:', window.clickResult.firstTitleBefore.substring(0, 50), 'Título ahora:', firstTitle.substring(0, 50));
                    }
                    return changed;
                }
                
                return firstTitle.length > 0 || firstItemText.length > 100;
            }
            """
            
            run_config = CrawlerRunConfig(
                session_id=session_id,
                js_code=js_click_next,
                js_only=True,
                cache_mode=self.cache_mode,
                markdown_generator=DefaultMarkdownGenerator(
                    content_source="raw_html",
                    content_filter=PruningContentFilter(
                        threshold=0.3,
                        threshold_type="dynamic",
                        min_word_threshold=5
                    ),
                    options={
                        "ignore_links": False,
                        "escape_html": True,
                    }
                ),
                wait_until="domcontentloaded",
                scan_full_page=True,
            )
            
            captured_html_page = None
            async def before_retrieve_html_hook_page(page, context, **kwargs):
                nonlocal captured_html_page
                from playwright.async_api import TimeoutError as PlaywrightTimeoutError
                
                try:
                    await page.wait_for_timeout(1000)
                    click_result_check = await page.evaluate("() => window.clickResult ? true : false")
                    if not click_result_check:
                        logger.warning(f"⚠️ Página {page_num}: window.clickResult no existe. Esperando un poco más...")
                        await page.wait_for_timeout(2000)
                    
                    logger.info(f"🔍 Página {page_num}: Esperando cambio de contenido")
                    
                    check_content_changed = """() => {
                        const items = document.querySelectorAll('.jet-listing-grid__item');
                        if (items.length === 0) return {ready: false, reason: 'no_items'};
                        
                        const firstItem = items[0];
                        const firstTitle = firstItem.querySelector('h3, h2, .elementor-heading-title')?.textContent?.trim() || '';
                        const firstItemText = (firstItem.innerText || firstItem.textContent || '').trim().substring(0, 100);
                        
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
                        
                        const titleChanged = window.clickResult && window.clickResult.firstTitleBefore && 
                                             firstTitle && firstTitle !== window.clickResult.firstTitleBefore;
                        const textChanged = window.clickResult && window.clickResult.firstItemTextBefore && 
                                            firstItemText && firstItemText !== window.clickResult.firstItemTextBefore;
                        
                        const isReady = (titleChanged || textChanged || !window.clickResult) && 
                                      (itemsWithContent >= 6 || totalTextLength > 5000) &&
                                      firstItemText.length >= 100;
                        
                        return {
                            ready: isReady,
                            itemsCount: items.length,
                            itemsWithContent: itemsWithContent,
                            totalTextLength: totalTextLength,
                            titleChanged: titleChanged,
                            textChanged: textChanged
                        };
                    }"""
                    
                    max_attempts = 120
                    attempt = 0
                    last_state = None
                    
                    while attempt < max_attempts:
                        state = await page.evaluate(check_content_changed)
                        
                        if state['ready']:
                            logger.info(
                                f"✅ Página {page_num}: Contenido listo después de {attempt * 0.5:.1f}s: "
                                f"{state['itemsWithContent']} items con contenido"
                            )
                            break
                        
                        if last_state and last_state == state and attempt % 10 == 0:
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                            await page.wait_for_timeout(500)
                            await page.evaluate("window.scrollTo(0, 0);")
                            logger.debug(f"🔄 Página {page_num}: Intento {attempt}, haciendo scroll para activar lazy loading")
                        
                        last_state = state
                        attempt += 1
                        await page.wait_for_timeout(500)
                    else:
                        final_state = await page.evaluate(check_content_changed)
                        logger.warning(f"⚠️ Página {page_num}: Timeout esperando contenido. Estado final: {final_state}")
                    
                    await page.wait_for_timeout(2000)
                    
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except:
                        pass
                    
                    captured_html_page = await page.content()
                    
                    soup_check = BeautifulSoup(captured_html_page, 'html.parser')
                    items = soup_check.select('.jet-listing-grid__item')
                    items_with_elementor = sum(1 for item in items if item.select_one('[data-elementor-type="jet-listing-items"]'))
                    logger.info(f"✅ HTML capturado para página {page_num}: {len(captured_html_page)} chars, {len(items)} items, {items_with_elementor} con Elementor")
                    
                except PlaywrightTimeoutError:
                    logger.warning(f"⚠️ Página {page_num}: Timeout esperando contenido, capturando HTML de todas formas")
                    try:
                        captured_html_page = await page.content()
                    except:
                        pass
                except Exception as e:
                    logger.warning(f"⚠️ Error en hook de página {page_num}: {e}", exc_info=True)
                    try:
                        captured_html_page = await page.content()
                    except:
                        pass
                return page
            
            crawler.crawler_strategy.set_hook("before_retrieve_html", before_retrieve_html_hook_page)
            
            try:
                result = await crawler.arun(url=url, config=run_config)
                
                # La verificación de última página se hace principalmente desde el HTML capturado
                # que se procesa más abajo
                
                if result.success:
                    captured_html = captured_html_page if captured_html_page else (result.html if result.html else "")
                    
                    soup_check = BeautifulSoup(captured_html, 'html.parser')
                    items = soup_check.select('.jet-listing-grid__item')
                    items_with_content = 0
                    items_with_elementor = 0
                    for item in items:
                        text = item.get_text(strip=True)
                        has_elementor = item.select_one('[data-elementor-type="jet-listing-items"]')
                        has_title = item.select_one('h1, h2, h3, h4, h5, h6, .elementor-heading-title')
                        has_date = bool(item.get_text() and any(word in text.lower() for word in ['noviembre', 'diciembre', 'octubre', 'cierre', 'apertura', 'inicio']))
                        
                        if has_elementor:
                            items_with_elementor += 1
                        if len(text) > 100 and (has_title or has_date):
                            items_with_content += 1
                    
                    logger.info(f"📊 Página {page_num}: {len(items)} items encontrados, {items_with_elementor} con Elementor, {items_with_content} con contenido")
                    
                    if items_with_content < 6:
                        logger.warning(f"⚠️ Página {page_num}: Solo {items_with_content} items con contenido. Puede que el contenido no se haya cargado correctamente.")
                    
                    if captured_html:
                        import html2text
                        h = html2text.HTML2Text()
                        h.ignore_links = False
                        h.escape_html = True
                        h.body_width = 0
                        markdown_content = h.handle(captured_html)
                    else:
                        markdown_content = result.markdown.raw_markdown if result.markdown else ""
                    
                    from utils.html_sanitizer import sanitize_html
                    sanitized_html = sanitize_html(captured_html, preserve_structure=True)
                    
                    try:
                        pagination_check = soup_check.select_one('.jet-filters-pagination, .jet-smart-filters-pagination')
                        if pagination_check:
                            links_check = pagination_check.select('.jet-filters-pagination__link')
                            next_button_exists = any(
                                link.get_text(strip=True) in ['>', '»', '&gt;'] 
                                for link in links_check
                            )
                            
                            if not next_button_exists:
                                page_numbers_check = []
                                for link in links_check:
                                    text = link.get_text(strip=True)
                                    try:
                                        num = int(text)
                                        page_numbers_check.append(num)
                                    except ValueError:
                                        pass
                                
                                max_page_check = max(page_numbers_check) if page_numbers_check else 0
                                current_page_item_check = pagination_check.select_one('.jet-filters-pagination__item.jet-filters-pagination__current, .jet-filters-pagination__item.active')
                                current_page_text_check = current_page_item_check.select_one('.jet-filters-pagination__link').get_text(strip=True) if current_page_item_check else ''
                                current_page_num_check = int(current_page_text_check) if current_page_text_check.isdigit() else page_num
                                
                                logger.info(
                                    f"⏹️ Última página detectada después de procesar página {current_page_num_check}. "
                                    f"Máxima disponible: {max_page_check}. "
                                    f"No se encontró botón '>' (siguiente página)."
                                )
                                last_page_detected = True
                    except Exception as check_error:
                        logger.debug(f"⚠️ Error al verificar botón siguiente después de procesar página {page_num}: {check_error}")
                    
                    page_result = {
                        "success": True,
                        "markdown": markdown_content,
                        "html": sanitized_html,
                        "html_raw": captured_html,
                        "url": url,
                        "html_length": len(captured_html),
                        "html_sanitized_length": len(sanitized_html),
                        "markdown_length": len(markdown_content)
                    }
                    all_results.append(page_result)
                    
                    if last_page_detected:
                        break
                    logger.info(f"✅ Página {page_num} procesada correctamente: {len(markdown_content)} chars markdown, {len(sanitized_html)} chars HTML sanitizado")
                else:
                    logger.warning(f"⚠️ Error al procesar página {page_num}: {result.error_message}")
                    error_msg_lower = str(result.error_message).lower() if result.error_message else ""
                    if "already_on_page" in error_msg_lower or "no_button" in error_msg_lower:
                        logger.info(f"ℹ️ No se encontró botón para página {page_num}. Probablemente se alcanzó la última página.")
                        last_page_detected = True
                    break
                    
            except Exception as e:
                logger.error(f"Error al procesar página {page_num}: {e}", exc_info=True)
                error_str = str(e).lower()
                if "last_page" in error_str or "no_button" in error_str:
                    last_page_detected = True
                break
        
        return all_results

