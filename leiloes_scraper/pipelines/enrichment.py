"""
Enrichment Pipelines

Pipelines that enrich items with additional data.
"""

import logging

from ..models import Imovel
from ..scrapers.utils import extract_property_type
from .base import ItemPipeline

logger = logging.getLogger("pipelines.enrichment")


class GeocodeEnrichmentPipeline(ItemPipeline):
    """
    Placeholder for geocoding enrichment.

    Future implementation will:
    - Geocode addresses to lat/lng coordinates
    - Update geocode_status field
    - Store detailed results in imoveis_geocoding table
    """

    async def process_item(self, item: Imovel) -> Imovel | None:
        """
        Placeholder - returns item unchanged.

        Future: Add geocoding logic here.
        """
        # TODO: Implement geocoding
        # - Build full address from endereco, bairro, cidade, uf
        # - Call geocoding API (Google, Nominatim, etc.)
        # - Update item with lat/lng if successful
        # - Set geocode_status = True/False
        return item


class PropertyTypeEnrichmentPipeline(ItemPipeline):
    """
    Extracts and enriches property type from description.

    Uses the extract_property_type utility to identify property types
    from the description text (apartamento, casa, terreno, etc.).
    """

    def __init__(self):
        self.items_enriched = 0

    async def process_item(self, item: Imovel) -> Imovel | None:
        """
        Enrich item with property type.

        Extracts property type from description if not already set.
        """
        # Only extract if description exists
        if item.descricao:
            extracted_type = extract_property_type(item.descricao)
            if extracted_type:
                # We don't have a tipo_imovel field on Imovel currently,
                # but we could add extracted info to description or a new field
                # For now, this pipeline demonstrates the enrichment pattern
                self.items_enriched += 1
                logger.debug(f"Extracted property type '{extracted_type}' from: {item.descricao[:50]}...")

        return item

    def close_pipeline(self) -> None:
        """Log enrichment stats."""
        if self.items_enriched > 0:
            logger.info(f"Property type enrichment: {self.items_enriched} items enriched")


class StatusLeilaoPipeline(ItemPipeline):
    """
    Sets the status_leilao field for scraped properties.

    All properties scraped from active listing pages should have status_leilao = True,
    as they represent currently available auctions.

    This pipeline is designed to be modular and reusable across all scrapers.
    """

    def __init__(self, default_status: bool = True):
        """
        Initialize the pipeline.

        Args:
            default_status: Default value for status_leilao.
                           True = active auction (default for scraped items)
                           False = inactive/ended auction
        """
        self.default_status = default_status
        self.items_processed = 0

    async def process_item(self, item: Imovel) -> Imovel | None:
        """
        Set status_leilao for the item.

        Only sets the status if it's currently None (not already set).
        This allows scrapers to explicitly set different values if needed.
        """
        if item.status_leilao is None:
            item.status_leilao = self.default_status
            self.items_processed += 1
            logger.debug(f"Set status_leilao={self.default_status} for: {item.link_de_acesso}")

        return item

    def close_pipeline(self) -> None:
        """Log stats."""
        if self.items_processed > 0:
            logger.info(f"Status leilão set for {self.items_processed} items (status={self.default_status})")


class DefaultValuesPipeline(ItemPipeline):
    """
    Fills in default values for missing fields.

    Useful for ensuring data consistency before database insertion.
    """

    def __init__(
        self,
        default_modalidade: str = "Leilão",
    ):
        self.default_modalidade = default_modalidade
        self.defaults_applied = 0

    async def process_item(self, item: Imovel) -> Imovel | None:
        """Apply default values to missing fields."""
        applied = False

        # Ensure modalidade_de_venda has a value
        if not item.modalidade_de_venda:
            item.modalidade_de_venda = self.default_modalidade
            applied = True

        # Ensure prices are at least 0, not None
        if item.preco is None:
            item.preco = 0.0
            applied = True

        if item.valor_de_avaliacao is None:
            item.valor_de_avaliacao = 0.0
            applied = True

        if applied:
            self.defaults_applied += 1

        return item

    def close_pipeline(self) -> None:
        """Log stats."""
        if self.defaults_applied > 0:
            logger.info(f"Default values applied to {self.defaults_applied} items")
