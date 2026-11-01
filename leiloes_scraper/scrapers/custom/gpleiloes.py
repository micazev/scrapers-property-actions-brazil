"""
GP Leilões Scraper

Scraper for https://www.gpleiloes.com.br/
"""

import asyncio

from ...models import Imovel, ScrapingConfig
from ..base.registry import register_scraper
from ..base.scraper import PlaywrightScraper
from ..utils import clean_text, parse_currency, parse_location


# enabled: site-specific selectors (.incompleto / .cardTitulo / .imoney)
# verified against the live SPA listing.
@register_scraper("gpleiloes", enabled=True)
class GPLeiloesScraper(PlaywrightScraper):
    """Scraper for GP Leilões auction website."""

    def __init__(self, headless: bool = True, max_properties: int = 1000):
        config = ScrapingConfig(
            source_name="gpleiloes",
            headless=headless,
            timeout=60000,
            max_retries=3,
            delay_between_requests=1.0,
            base_url="https://www.gpleiloes.com.br/gp/#/pesquisa?categorias=6&",
            selectors={
                "property_cards": ".incompleto",
                "card_link": "a.link-interno-direto",
                "card_title": ".cardTitulo",
                "card_price": ".imoney",
                "card_location": ".localizacao.destaque",
                "card_image": ".cardImage img",
                "load_more": ".load-more, .carregar-mais",
                "pagination_next": ".icon-caret-right",
            },
        )
        super().__init__(config)
        self.max_properties = max_properties
        self.seen_links: set[str] = set()

    def get_source_name(self) -> str:
        return "gpleiloes"

    async def _close_popups(self) -> None:
        """Close any popups or modals that may appear on the page."""
        popup_selectors = [
            "button.close",
            ".modal-close",
            '[aria-label="Close"]',
            '[aria-label="Fechar"]',
            ".btn-close",
            ".fechar",
            'button[class*="close"]',
            'a[class*="close"]',
            ".modal .close",
            ".popup .close",
            '[data-dismiss="modal"]',
            ".swal2-close",
            ".swal2-confirm",
            ".fancybox-close",
            ".fancybox-close-small",
            'button:has-text("Fechar")',
            'button:has-text("OK")',
            'button:has-text("Entendi")',
            'button:has-text("Aceitar")',
            ".overlay-close",
            '[class*="popup"] button',
            '[class*="modal"] button',
        ]

        for selector in popup_selectors:
            try:
                elements = await self.page.query_selector_all(selector)
                for element in elements:
                    if await element.is_visible():
                        self.logger.info(f"Closing popup with selector: {selector}")
                        await element.click()
                        await asyncio.sleep(0.5)
            except Exception:
                continue

        # Also try pressing Escape key to close any modal
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        except Exception:
            pass

    async def scrape(self) -> list[Imovel]:
        """Execute scraping for GP Leilões."""
        properties = []

        async with self:
            try:
                self.logger.info(f"Navigating to {self.config.base_url}")
                await self.goto(self.config.base_url)
                await asyncio.sleep(3)

                # Close any popups that may appear
                await self._close_popups()
                await asyncio.sleep(1)

                # Check for bot protection before proceeding
                if await self.check_for_bot_protection():
                    self.logger.error("Bot protection detected, aborting scrape")
                    return properties

                # Log page info for debugging
                await self.log_page_debug_info()

                # Try to find and process property listings
                page_num = 1
                max_pages = 50

                while len(properties) < self.max_properties and page_num <= max_pages:
                    # Extract properties from current page
                    new_properties = await self._extract_properties()

                    if not new_properties:
                        self.logger.info(f"Page {page_num}: no properties found, stopping")
                        break

                    properties.extend(new_properties)
                    self.logger.info(
                        f"Page {page_num}: extracted {len(new_properties)} properties (total: {len(properties)})"
                    )

                    # Try pagination or load more
                    if not await self._navigate_next_page():
                        self.logger.info("No more pages available")
                        break

                    page_num += 1
                    await asyncio.sleep(self.config.delay_between_requests)

            except Exception as e:
                self.logger.error(f"Error during scraping: {e}")
                if self.config.screenshot_on_error:
                    await self.screenshot("error")
                raise

        self.logger.info(f"Scraping complete: {len(properties)} properties found")
        return properties[: self.max_properties]

    async def _extract_properties(self) -> list[Imovel]:
        """Extract properties from current page."""
        properties = []
        selector = self.config.selectors["property_cards"]

        try:
            cards = await self.page.query_selector_all(selector)
            self.logger.info(f"Found {len(cards)} property cards using selector: {selector}")

            if len(cards) == 0:
                self.logger.warning(f"No property cards found on page: {self.page.url}")
                # Take debug screenshot for zero results
                try:
                    screenshot_path = await self.screenshot("zero_cards")
                    self.logger.info(f"Debug screenshot saved: {screenshot_path}")
                except Exception:
                    pass

            for i, card in enumerate(cards):
                try:
                    prop = await self._extract_property_from_card(card)
                    if prop and prop.link_de_acesso not in self.seen_links:
                        self.seen_links.add(prop.link_de_acesso)
                        properties.append(prop)
                except Exception as card_error:
                    self.logger.warning(f"Error extracting property from card {i}: {card_error}")
                    continue

        except Exception as e:
            self.logger.error(f"Error querying property cards: {e}")

        return properties

    async def _extract_property_from_card(self, card) -> Imovel | None:
        """Extract property data from a card element."""
        try:
            # Extract link
            link_element = await card.query_selector(self.config.selectors["card_link"])
            if not link_element:
                link_element = await card.query_selector("a")

            link_href = ""
            if link_element:
                link_href = await link_element.get_attribute("href") or ""
                # Handle hash-based routes like #/lote/3647/1
                if link_href:
                    if link_href.startswith("#/"):
                        link_href = f"https://www.gpleiloes.com.br/gp/{link_href}"
                    elif not link_href.startswith("http"):
                        link_href = f"https://www.gpleiloes.com.br{link_href}"

            if not link_href:
                return None

            # Extract title/description from cardTitulo
            title_element = await card.query_selector(self.config.selectors["card_title"])
            descricao = ""
            if title_element:
                # Get the title attribute which contains the full description
                descricao = await title_element.get_attribute("title") or ""
                if not descricao:
                    descricao = clean_text(await title_element.text_content())

            # Extract price (first price shown is usually the starting price)
            price_element = await card.query_selector(self.config.selectors["card_price"])
            preco = 0.0
            if price_element:
                price_text = await price_element.text_content() or ""
                preco = parse_currency(price_text)

            # Extract location (format: CIDADE/UF)
            location_element = await card.query_selector(self.config.selectors["card_location"])
            cidade, uf, bairro = "", "", ""
            if location_element:
                location_text = await location_element.text_content() or ""
                cidade, uf, bairro = parse_location(location_text)

            # Extract image
            img_element = await card.query_selector(self.config.selectors["card_image"])
            img_url = ""
            if img_element:
                img_url = await img_element.get_attribute("src") or ""

            # Determine property type

            return Imovel(
                link_de_acesso=link_href,
                leiloeiro=self.get_source_name(),
                uf=uf or "MG",
                cidade=cidade,
                bairro=bairro,
                descricao=descricao,
                preco=preco,
                valor_de_avaliacao=preco,
                modalidade_de_venda="Leilão",
                img_imovel=img_url or None,
            )

        except Exception as e:
            self.logger.warning(f"Error extracting property from card: {e}")
            return None

    async def _navigate_next_page(self) -> bool:
        """Try to navigate to next page."""
        try:
            # Try load more button
            load_more = await self.page.query_selector(self.config.selectors["load_more"])
            if load_more and await load_more.is_visible():
                self.logger.debug("Clicking 'Load More' button")
                await load_more.click()
                await asyncio.sleep(2)
                return True

            # Try pagination - find the next button (icon-caret-right)
            next_icon = await self.page.query_selector(self.config.selectors["pagination_next"])
            if next_icon:
                # Get the parent <a> element to click
                parent_link = await next_icon.evaluate_handle('el => el.closest("a")')
                if parent_link:
                    is_visible = await parent_link.evaluate("el => el.offsetParent !== null")
                    if is_visible:
                        self.logger.debug("Clicking next page link")
                        await parent_link.click()
                        await asyncio.sleep(2)
                        # Close any popups that may appear after navigation
                        await self._close_popups()
                        return True

        except Exception as e:
            self.logger.warning(f"Error navigating to next page: {e}")

        return False
