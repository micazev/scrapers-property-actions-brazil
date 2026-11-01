"""
Processing Pipelines

Scrapy-inspired pipeline architecture for processing scraped items.

Pipelines allow for:
- Validation of scraped data
- Deduplication of items
- Enrichment with additional data
- Any other transformations

Usage:
    from src.pipelines import PipelineRunner, ValidationPipeline, DeduplicationPipeline

    runner = PipelineRunner([
        ValidationPipeline(),
        DeduplicationPipeline(),
    ])

    processed_items = await runner.process_items(raw_items)
"""

from .base import ItemPipeline, PipelineRunner
from .deduplication import (
    CANONICAL_SOURCES,
    CrossSourceDeduplicationPipeline,
    DeduplicationPipeline,
    build_dedup_key,
)
from .enrichment import (
    DefaultValuesPipeline,
    GeocodeEnrichmentPipeline,
    PropertyTypeEnrichmentPipeline,
    StatusLeilaoPipeline,
)
from .validation import ValidationError, ValidationPipeline

__all__ = [
    # Base classes
    "ItemPipeline",
    "PipelineRunner",
    # Validation
    "ValidationPipeline",
    "ValidationError",
    # Deduplication
    "DeduplicationPipeline",
    "CrossSourceDeduplicationPipeline",
    "build_dedup_key",
    "CANONICAL_SOURCES",
    # Enrichment
    "GeocodeEnrichmentPipeline",
    "PropertyTypeEnrichmentPipeline",
    "StatusLeilaoPipeline",
    "DefaultValuesPipeline",
]


def create_default_pipeline(source_name: str | None = None) -> PipelineRunner:
    """
    Create a pipeline runner with default pipelines.

    Default order:
    1. ValidationPipeline - Drop invalid items
    2. DeduplicationPipeline - Remove within-run duplicates (same link)
    3. CrossSourceDeduplicationPipeline - Drop listings that duplicate a
       canonical source's active listing (only when source_name is provided
       and is not itself a canonical source)
    4. StatusLeilaoPipeline - Set status_leilao=True for active auctions
    5. DefaultValuesPipeline - Fill missing values

    Args:
        source_name: leiloeiro slug of the scraper this pipeline serves;
            enables cross-source deduplication against canonical sources

    Returns:
        Configured PipelineRunner
    """
    pipelines: list[ItemPipeline] = [
        ValidationPipeline(strict=False),
        DeduplicationPipeline(),
    ]

    if source_name and source_name not in CANONICAL_SOURCES:
        pipelines.append(CrossSourceDeduplicationPipeline(source_name=source_name))

    pipelines.extend(
        [
            StatusLeilaoPipeline(default_status=True),
            DefaultValuesPipeline(),
        ]
    )

    return PipelineRunner(pipelines)
