"""
Statistics Models

Models for daily statistics and analytics.
"""

from datetime import date

from pydantic import BaseModel, Field


class DailyStats(BaseModel):
    """
    Model for daily_stats table.
    Daily snapshot of property statistics for dashboard.
    """

    # Date (one row per day)
    stat_date: date = Field(default_factory=date.today)

    # Total counts (all sources combined)
    total_active: int = 0
    total_inactive: int = 0

    # Caixa specific
    caixa_active: int = 0
    caixa_inactive: int = 0

    # Scrapers specific (imoveis_scrapers)
    scrapers_active: int = 0
    scrapers_inactive: int = 0

    # Daily changes
    added_today: int = 0
    removed_today: int = 0

    # Breakdown by source (for scrapers)
    added_by_source: dict[str, int] = Field(default_factory=dict)
    removed_by_source: dict[str, int] = Field(default_factory=dict)
    active_by_source: dict[str, int] = Field(default_factory=dict)

    # Caixa breakdown by state
    caixa_by_uf: dict[str, int] = Field(default_factory=dict)

    def to_db_dict(self) -> dict:
        """Convert to dictionary for database insertion."""
        # stat_date stays as a native date: asyncpg binds it to the date
        # column directly and rejects ISO strings.
        return self.dict()
