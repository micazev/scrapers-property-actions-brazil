"""
Scraper Health Database Operations

Durable storage for scraper health metrics (scraper_health table).

Health state used to live in a local scraper_health.json file, but GitHub
Actions containers are ephemeral: consecutive_failures and avg_items_found
reset on every run, so "3 consecutive failures" and "zero results vs.
historical average" detection never actually fired in CI. Persisting the
state per scraper in Postgres makes those checks work across runs.
"""

from datetime import datetime
from typing import Any

from .client import PgClient, get_client

# Columns persisted for each scraper's health row. Mirrors the mutable fields
# of monitoring.health.ScraperHealthMetrics (computed properties like
# success_rate are derived, not stored).
HEALTH_COLUMNS = (
    "scraper_name",
    "last_run",
    "last_success",
    "last_failure",
    "last_error_message",
    "total_runs",
    "successful_runs",
    "failed_runs",
    "partial_runs",
    "consecutive_failures",
    "items_found_last_run",
    "items_saved_last_run",
    "avg_items_found",
    "selector_issues_detected",
    "last_selector_issue",
)


async def load_scraper_health(scraper_name: str, client: PgClient | None = None) -> dict[str, Any] | None:
    """
    Load the persisted health row for one scraper.

    Returns:
        Dict of column values, or None if the scraper has no row yet.
    """
    client = client or get_client()
    records = await client.select(
        client.TABLE_HEALTH,
        columns=", ".join(HEALTH_COLUMNS),
        filters={"scraper_name": ("eq", scraper_name)},
        limit=1,
    )
    return records[0] if records else None


async def load_all_scraper_health(client: PgClient | None = None) -> list[dict[str, Any]]:
    """Load all persisted health rows (for reports/summaries)."""
    client = client or get_client()
    return await client.select(client.TABLE_HEALTH, columns=", ".join(HEALTH_COLUMNS), order="scraper_name.asc")


async def upsert_scraper_health(data: dict[str, Any], client: PgClient | None = None) -> None:
    """
    Upsert one scraper's health row (conflict key: scraper_name).

    Args:
        data: column -> value dict; datetimes must be native datetime objects
            (asyncpg rejects ISO strings for timestamp columns).
    """
    client = client or get_client()
    row = {k: v for k, v in data.items() if k in HEALTH_COLUMNS}
    row["updated_at"] = datetime.now()
    await client.insert(client.TABLE_HEALTH, data=[row], on_conflict="scraper_name")
