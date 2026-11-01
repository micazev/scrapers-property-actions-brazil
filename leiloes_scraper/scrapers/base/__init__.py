"""
Base Scraper Module

Abstract base classes for all scrapers and registry functions.
"""

from .registry import (
    clear_registry,
    discover_scrapers,
    get_all_scrapers,
    get_scraper,
    list_scrapers,
    register_scraper,
)
from .scraper import BaseScraper, GenericPaginationScraper, PlaywrightScraper

__all__ = [
    # Base classes
    "BaseScraper",
    "PlaywrightScraper",
    "GenericPaginationScraper",
    # Registry functions
    "register_scraper",
    "get_scraper",
    "list_scrapers",
    "get_all_scrapers",
    "discover_scrapers",
    "clear_registry",
]
