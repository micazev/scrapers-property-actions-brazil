"""
Scraper Runs Database Operations

Functions for scraper_runs table (analytics/metrics).
"""

from datetime import datetime

from ..models import RunStatus, ScraperRun
from .client import PgClient, get_client


async def create_run(run: ScraperRun, client: PgClient | None = None) -> ScraperRun:
    """
    Record the start of a scraper run.

    Args:
        run: ScraperRun object to insert

    Returns:
        The inserted ScraperRun
    """
    client = client or get_client()

    await client.insert(client.TABLE_RUNS, data=[run.to_db_dict()])
    return run


async def update_run(run: ScraperRun, client: PgClient | None = None) -> None:
    """
    Update a scraper run with current metrics.

    Args:
        run: ScraperRun object with updated values
    """
    client = client or get_client()

    await client.update(client.TABLE_RUNS, data=run.to_db_dict(), filters={"run_id": f"eq.{run.run_id}"})


async def complete_run(run: ScraperRun, status: RunStatus = RunStatus.SUCCESS, client: PgClient | None = None) -> None:
    """
    Mark a scraper run as complete.

    Args:
        run: ScraperRun object
        status: Final status (SUCCESS, FAILED, PARTIAL)
    """
    run.complete(status)
    await update_run(run, client)


async def get_last_run(source: str, client: PgClient | None = None) -> ScraperRun | None:
    """
    Get the most recent run for a source.

    Args:
        source: Scraper source name

    Returns:
        ScraperRun or None if no runs found
    """
    client = client or get_client()

    records = await client.select(
        client.TABLE_RUNS, filters={"source": f"eq.{source}"}, order="started_at.desc", limit=1
    )

    if records:
        return ScraperRun(**records[0])
    return None


async def get_runs_by_source(source: str, limit: int = 10, client: PgClient | None = None) -> list[ScraperRun]:
    """
    Get recent runs for a source.

    Args:
        source: Scraper source name
        limit: Max number of runs to return

    Returns:
        List of ScraperRun objects
    """
    client = client or get_client()

    records = await client.select(
        client.TABLE_RUNS, filters={"source": f"eq.{source}"}, order="started_at.desc", limit=limit
    )

    return [ScraperRun(**r) for r in records]


async def get_runs_summary(days: int = 7, client: PgClient | None = None) -> dict:
    """
    Get summary of runs for the last N days.

    Returns:
        Dict with counts by status and source
    """
    client = client or get_client()
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=days)

    records = await client.select(client.TABLE_RUNS, columns="source,status", filters={"started_at": ("gte", cutoff)})

    summary = {"total": len(records), "by_status": {}, "by_source": {}}

    for r in records:
        status = r.get("status", "unknown")
        source = r.get("source", "unknown")

        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        summary["by_source"][source] = summary["by_source"].get(source, 0) + 1

    return summary
