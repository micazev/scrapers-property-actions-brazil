"""
Per-source Yield Report

Answers "how much does each source actually add today?" — the empirical
baseline for deciding which connectors to tune, enable, or retire.

Combines:
- imoveis grouped by leiloeiro (active/inactive listing counts)
- scraper_runs over the last N days (run count, last status, found counts)
- the registry's curation flag (enabled vs. disabled)

Usage:
    python -m leiloes_scraper.monitoring.yield_report            # table, last 7 days
    python -m leiloes_scraper.monitoring.yield_report --days 30
    python -m leiloes_scraper.monitoring.yield_report --json     # machine-readable
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from typing import Any

from ..database import get_client


async def collect_yield_data(days: int = 7) -> dict[str, Any]:
    """
    Build the per-source yield dataset.

    Returns:
        Dict with:
        - sources: list of per-source dicts (leiloeiro, enabled, active,
          inactive, runs, last_run_at, last_status, last_found, avg_found)
        - totals: caixa vs. non-caixa active counts and the overall split
        - days: the scraper_runs window used
    """
    client = get_client()

    # Listing counts per leiloeiro, split by active/inactive. Aggregated in
    # the database — fetching every row just to count does not scale with
    # the inventory. NULL/empty leiloeiro collapses to "unknown", and only
    # status_leilao = TRUE counts as active (FALSE and NULL are inactive).
    listing_rows = await client.fetch(
        f"""
        SELECT COALESCE(NULLIF(leiloeiro, ''), 'unknown') AS source,
               COUNT(*) FILTER (WHERE status_leilao) AS active,
               COUNT(*) FILTER (WHERE status_leilao IS NOT TRUE) AS inactive
        FROM {client.TABLE_IMOVEIS}
        GROUP BY 1
        """
    )
    active: dict[str, int] = {row["source"]: row["active"] for row in listing_rows}
    inactive: dict[str, int] = {row["source"]: row["inactive"] for row in listing_rows}

    # Run history over the window: per-source totals plus the most recent
    # run, in one aggregate query instead of every run row in the window.
    cutoff = datetime.now() - timedelta(days=days)
    run_rows = await client.fetch(
        f"""
        SELECT DISTINCT ON (source)
               source,
               COUNT(*) OVER w AS runs,
               SUM(total_found) OVER w AS found_sum,
               started_at AS last_run_at,
               status AS last_status,
               total_found AS last_found
        FROM (
            SELECT COALESCE(NULLIF(source, ''), 'unknown') AS source,
                   status,
                   COALESCE(total_found, 0) AS total_found,
                   started_at
            FROM {client.TABLE_RUNS}
            WHERE started_at >= $1
        ) windowed_runs
        WINDOW w AS (PARTITION BY source)
        ORDER BY source, started_at DESC
        """,
        cutoff,
    )
    run_stats: dict[str, dict[str, Any]] = {
        row["source"]: {
            "runs": row["runs"],
            "found_sum": int(row["found_sum"]),
            "last_run_at": row["last_run_at"],
            "last_status": row["last_status"],
            "last_found": row["last_found"],
        }
        for row in run_rows
    }

    # Curation flags from the registry (import here: pulls in playwright).
    from ..scrapers import is_scraper_enabled, list_scrapers

    registered = set(list_scrapers())

    all_sources = sorted(set(active) | set(inactive) | set(run_stats) | registered)
    sources = []
    for source in all_sources:
        stats = run_stats.get(source, {})
        run_count = stats.get("runs", 0)
        sources.append(
            {
                "leiloeiro": source,
                "registered": source in registered,
                "enabled": is_scraper_enabled(source),
                "active": active.get(source, 0),
                "inactive": inactive.get(source, 0),
                "runs": run_count,
                "last_run_at": stats.get("last_run_at"),
                "last_status": stats.get("last_status"),
                "last_found": stats.get("last_found"),
                "avg_found": round(stats["found_sum"] / run_count, 1) if run_count else None,
            }
        )

    caixa_active = active.get("caixa", 0)
    non_caixa_active = sum(count for source, count in active.items() if source != "caixa")
    totals = {
        "caixa_active": caixa_active,
        "non_caixa_active": non_caixa_active,
        "total_active": caixa_active + non_caixa_active,
        "non_caixa_share_pct": (
            round(100 * non_caixa_active / (caixa_active + non_caixa_active), 1)
            if (caixa_active + non_caixa_active)
            else 0.0
        ),
    }

    return {"sources": sources, "totals": totals, "days": days}


def format_report(data: dict[str, Any]) -> str:
    """Render the yield dataset as a fixed-width table."""
    lines = []
    lines.append("=" * 108)
    lines.append(f"PER-SOURCE YIELD REPORT (scraper_runs window: last {data['days']} days)")
    lines.append("=" * 108)

    header = (
        f"{'source':<28} {'flag':<10} {'active':>7} {'inactive':>8} "
        f"{'runs':>5} {'last run':<17} {'last status':<12} {'last found':>10} {'avg found':>9}"
    )
    lines.append(header)
    lines.append("-" * 108)

    # Sort: most active listings first — that is the yield ranking.
    for source in sorted(data["sources"], key=lambda s: (-s["active"], s["leiloeiro"])):
        if not source["registered"]:
            flag = "external"  # rows in imoveis with no registered scraper
        elif source["enabled"]:
            flag = "enabled"
        else:
            flag = "disabled"

        last_run_at = source["last_run_at"]
        if isinstance(last_run_at, datetime):
            last_run_str = last_run_at.strftime("%Y-%m-%d %H:%M")
        else:
            last_run_str = str(last_run_at or "-")

        lines.append(
            f"{source['leiloeiro']:<28} {flag:<10} {source['active']:>7} {source['inactive']:>8} "
            f"{source['runs']:>5} {last_run_str:<17} {str(source['last_status'] or '-'):<12} "
            f"{str(source['last_found'] if source['last_found'] is not None else '-'):>10} "
            f"{str(source['avg_found'] if source['avg_found'] is not None else '-'):>9}"
        )

    totals = data["totals"]
    lines.append("-" * 108)
    lines.append(
        f"Active listings: {totals['total_active']} total — "
        f"Caixa: {totals['caixa_active']}, non-Caixa: {totals['non_caixa_active']} "
        f"({totals['non_caixa_share_pct']}% of inventory comes from sources beyond the Caixa CSV)"
    )
    lines.append("=" * 108)
    return "\n".join(lines)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Per-source yield report for the scraper fleet")
    parser.add_argument("--days", type=int, default=7, help="scraper_runs lookback window in days (default: 7)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a table")
    args = parser.parse_args()

    data = await collect_yield_data(days=args.days)

    if args.json:
        print(json.dumps(data, indent=2, default=_json_default))
    else:
        print(format_report(data))

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
