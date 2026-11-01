"""
Scrapers Module

Web scrapers for auction property data.

Uses decorator-based auto-registration for scalable scraper management.
New scrapers only need the @register_scraper decorator - no manual imports needed.
"""

# Base classes
# Registry functions
from .base.registry import (
    discover_scrapers,
    get_all_scrapers,
    get_scraper,
    is_scraper_enabled,
    list_enabled_scrapers,
    list_scrapers,
    register_scraper,
)
from .base.scraper import BaseScraper, GenericPaginationScraper, PlaywrightScraper

# Utility functions
from .utils import (
    clean_text,
    extract_property_type,
    extract_state_from_text,
    parse_currency,
    parse_location,
)

# Auto-discover all scrapers at module load time
# This triggers the @register_scraper decorators
discover_scrapers()

# Export the registry as SCRAPERS for backward compatibility
SCRAPERS = get_all_scrapers()

__all__ = [
    # Base classes
    "BaseScraper",
    "PlaywrightScraper",
    "GenericPaginationScraper",
    # Registry functions
    "register_scraper",
    "get_scraper",
    "list_scrapers",
    "list_enabled_scrapers",
    "is_scraper_enabled",
    "get_all_scrapers",
    "discover_scrapers",
    "SCRAPERS",
    # Utilities
    "parse_currency",
    "parse_location",
    "clean_text",
    "extract_property_type",
    "extract_state_from_text",
]
