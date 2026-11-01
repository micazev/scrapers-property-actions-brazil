"""
Daily Stats Database Operations

Functions for daily_stats table (dashboard analytics).
"""

from datetime import date

from ..models import DailyStats
from .client import PgClient, get_client
from .imoveis import (
    get_caixa_active_count,
    get_caixa_by_uf,
    get_scrapers_by_source,
)


async def get_daily_stats(stat_date: date | None = None, client: PgClient | None = None) -> DailyStats | None:
    """
    Get daily stats for a specific date.

    Args:
        stat_date: Date to get stats for (default: today)

    Returns:
        DailyStats or None if not found
    """
    client = client or get_client()
    stat_date = stat_date or date.today()

    records = await client.select(client.TABLE_STATS, filters={"stat_date": ("eq", stat_date)}, limit=1)

    if records:
        return DailyStats(**records[0])
    return None


async def upsert_daily_stats(stats: DailyStats, client: PgClient | None = None) -> None:
    """
    Upsert daily stats record.

    Args:
        stats: DailyStats object to upsert
    """
    client = client or get_client()

    try:
        await client.insert(client.TABLE_STATS, data=[stats.to_db_dict()], on_conflict="stat_date")
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Failed to upsert daily stats: {e}")
        logger.error(f"Stats data: {stats.to_db_dict()}")
        # Re-raise to maintain existing error handling behavior
        raise


async def calculate_daily_stats(client: PgClient | None = None) -> DailyStats:
    """
    Calculate and store daily statistics.
    Should be called at the end of the daily ETL run.

    Returns:
        Calculated DailyStats
    """
    client = client or get_client()

    # Get current counts
    caixa_active = await get_caixa_active_count(client)
    # Every source except Caixa, which has its own counter above.
    scrapers_by_source = {
        source: count for source, count in (await get_scrapers_by_source(client)).items() if source != "caixa"
    }
    caixa_by_uf = await get_caixa_by_uf(client)

    scrapers_active = sum(scrapers_by_source.values())

    # Get yesterday's stats to calculate changes
    from datetime import timedelta

    yesterday = date.today() - timedelta(days=1)
    yesterday_stats = await get_daily_stats(yesterday, client)

    added_today = 0
    removed_today = 0

    if yesterday_stats:
        total_today = caixa_active + scrapers_active
        total_yesterday = yesterday_stats.caixa_active + yesterday_stats.scrapers_active

        if total_today > total_yesterday:
            added_today = total_today - total_yesterday
        else:
            removed_today = total_yesterday - total_today

    # Create stats object
    stats = DailyStats(
        stat_date=date.today(),
        total_active=caixa_active + scrapers_active,
        total_inactive=0,  # Would need separate query
        caixa_active=caixa_active,
        caixa_inactive=0,
        scrapers_active=scrapers_active,
        scrapers_inactive=0,
        added_today=added_today,
        removed_today=removed_today,
        active_by_source=scrapers_by_source,
        caixa_by_uf=caixa_by_uf,
    )

    # Save to database
    await upsert_daily_stats(stats, client)

    return stats


async def get_stats_trend(days: int = 30, client: PgClient | None = None) -> list:
    """
    Get daily stats trend for the last N days.

    Args:
        days: Number of days to retrieve

    Returns:
        List of DailyStats ordered by date
    """
    client = client or get_client()
    from datetime import timedelta

    cutoff = date.today() - timedelta(days=days)

    records = await client.select(client.TABLE_STATS, filters={"stat_date": ("gte", cutoff)}, order="stat_date.asc")

    return [DailyStats(**r) for r in records]
