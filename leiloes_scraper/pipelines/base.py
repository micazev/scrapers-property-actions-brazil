"""
Base Pipeline Classes

Scrapy-inspired pipeline architecture for processing scraped items.
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..models import Imovel

logger = logging.getLogger("pipelines")


class ItemPipeline(ABC):
    """
    Abstract base class for item processing pipelines.

    Pipelines process items sequentially, allowing for validation,
    enrichment, deduplication, and other transformations.

    Each pipeline receives an item and returns either:
    - The processed item (possibly modified)
    - None to drop the item from the pipeline
    """

    @abstractmethod
    async def process_item(self, item: "Imovel") -> Optional["Imovel"]:
        """
        Process a single item.

        Args:
            item: The Imovel item to process

        Returns:
            The processed item, or None to drop it
        """
        pass

    # open/close are optional lifecycle hooks; subclasses override only if needed.
    def open_pipeline(self) -> None:  # noqa: B027
        """Called when the pipeline is opened (start of scraping)."""
        pass

    def close_pipeline(self) -> None:  # noqa: B027
        """Called when the pipeline is closed (end of scraping)."""
        pass


class PipelineRunner:
    """
    Runs items through a chain of pipelines.

    The runner processes each item through all configured pipelines
    in order. If any pipeline returns None, the item is dropped.
    """

    def __init__(self, pipelines: list[ItemPipeline] | None = None):
        self.pipelines = pipelines or []
        self.items_processed = 0
        self.items_dropped = 0

    def add_pipeline(self, pipeline: ItemPipeline) -> None:
        """Add a pipeline to the chain."""
        self.pipelines.append(pipeline)

    async def process_item(self, item: "Imovel") -> Optional["Imovel"]:
        """
        Process an item through all pipelines.

        Args:
            item: The item to process

        Returns:
            The processed item, or None if dropped
        """
        current_item = item

        for pipeline in self.pipelines:
            try:
                current_item = await pipeline.process_item(current_item)
                if current_item is None:
                    self.items_dropped += 1
                    logger.debug(f"Item dropped by {pipeline.__class__.__name__}: {item.link_de_acesso}")
                    return None
            except Exception as e:
                logger.error(f"Error in {pipeline.__class__.__name__}: {e}, item: {item.link_de_acesso}")
                # Continue processing despite errors
                continue

        self.items_processed += 1
        return current_item

    async def process_items(self, items: list["Imovel"]) -> list["Imovel"]:
        """
        Process a list of items through all pipelines.

        Args:
            items: List of items to process

        Returns:
            List of processed items (items that weren't dropped)
        """
        processed = []

        for item in items:
            result = await self.process_item(item)
            if result is not None:
                processed.append(result)

        return processed

    def open_all(self) -> None:
        """Open all pipelines."""
        for pipeline in self.pipelines:
            pipeline.open_pipeline()

    def close_all(self) -> None:
        """Close all pipelines."""
        for pipeline in self.pipelines:
            pipeline.close_pipeline()

    def get_stats(self) -> dict:
        """Get processing statistics."""
        return {
            "items_processed": self.items_processed,
            "items_dropped": self.items_dropped,
            "pipelines": [p.__class__.__name__ for p in self.pipelines],
        }
