"""
Portal Zuk Scraper

Scraper for https://www.portalzuk.com.br/
"""

import asyncio
from urllib.parse import urljoin

from ...models import Imovel, ScrapingConfig
from ..base.registry import register_scraper
from ..base.scraper import PlaywrightScraper
from ..utils import clean_text, parse_currency


# enabled: site-specific selectors verified against the live card DOM
# (card-property / praça-1 vs praça-2 price labels / load-more button).
@register_scraper("portalzuk", enabled=True)
class PortalZukScraper(PlaywrightScraper):
    """Scraper for Portal Zuk auction website."""

    def __init__(self, headless: bool = True, max_properties: int = 1000):
        config = ScrapingConfig(
            source_name="portalzuk",
            headless=headless,
            timeout=60000,
            max_retries=3,
            delay_between_requests=0.8,
            base_url="https://www.portalzuk.com.br/leilao-de-imoveis",
            selectors={
                "property_cards": 'div.card-property, .property-card, [class*="card-property"]',
                "load_more_button": '#btn_carregarMais, .load-more, button[class*="carregar"]',
                "card_link": 'a[href*="/imovel/"], a[href*="leilao"]',
                "card_address": "address.card-property-address, .address, .endereco",
                "location_span": 'span[style*="font-weight: 600"], .cidade-estado',
                "address_span": 'span[style*="flex-basis: 100%"], .endereco-completo',
                "price_items": "li.card-property-price, .price-item",
                "price_label": "span.card-property-price-label, .price-label",
                "price_value": "span.card-property-price-value, .price-value",
                "property_title": "a[title], .titulo",
                "card_image": "img",
            },
        )
        super().__init__(config)
        self.max_properties = max_properties
        self.seen_links: set[str] = set()

    def get_source_name(self) -> str:
        return "portalzuk"

    async def scrape(self) -> list[Imovel]:
        """Execute scraping for Portal Zuk."""
        properties = []

        async with self:
            try:
                self.logger.info(f"Navigating to {self.config.base_url}")
                await self.goto(self.config.base_url)
                await asyncio.sleep(3)

                # Check for bot protection before proceeding
                if await self.check_for_bot_protection():
                    self.logger.error("Bot protection detected, aborting scrape")
                    return properties

                # Log page info for debugging
                await self.log_page_debug_info()

                load_more_attempts = 0
                max_load_more_attempts = 50

                while len(properties) < self.max_properties and load_more_attempts < max_load_more_attempts:
                    new_properties = await self._extract_properties()

                    for prop in new_properties:
                        if prop.link_de_acesso not in self.seen_links:
                            self.seen_links.add(prop.link_de_acesso)
                            properties.append(prop)

                    self.logger.info(
                        f"Attempt {load_more_attempts + 1}: extracted {len(new_properties)} new properties (total: {len(properties)})"
                    )

                    if not await self._click_load_more():
                        self.logger.info("No more 'Load More' button available, stopping")
                        break

                    load_more_attempts += 1
                    await asyncio.sleep(self.config.delay_between_requests)

            except Exception as e:
                self.logger.error(f"Error during scraping: {e}")
                if self.config.screenshot_on_error:
                    await self.screenshot("error")
                raise

        self.logger.info(f"Scraping complete: {len(properties)} properties found")
        return properties[: self.max_properties]

    async def _extract_properties(self) -> list[Imovel]:
        """Extract properties from current page state."""
        properties = []
        selector = self.config.selectors["property_cards"]

        try:
            await self.page.wait_for_selector(selector, timeout=10000)
        except Exception as wait_error:
            self.logger.warning(f"Timeout waiting for selector '{selector}': {wait_error}")
            # Take debug screenshot
            try:
                screenshot_path = await self.screenshot("selector_timeout")
                self.logger.info(f"Debug screenshot saved: {screenshot_path}")
            except Exception:
                pass

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
                    if prop:
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
                return None

            link_href = await link_element.get_attribute("href") or ""
            if not link_href:
                return None

            full_link = urljoin(self.config.base_url, link_href)

            # Skip if already seen
            if full_link in self.seen_links:
                return None

            # Extract title
            property_title = await link_element.get_attribute("title") or ""

            # Extract location
            endereco = ""
            cidade = ""
            uf = ""

            address_element = await card.query_selector(self.config.selectors["card_address"])
            if address_element:
                location_span = await address_element.query_selector(self.config.selectors["location_span"])
                if location_span:
                    location_text = await location_span.text_content() or ""
                    if " / " in location_text:
                        parts = location_text.split(" / ")
                        if len(parts) >= 2:
                            cidade = parts[0].strip()
                            estado_part = parts[1].strip()
                            if " - " in estado_part:
                                uf = estado_part.split(" - ")[0].strip()
                            else:
                                uf = estado_part[:2].upper()

                address_span = await address_element.query_selector(self.config.selectors["address_span"])
                if address_span:
                    endereco = clean_text(await address_span.text_content())

            # Extract prices
            valor_avaliacao = 0.0
            valor_minimo = 0.0

            price_items = await card.query_selector_all(self.config.selectors["price_items"])
            for price_item in price_items:
                label_element = await price_item.query_selector(self.config.selectors["price_label"])
                value_element = await price_item.query_selector(self.config.selectors["price_value"])

                if label_element and value_element:
                    label_text = (await label_element.text_content() or "").lower()
                    value_text = await value_element.text_content() or ""

                    if "1º leilão" in label_text or "avaliação" in label_text:
                        valor_avaliacao = parse_currency(value_text)
                    elif "2º leilão" in label_text or "mínimo" in label_text:
                        valor_minimo = parse_currency(value_text)

            # Extract image URL
            img_url = ""
            img_element = await card.query_selector(self.config.selectors["card_image"])
            if img_element:
                img_url = await img_element.get_attribute("src") or ""

            return Imovel(
                link_de_acesso=full_link,
                leiloeiro=self.get_source_name(),
                uf=uf or "SP",
                cidade=cidade or "São Paulo",
                endereco=endereco,
                descricao=property_title,
                valor_de_avaliacao=valor_avaliacao,
                preco=valor_minimo or valor_avaliacao,
                modalidade_de_venda="Leilão",
                img_imovel=img_url or None,
            )

        except Exception as e:
            self.logger.warning(f"Error extracting property from card: {e}")
            return None

    async def _click_load_more(self) -> bool:
        """Click the 'Load More' button if available."""
        try:
            load_more_button = await self.page.query_selector(self.config.selectors["load_more_button"])
            if load_more_button:
                is_visible = await load_more_button.is_visible()
                is_enabled = await load_more_button.is_enabled()

                if is_visible and is_enabled:
                    self.logger.debug("Clicking 'Load More' button")
                    await load_more_button.click()
                    await asyncio.sleep(2)
                    return True
                else:
                    self.logger.debug(f"Load more button not clickable: visible={is_visible}, enabled={is_enabled}")

            return False
        except Exception as e:
            self.logger.warning(f"Error clicking 'Load More' button: {e}")
            return False
