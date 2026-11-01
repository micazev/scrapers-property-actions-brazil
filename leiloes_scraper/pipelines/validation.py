"""
Validation Pipeline

Validates scraped items and drops invalid ones.
"""

import logging

from ..models import Imovel
from .base import ItemPipeline

logger = logging.getLogger("pipelines.validation")

# Valid Brazilian state codes
VALID_UF_CODES: set[str] = {
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
}


class ValidationError(Exception):
    """Raised when an item fails validation."""

    def __init__(self, message: str, field: str = None):
        super().__init__(message)
        self.field = field


class ValidationPipeline(ItemPipeline):
    """
    Validates scraped items.

    Validation rules:
    - Required fields: link_de_acesso, leiloeiro
    - UF must be a valid Brazilian state code (if provided)
    - preco and valor_de_avaliacao must be non-negative (if provided)

    Invalid items are dropped (returns None) with a warning.
    """

    def __init__(self, strict: bool = False):
        """
        Initialize validation pipeline.

        Args:
            strict: If True, raise ValidationError instead of dropping items
        """
        self.strict = strict
        self.validation_errors = 0
        self.items_validated = 0

    async def process_item(self, item: Imovel) -> Imovel | None:
        """Validate an item."""
        try:
            self._validate_required_fields(item)
            self._validate_uf(item)
            self._validate_prices(item)

            self.items_validated += 1
            return item

        except ValidationError as e:
            self.validation_errors += 1
            logger.warning(f"Validation failed for item: {e}. link_de_acesso={getattr(item, 'link_de_acesso', 'N/A')}")
            if self.strict:
                raise
            return None

    def _validate_required_fields(self, item: Imovel) -> None:
        """Validate required fields are present and non-empty."""
        if not item.link_de_acesso:
            raise ValidationError("link_de_acesso is required", "link_de_acesso")

        if not item.leiloeiro:
            raise ValidationError("leiloeiro is required", "leiloeiro")

    def _validate_uf(self, item: Imovel) -> None:
        """Validate UF is a valid Brazilian state code."""
        if item.uf:
            uf_upper = item.uf.upper().strip()
            if uf_upper and uf_upper not in VALID_UF_CODES:
                raise ValidationError(f"Invalid UF code: '{item.uf}'. Must be one of {VALID_UF_CODES}", "uf")

    def _validate_prices(self, item: Imovel) -> None:
        """Validate prices are non-negative."""
        if item.preco is not None and item.preco < 0:
            raise ValidationError(f"preco must be non-negative, got: {item.preco}", "preco")

        if item.valor_de_avaliacao is not None and item.valor_de_avaliacao < 0:
            raise ValidationError(
                f"valor_de_avaliacao must be non-negative, got: {item.valor_de_avaliacao}", "valor_de_avaliacao"
            )

    def close_pipeline(self) -> None:
        """Log validation stats."""
        if self.validation_errors > 0:
            logger.info(
                f"Validation complete: {self.items_validated} valid, {self.validation_errors} invalid (dropped)"
            )
