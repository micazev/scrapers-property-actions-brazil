"""
Scraper Registry

Decorator-based auto-registration system for scrapers.
This eliminates the need for manual SCRAPERS dictionary maintenance.

Usage:
    from leiloes_scraper.scrapers.base.registry import register_scraper

    @register_scraper("myscraper")
    class MyScraperScraper(GenericPaginationScraper):
        ...

The decorator automatically registers the scraper class when imported.

Fleet curation: scrapers register as disabled by default. Only connectors whose
selectors have been verified against the live site (and ideally have an HTML
fixture test) should pass ``enabled=True``. The daily ``runner --all`` run only
executes enabled scrapers; ``runner --scraper <name>`` can still force any
registered scraper for tuning.
"""

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scraper import BaseScraper

logger = logging.getLogger("scrapers.registry")

# Global registry of scraper classes
_SCRAPER_REGISTRY: dict[str, type["BaseScraper"]] = {}

# Curation flags: name -> whether the scraper runs in full-fleet (`--all`) runs.
_SCRAPER_ENABLED: dict[str, bool] = {}


def register_scraper(name: str, enabled: bool = False):
    """
    Decorator to register a scraper class with a given name.

    Args:
        name: The unique identifier for this scraper (e.g., "gpleiloes", "portalzuk")
        enabled: Whether this scraper is part of the curated set run by
            ``runner --all``. Default False: a new scraper must have its
            selectors verified against the live site before it is enabled,
            otherwise it burns browser minutes and pollutes failure signal
            for ~zero yield. Disabled scrapers can still be run explicitly
            via ``runner --scraper <name>`` for tuning.

    Returns:
        Decorator function that registers the class

    Example:
        @register_scraper("francoleiloes", enabled=True)
        class FrancoLeiloesScraper(GenericPaginationScraper):
            ...
    """

    def decorator(cls: type["BaseScraper"]) -> type["BaseScraper"]:
        if name in _SCRAPER_REGISTRY:
            logger.warning(
                f"Scraper '{name}' is being re-registered. "
                f"Previous: {_SCRAPER_REGISTRY[name].__name__}, New: {cls.__name__}"
            )
        _SCRAPER_REGISTRY[name] = cls
        _SCRAPER_ENABLED[name] = enabled
        logger.debug(f"Registered scraper: {name} -> {cls.__name__} (enabled={enabled})")
        return cls

    return decorator


def get_scraper(name: str) -> type["BaseScraper"]:
    """
    Get a scraper class by name.

    Args:
        name: The scraper identifier

    Returns:
        The scraper class

    Raises:
        ValueError: If scraper is not found
    """
    if name not in _SCRAPER_REGISTRY:
        available = list_scrapers()
        raise ValueError(f"Unknown scraper: '{name}'. Available scrapers: {available}")
    return _SCRAPER_REGISTRY[name]


def list_scrapers() -> list[str]:
    """
    List all registered scraper names.

    Returns:
        Sorted list of scraper names
    """
    return sorted(_SCRAPER_REGISTRY.keys())


def is_scraper_enabled(name: str) -> bool:
    """Whether a scraper is part of the curated set run by ``--all``."""
    return _SCRAPER_ENABLED.get(name, False)


def list_enabled_scrapers() -> list[str]:
    """
    List scraper names in the curated (enabled) set.

    Returns:
        Sorted list of enabled scraper names
    """
    return sorted(name for name in _SCRAPER_REGISTRY if _SCRAPER_ENABLED.get(name, False))


def get_all_scrapers() -> dict[str, type["BaseScraper"]]:
    """
    Get the complete registry of all scrapers.

    Returns:
        Dictionary mapping scraper names to classes
    """
    return dict(_SCRAPER_REGISTRY)


def discover_scrapers() -> None:
    """
    Auto-discover and import all scraper modules.

    This function walks through the scrapers package and its subdirectories
    (leiloeiros/, custom/) to import all Python modules, which triggers
    the @register_scraper decorators to populate the registry.

    Call this function once at module load time to ensure all scrapers
    are registered before use.
    """
    # Get the scrapers package directory
    scrapers_dir = Path(__file__).parent.parent  # .../scrapers/

    # Derive the scrapers package name dynamically from this module so discovery
    # works regardless of the top-level package name (this module is
    # "<top>.scrapers.base.registry"; drop the trailing ".base" to get
    # "<top>.scrapers"). Hardcoding broke when the package moved out of "src".
    scrapers_package = __package__.rsplit(".", 1)[0] if __package__ else "leiloes_scraper.scrapers"

    # Directories to scan for scrapers
    scraper_dirs = [
        scrapers_dir,  # Root scrapers directory (for any remaining scrapers)
        scrapers_dir / "leiloeiros",  # Simple config-based scrapers
        scrapers_dir / "custom",  # Complex custom scrapers
    ]

    # Modules to skip (not scrapers)
    skip_modules = {
        "__init__",
        "utils",
        "runner",
        "config_loader",
    }

    # Packages to skip (not scraper files)
    skip_packages = {
        "base",
        "leiloeiros",
        "custom",
    }

    for scraper_dir in scraper_dirs:
        if not scraper_dir.exists():
            continue

        # Determine the package path for imports
        if scraper_dir == scrapers_dir:
            package_prefix = scrapers_package
        else:
            rel_path = scraper_dir.relative_to(scrapers_dir)
            package_prefix = f"{scrapers_package}.{rel_path.as_posix().replace('/', '.')}"

        # Iterate through modules in this directory
        for module_info in pkgutil.iter_modules([str(scraper_dir)]):
            module_name = module_info.name

            # Skip non-scraper modules and packages
            if module_name in skip_modules:
                continue
            if module_info.ispkg and module_name in skip_packages:
                continue
            if module_info.ispkg:
                # Don't import packages here, they're handled separately
                continue

            # Import the module to trigger decorator registration
            full_module_name = f"{package_prefix}.{module_name}"
            try:
                importlib.import_module(full_module_name)
                logger.debug(f"Discovered scraper module: {full_module_name}")
            except Exception as e:
                logger.error(f"Failed to import scraper module {full_module_name}: {e}")


def clear_registry() -> None:
    """
    Clear the scraper registry.

    Primarily useful for testing.
    """
    _SCRAPER_REGISTRY.clear()
    _SCRAPER_ENABLED.clear()
