"""
Deduplication Pipelines

Two layers:
- DeduplicationPipeline: within-run duplicates (same link_de_acesso scraped
  twice, e.g. a lot listed under two categories on the same site).
- CrossSourceDeduplicationPipeline: cross-source duplicates (the same physical
  property listed by Caixa *and* an auctioneer under different URLs), matched
  by a normalized location key against the canonical sources already in the
  database.
"""

import logging
import re
import unicodedata

from ..models import Imovel
from .base import ItemPipeline

logger = logging.getLogger("pipelines.deduplication")

# Sources whose listings are considered canonical for a property. Caixa is the
# bank's own listing with the direct link, so an auctioneer listing of the same
# property is treated as the duplicate, never the other way around.
CANONICAL_SOURCES: tuple[str, ...] = ("caixa",)

# Leading street-type tokens dropped during address normalization so
# "R. X, 123" and "RUA X 123" produce the same key.
_STREET_TYPE_TOKENS = {
    "RUA",
    "R",
    "AVENIDA",
    "AV",
    "AVN",
    "ALAMEDA",
    "AL",
    "TRAVESSA",
    "TV",
    "ESTRADA",
    "EST",
    "RODOVIA",
    "ROD",
    "PRACA",
    "PCA",
    "PC",
}

# Number-marker tokens dropped so "N 123" / "Nº 123" / "NUMERO 123" == "123".
_NUMBER_MARKER_TOKENS = {"N", "NO", "NUM", "NUMERO"}

_CEP_RE = re.compile(r"\b\d{5}\s*-?\s*\d{3}\b")


def _strip_accents_upper(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").upper()


def build_dedup_key(uf: str | None, cidade: str | None, endereco: str | None) -> str | None:
    """
    Build a conservative cross-source match key from location fields.

    Returns None when the fields are too weak to identify a single property
    (missing pieces, no street number, very short address) — a missed match is
    acceptable; collapsing two *different* properties is not.
    """
    if not uf or not cidade or not endereco:
        return None

    uf_norm = _strip_accents_upper(uf.strip())[:2]
    cidade_norm = _strip_accents_upper(cidade.strip())
    if not uf_norm or not cidade_norm:
        return None

    endereco_norm = _strip_accents_upper(endereco)
    # CEP formats vary between sources (present/absent, hyphenated or not);
    # drop them so the street address decides the match.
    endereco_norm = _CEP_RE.sub(" ", endereco_norm)
    endereco_norm = endereco_norm.replace("CEP", " ")
    # Punctuation to spaces, then tokenize.
    endereco_norm = re.sub(r"[^A-Z0-9]+", " ", endereco_norm)
    tokens = endereco_norm.split()

    if tokens and tokens[0] in _STREET_TYPE_TOKENS:
        tokens = tokens[1:]
    tokens = [t for t in tokens if t not in _NUMBER_MARKER_TOKENS]

    endereco_key = " ".join(tokens)

    # Quality gate: require a street number and a minimally specific address,
    # otherwise vague values like "CENTRO" or "QUADRA 5" would collapse
    # distinct properties. Three tokens still admits short real addresses
    # like "T 63 500" (Goiânia grid streets).
    if len(tokens) < 3 or len(endereco_key) < 8 or not any(c.isdigit() for c in endereco_key):
        return None

    return f"{uf_norm}|{cidade_norm}|{endereco_key}"


class DeduplicationPipeline(ItemPipeline):
    """
    Removes duplicate items.

    Tracks seen link_de_acesso values and drops duplicates.
    The first occurrence of each link is kept.
    """

    def __init__(self):
        self.seen_links: set[str] = set()
        self.duplicates_removed = 0
        self.unique_items = 0

    async def process_item(self, item: Imovel) -> Imovel | None:
        """
        Check if item is a duplicate.

        Returns:
            The item if unique, None if duplicate
        """
        link = item.link_de_acesso

        if not link:
            # Items without links can't be deduplicated
            logger.warning("Item has no link_de_acesso, cannot deduplicate")
            return item

        if link in self.seen_links:
            self.duplicates_removed += 1
            logger.debug(f"Duplicate item dropped: {link}")
            return None

        self.seen_links.add(link)
        self.unique_items += 1
        return item

    def open_pipeline(self) -> None:
        """Reset seen links at start of scraping."""
        self.seen_links.clear()
        self.duplicates_removed = 0
        self.unique_items = 0

    def close_pipeline(self) -> None:
        """Log deduplication stats."""
        if self.duplicates_removed > 0:
            logger.info(
                f"Deduplication complete: {self.unique_items} unique, {self.duplicates_removed} duplicates removed"
            )

    def get_stats(self) -> dict:
        """Get deduplication statistics."""
        return {
            "unique_items": self.unique_items,
            "duplicates_removed": self.duplicates_removed,
            "total_seen": len(self.seen_links),
        }


class CrossSourceDeduplicationPipeline(ItemPipeline):
    """
    Drops items that duplicate a property already listed by a canonical source.

    The same physical property is frequently listed on Caixa's own portal
    *and* on the auctioneer's site under a different URL; keyed only by
    link_de_acesso they become two imoveis rows and two cards on the map.
    This pipeline matches incoming items against the canonical sources'
    active listings using a normalized uf|cidade|endereco key and drops the
    non-canonical copy (logged, never silently).

    Failure-safe: if the canonical index cannot be loaded (e.g. no database
    in a local dry run), items pass through unchanged — ingestion is never
    blocked by dedup.
    """

    def __init__(
        self,
        source_name: str,
        canonical_sources: tuple[str, ...] = CANONICAL_SOURCES,
        canonical_index: dict[str, str] | None = None,
    ):
        """
        Args:
            source_name: leiloeiro slug of the scraper feeding this pipeline
            canonical_sources: sources whose listings win a duplicate match
            canonical_index: pre-built {dedup_key: canonical_link} map
                (injected in tests; loaded lazily from the DB otherwise)
        """
        self.source_name = source_name
        self.canonical_sources = tuple(canonical_sources)
        self._canonical_index = canonical_index
        self._index_load_failed = False
        self.cross_source_duplicates: list[tuple[str, str]] = []

    async def _get_canonical_index(self) -> dict[str, str] | None:
        """Lazily load {dedup_key: link} for canonical sources' active rows."""
        if self._canonical_index is not None or self._index_load_failed:
            return self._canonical_index

        from ..storage import using_database

        if not using_database():
            # JSON-file mode has no canonical listings to match against.
            self._index_load_failed = True
            return None

        try:
            from ..database import get_cross_source_rows

            rows = await get_cross_source_rows(list(self.canonical_sources))
            index: dict[str, str] = {}
            for row in rows:
                key = build_dedup_key(row.get("uf"), row.get("cidade"), row.get("endereco"))
                if key:
                    index[key] = row["link_de_acesso"]
            self._canonical_index = index
            logger.info(
                f"Cross-source dedup index loaded: {len(index)} keyed listings from {list(self.canonical_sources)}"
            )
        except Exception as e:
            self._index_load_failed = True
            logger.warning(f"Could not load cross-source dedup index ({e}); items will pass through unchecked")

        return self._canonical_index

    async def process_item(self, item: Imovel) -> Imovel | None:
        # A canonical source never dedupes against itself.
        if self.source_name in self.canonical_sources:
            return item

        index = await self._get_canonical_index()
        if not index:
            return item

        key = build_dedup_key(item.uf, item.cidade, item.endereco)
        if key:
            canonical_link = index.get(key)
            if canonical_link and canonical_link != item.link_de_acesso:
                self.cross_source_duplicates.append((item.link_de_acesso, canonical_link))
                logger.info(f"Cross-source duplicate dropped: {item.link_de_acesso} matches {canonical_link}")
                return None

        return item

    def open_pipeline(self) -> None:
        self.cross_source_duplicates = []

    def close_pipeline(self) -> None:
        if self.cross_source_duplicates:
            logger.info(
                f"Cross-source deduplication: {len(self.cross_source_duplicates)} listing(s) "
                f"dropped in favor of canonical sources {list(self.canonical_sources)}"
            )

    def get_stats(self) -> dict:
        return {
            "cross_source_duplicates_removed": len(self.cross_source_duplicates),
            "canonical_sources": list(self.canonical_sources),
        }
