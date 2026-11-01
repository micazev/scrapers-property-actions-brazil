"""
Franco Leilões Scraper

Scraper for https://www.francoleiloes.com.br/
"""

import re

from ...models import Imovel, ScrapingConfig
from ..base.registry import register_scraper
from ..base.scraper import GenericPaginationScraper
from ..utils import clean_text, extract_state_from_text, parse_currency


# enabled: site-specific selectors (.dg-leiloes-*) with custom praça-1/praça-2
# price logic and JS-function pagination, verified against the live site.
@register_scraper("francoleiloes", enabled=True)
class FrancoLeiloesScraper(GenericPaginationScraper):
    """Scraper for Franco Leilões auction website."""

    def __init__(self, headless: bool = True, max_properties: int = 1000):
        config = ScrapingConfig(
            source_name="francoleiloes",
            headless=headless,
            timeout=60000,
            max_retries=3,
            delay_between_requests=1.0,
            base_url="https://www.francoleiloes.com.br/leiloes",
            selectors={
                "property_cards": ".dg-leiloes-item-col",
                "card_link": "a.dg-leiloes-img",
                "card_title": ".dg-leiloes-nome-subleilao",
                "card_price": ".dg-leiloes-valor-lance strong",
                "card_location": ".dg-leiloes-nome-subleilao",
                "card_image": "a.dg-leiloes-img span",
                "pagination": ".dg-paginacao",
            },
        )
        super().__init__(
            config=config,
            max_properties=max_properties,
            default_uf="SP",
            default_cidade="São Paulo",
        )
        self.current_page = 1

    def get_source_name(self) -> str:
        return "francoleiloes"

    async def _extract_property_from_card(self, card) -> Imovel | None:
        """
        Custom extraction for Franco Leilões card structure.

        Handles:
        - Price logic: Use Praca2 if Praca1 has 'dg-opacidade' class
        - Image from background-image style
        - Location from subleilao title
        """
        try:
            # Extract link
            link_element = await card.query_selector("a.dg-leiloes-img")
            if not link_element:
                return None

            link_href = await link_element.get_attribute("href") or ""
            if not link_href:
                return None

            if not link_href.startswith("http"):
                link_href = f"https://www.francoleiloes.com.br{link_href}"

            # Extract title/description from subleilao
            title_element = await card.query_selector(".dg-leiloes-nome-subleilao")
            descricao = ""
            if title_element:
                descricao = clean_text(await title_element.text_content())

            # Extract auction name
            leilao_element = await card.query_selector(".dg-leiloes-nome-leilao")
            leilao_name = ""
            if leilao_element:
                leilao_name = clean_text(await leilao_element.text_content())

            # Parse location from description (format: "Cidade/UF - Bairro - Property type")
            cidade, uf, bairro = self._parse_location_from_description(descricao)

            # Extract price with Praca1/Praca2 logic
            preco = await self._extract_price(card)

            # Extract evaluation value
            avaliacao_element = await card.query_selector(".dg-leiloes-valor-avaliacao strong")
            valor_avaliacao = 0.0
            if avaliacao_element:
                avaliacao_text = await avaliacao_element.text_content() or ""
                valor_avaliacao = parse_currency(avaliacao_text)

            # Extract image from background-image style
            img_url = await self._extract_image_url(card)

            # Combine leilao name and description for full description
            full_descricao = descricao
            if leilao_name and leilao_name not in descricao:
                full_descricao = f"{leilao_name} - {descricao}"

            return Imovel(
                link_de_acesso=link_href,
                leiloeiro="francoleiloes",
                uf=uf or self.default_uf,
                cidade=cidade or self.default_cidade,
                bairro=bairro,
                descricao=full_descricao,
                preco=preco,
                valor_de_avaliacao=valor_avaliacao if valor_avaliacao > 0 else preco,
                modalidade_de_venda="Leilão",
                img_imovel=img_url or None,
            )

        except Exception as e:
            self.log_error("Error parsing Franco Leilões property card", e)
            return None

    async def _extract_price(self, card) -> float:
        """
        Extract price using Praca1/Praca2 logic.

        If Praca1 has 'dg-opacidade' class (1st auction ended), use Praca2 price.
        Otherwise, use Praca1 price.
        """
        try:
            # Check if Praca1 has dg-opacidade class (meaning 1st auction ended)
            praca1_element = await card.query_selector(".Praca1")
            use_praca2 = False

            if praca1_element:
                praca1_class = await praca1_element.get_attribute("class") or ""
                if "dg-opacidade" in praca1_class:
                    use_praca2 = True

            # Try to get price from Lance Mínimo first (most reliable)
            lance_minimo = await card.query_selector(".dg-leiloes-valor-lance strong")
            if lance_minimo:
                lance_text = await lance_minimo.text_content() or ""
                preco = parse_currency(lance_text)
                if preco > 0:
                    return preco

            # If no Lance Mínimo, try to get from the appropriate Praca
            if use_praca2:
                # Try Praca2 lance inicial
                praca2_lance = await card.query_selector(".Praca2 .dg-leiloes-lance-incial strong")
                if praca2_lance:
                    lance_text = await praca2_lance.text_content() or ""
                    preco = parse_currency(lance_text)
                    if preco > 0:
                        return preco
            else:
                # Try Praca1 lance inicial
                praca1_lance = await card.query_selector(".Praca1 .dg-leiloes-lance-incial strong")
                if praca1_lance:
                    lance_text = await praca1_lance.text_content() or ""
                    preco = parse_currency(lance_text)
                    if preco > 0:
                        return preco

            return 0.0

        except Exception as e:
            self.log_error("Error extracting price", e)
            return 0.0

    async def _extract_image_url(self, card) -> str:
        """Extract image URL from background-image style."""
        try:
            # Image is in a span with background-image style inside the link
            img_span = await card.query_selector('a.dg-leiloes-img span[style*="background-image"]')
            if img_span:
                style = await img_span.get_attribute("style") or ""
                # Parse url from: background-image: url(/imagens-center/350x282/xxx.jpg)
                match = re.search(r'url\([\'"]?([^\'")\s]+)[\'"]?\)', style)
                if match:
                    img_path = match.group(1)
                    if not img_path.startswith("http"):
                        return f"https://www.francoleiloes.com.br{img_path}"
                    return img_path
            return ""
        except Exception:
            return ""

    def _parse_location_from_description(self, descricao: str) -> tuple:
        """
        Parse location from description text.

        Format: "Cidade/UF - Bairro - Property type" or "Cidade/UF"
        Example: "Águas Lindas de Goiás/GO - Parque da Barragem - Casa 150m²"
        """
        cidade = ""
        uf = ""
        bairro = ""

        if not descricao:
            return (cidade, uf, bairro)

        # Split by " - " to separate parts
        parts = descricao.split(" - ")

        if len(parts) >= 1:
            # First part should be "Cidade/UF"
            location_part = parts[0].strip()

            # Try to extract cidade/UF pattern
            if "/" in location_part:
                loc_parts = location_part.rsplit("/", 1)
                cidade = loc_parts[0].strip()
                uf = loc_parts[1].strip()[:2].upper()
            else:
                cidade = location_part
                # Try to extract state from entire description
                uf = extract_state_from_text(descricao)

        if len(parts) >= 2:
            # Second part is usually the bairro
            bairro = parts[1].strip()

        return (cidade, uf, bairro)

    async def _navigate_next_page(self) -> bool:
        """
        Navigate to next page using JavaScript pagination.

        Franco Leilões uses JavaScript function: GetLeiloesDestaquePG(page_number)
        """
        try:
            # Check if there's a next page link in pagination
            pagination = await self.page.query_selector(".dg-paginacao")
            if not pagination:
                return False

            # Get all page links
            page_links = await pagination.query_selector_all("a")
            if not page_links:
                return False

            # Find next page number
            self.current_page += 1

            # Check if next page link exists
            next_page_exists = False
            for link in page_links:
                onclick = await link.get_attribute("onclick") or ""
                if f"GetLeiloesDestaquePG({self.current_page})" in onclick:
                    next_page_exists = True
                    break

            if not next_page_exists:
                return False

            # Execute JavaScript to load next page
            self.logger.info(f"Navigating to page {self.current_page}")
            await self.page.evaluate(f"GetLeiloesDestaquePG({self.current_page})")

            # Wait for content to load
            import asyncio

            await asyncio.sleep(2)

            return True

        except Exception as e:
            self.log_error("Error navigating to next page", e)
            return False
