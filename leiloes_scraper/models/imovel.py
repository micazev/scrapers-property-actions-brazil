"""
Property Models

Data models for properties from different sources.
"""

from datetime import datetime

from pydantic import BaseModel


class Imovel(BaseModel):
    """
    Property model for imoveis table.
    Used by all scrapers including Caixa CSV and web scrapers.

    Maps directly to the imoveis table:
    - Primary key: link_de_acesso
    - Foreign key: leiloeiro (references leiloeiros.slug)
    """

    # Timestamps
    updated_at: datetime | None = None

    # Identifiers
    n_do_imovel: str | None = None
    link_de_acesso: str  # Primary key

    # Location
    uf: str | None = None
    cidade: str | None = None
    bairro: str | None = None
    endereco: str | None = None

    # Values
    preco: float | None = None
    valor_de_avaliacao: float | None = None
    desconto: float | None = None

    # Details
    descricao: str | None = None
    modalidade_de_venda: str
    tipo_imovel: str | None = None

    # Status
    status_leilao: bool | None = None

    # Foreign key - references leiloeiros.slug
    # This should be the scraper filename without extension (e.g., "gpleiloes", "portalzuk", "caixa")
    leiloeiro: str | None = None

    # Property image URL
    img_imovel: str | None = None

    # PDF link for property registration document (matrícula)
    link_pdf_matricula: str | None = None

    def to_db_dict(self) -> dict:
        """Convert to dictionary for database insertion."""
        from ..scrapers.utils import extract_property_type, normalize_cidade

        data = self.model_dump(exclude_none=True)
        # Keep a native datetime: asyncpg binds it to the timestamptz column
        # directly and rejects ISO strings.
        data["updated_at"] = datetime.now()

        # A scraped listing is active by definition. Setting it here (rather than
        # via a separate blanket "mark all of this leiloeiro active" UPDATE after
        # the upsert) lets the diff-guarded upsert both activate and skip
        # unchanged rows in one write. exclude_none drops it above unless a
        # scraper explicitly set it, so a deliberate False is still respected.
        data.setdefault("status_leilao", True)

        # Always populate tipo_imovel based on descricao
        # Extract from description or default to "outros"
        data["tipo_imovel"] = extract_property_type(self.descricao or "")

        # Canonicalize cidade on write so scraper output matches the
        # accent-stripped + uppercased form the frontend compares against.
        if "cidade" in data:
            data["cidade"] = normalize_cidade(data["cidade"])

        # Generate matrícula PDF URL for Caixa properties
        if self.leiloeiro == "caixa" and self.n_do_imovel and self.uf:
            data["link_pdf_matricula"] = (
                f"https://venda-imoveis.caixa.gov.br/editais/matricula/{self.uf}/{self.n_do_imovel}.pdf"
            )

        return data


# Alias for backward compatibility
ImovelScraper = Imovel
