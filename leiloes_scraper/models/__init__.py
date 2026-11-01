"""
Data Models

All Pydantic models for the ETL system.
"""

from .imovel import Imovel, ImovelScraper
from .scraper import RunStatus, ScraperRun, ScrapingConfig
from .stats import DailyStats

__all__ = [
    # Property models
    "Imovel",
    "ImovelScraper",  # Alias for backward compatibility
    # Scraper models
    "ScraperRun",
    "RunStatus",
    "ScrapingConfig",
    # Stats models
    "DailyStats",
]
