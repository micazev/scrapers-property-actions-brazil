"""
Palácio dos Leilões Scraper

Scraper for https://www.palaciodosleiloes.com.br
"""

import asyncio
import re

from ...models import Imovel, ScrapingConfig
from ..base.registry import register_scraper
from ..base.scraper import PlaywrightScraper
from ..utils import clean_text, parse_currency


# enabled: custom extraction for the site's JS onclick navigation, verified
# against the live listing (categoria 13059 = imóveis).
@register_scraper("palaciodosleiloes", enabled=True)
class PalacioDoLeiloesScraper(PlaywrightScraper):
    """
    Scraper for Palácio dos Leilões auction website.

    This site uses JavaScript onclick handlers instead of anchor links,
    so we need custom extraction logic.
    """

    def __init__(self, headless: bool = True, max_properties: int = 2000):
        config = ScrapingConfig(
            source_name="palaciodosleiloes",
            headless=headless,
            timeout=120000,  # 2 minutes for loading all items
            max_retries=3,
            delay_between_requests=1.0,
            page_load_wait=5.0,
            screenshot_on_error=True,
            # Category 13059 = Imóveis (Real Estate)
            base_url="https://www.palaciodosleiloes.com.br/site/?categoria_pesquisa=13059",
        )
        super().__init__(config)
        self.max_properties = max_properties
        self.seen_lotes: set[str] = set()

    def get_source_name(self) -> str:
        return "palaciodosleiloes"

    async def scrape(self) -> list[Imovel]:
        """Execute scraping for Palácio dos Leilões."""
        properties = []

        async with self:
            try:
                self.logger.info(f"Navigating to {self.config.base_url}")
                await self.goto(self.config.base_url)
                await asyncio.sleep(self.config.page_load_wait)

                # Get initial count from page
                try:
                    count_element = await self.page.query_selector("#top_pes strong")
                    if count_element:
                        count_text = await count_element.text_content()
                        self.logger.info(f"Total items on site: {count_text}")
                except Exception:
                    pass

                # Click "Exibir todos" (Show all) to load all properties at once
                show_all_clicked = await self._click_show_all()
                if show_all_clicked:
                    self.logger.info("Clicked 'Exibir todos' - waiting for all items to load...")
                    # Wait longer for all items to load
                    await asyncio.sleep(10)
                else:
                    self.logger.warning("Could not find 'Exibir todos' button - will scrape visible items only")

                # Wait for cards to be present
                try:
                    await self.page.wait_for_selector("div.shadow.mb-4", timeout=15000)
                except Exception as e:
                    self.logger.warning(f"Timeout waiting for property cards: {e}")
                    await self.screenshot("no_cards")

                # Extract properties
                properties = await self._extract_all_properties()
                self.logger.info(f"Extracted {len(properties)} properties")

                if len(properties) == 0:
                    await self.screenshot("zero_properties")

            except Exception as e:
                self.log_error("Error during scraping", e)
                if self.config.screenshot_on_error:
                    await self.screenshot("error")
                raise

        return properties[: self.max_properties]

    async def _click_show_all(self) -> bool:
        """Click the 'Exibir todos' button to load all properties."""
        try:
            # Try multiple selectors for the show all button
            selectors = [
                "span.btn_exibir_todos",
                ".btn_exibir_todos",
                "span:has-text('Exibir todos')",
            ]

            for selector in selectors:
                try:
                    button = await self.page.query_selector(selector)
                    if button and await button.is_visible():
                        await button.click()
                        return True
                except Exception:
                    continue

            return False
        except Exception as e:
            self.log_error("Error clicking show all button", e)
            return False

    async def _extract_all_properties(self) -> list[Imovel]:
        """Extract all properties from the page."""
        properties = []

        try:
            # The property cards are divs with class "shadow mb-4"
            cards = await self.page.query_selector_all("div.shadow.mb-4")
            self.logger.info(f"Found {len(cards)} property cards")

            for i, card in enumerate(cards):
                try:
                    prop = await self._extract_property_from_card(card)
                    if prop and prop.link_de_acesso not in self.seen_lotes:
                        self.seen_lotes.add(prop.link_de_acesso)
                        properties.append(prop)
                except Exception as e:
                    self.log_error(f"Error extracting property {i}", e)
                    continue

            # Log if cards were found but no properties extracted
            if len(cards) > 0 and len(properties) == 0:
                self.logger.warning(f"Found {len(cards)} cards but extracted 0 properties - check selectors")

        except Exception as e:
            self.log_error("Error querying property cards", e)

        return properties

    async def _extract_property_from_card(self, card) -> Imovel | None:
        """Extract property data from a card element."""
        try:
            # Extract lote ID and leilao ID from onclick handler
            # Pattern: onclick="exibir_lote(1479630,8193)"
            image_container = await card.query_selector("div.i-c")
            if not image_container:
                return None

            onclick = await image_container.get_attribute("onclick")
            if not onclick:
                return None

            # Parse: exibir_lote(1479630,8193)
            match = re.search(r"exibir_lote\((\d+),\s*(\d+)\)", onclick)
            if not match:
                return None

            lote_id = match.group(1)
            leilao_id = match.group(2)

            # Construct unique link for this property
            link = f"https://www.palaciodosleiloes.com.br/site/?lote={lote_id}&leilao={leilao_id}"

            # Extract description from .quebraln
            descricao = ""
            desc_element = await card.query_selector("div.quebraln")
            if desc_element:
                descricao = clean_text(await desc_element.text_content())

            # Extract price from the h3 with gavel icon
            preco = 0.0
            price_element = await card.query_selector("div.h3.mt-3.text-center")
            if price_element:
                price_text = await price_element.text_content() or ""
                preco = parse_currency(price_text)

            # Extract location from .inf.small elements
            # Pattern: <div class="inf small">Juatuba <div class="float-right">02/02/26</div></div>
            cidade = ""
            uf = ""
            inf_elements = await card.query_selector_all("div.inf.small")
            for inf in inf_elements:
                text = await inf.text_content() or ""
                # Skip entries with known labels
                if any(
                    skip in text.lower()
                    for skip in ["leilão", "sequência", "lote", "visualizações", "lances", "compartilhar"]
                ):
                    continue
                # This is likely the location line (cidade + date)
                # Extract just the city part (before the date)
                parts = text.strip().split()
                if parts and not parts[0].isdigit():
                    cidade = parts[0]
                    break

            # Extract leiloeiro name from .inf element with logo
            leiloeiro_element = await card.query_selector("div.inf.p-1.bg-light")
            if leiloeiro_element:
                clean_text(await leiloeiro_element.text_content())

            # Try to get image URL
            img_url = ""
            img_element = await card.query_selector("img.i-p-l")
            if img_element:
                img_src = await img_element.get_attribute("src") or ""
                if img_src and not img_src.startswith("http"):
                    img_url = f"https://www.palaciodosleiloes.com.br/site/{img_src}"
                else:
                    img_url = img_src

            return Imovel(
                link_de_acesso=link,
                leiloeiro=self.get_source_name(),
                uf=uf or "MG",  # Default to MG based on site location (Juatuba-MG)
                cidade=cidade,
                descricao=descricao,
                preco=preco,
                valor_de_avaliacao=preco,
                modalidade_de_venda="Leilão",
                img_imovel=img_url or None,
            )

        except Exception as e:
            self.log_error("Error parsing property card", e)
            return None
