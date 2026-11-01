"""
Scraper Runner Module

Utilities for running scrapers individually or in batch.
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ..database import calculate_daily_stats
from ..storage import using_database
from . import get_scraper, is_scraper_enabled, list_enabled_scrapers, list_scrapers

# Configure logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("runner")


# Scrapers in this list run first (in the order given) during full-pipeline
# runs, ahead of the remaining scrapers in alphabetical order. `--list` stays
# alphabetical for predictable lookup.
#
# Policy: priority = the highest-yield *tuned* connectors, so the most valuable
# inventory lands even if the run is cut short. Only enabled scrapers belong
# here — an untuned scraper in this list wastes the best slot of every run
# (frazaoleiloes previously ran first every day with empty selectors).
PRIORITY_SCRAPERS: list[str] = ["portalzuk", "gpleiloes", "francoleiloes", "palaciodosleiloes"]

# Where the runner drops machine-readable alerts (e.g. silent zero-yield
# regressions) for a CI step to pick up. Relative to the working directory.
ALERTS_FILE = Path("scraper_alerts.json")


def _ordered_scrapers(include_disabled: bool = False) -> list[str]:
    """Return scraper names for a full run: priority first, rest alphabetical.

    By default only the curated (enabled) set is returned — template-clone
    scrapers with unverified selectors extract ~nothing and only burn
    Browserless minutes. Pass include_disabled=True to force the whole
    registry (e.g. for a one-off census run).
    """
    all_names = list_scrapers() if include_disabled else list_enabled_scrapers()
    priority = [n for n in PRIORITY_SCRAPERS if n in all_names]
    others = [n for n in all_names if n not in priority]
    return priority + others


async def run_scraper(
    name: str,
    headless: bool = True,
    max_properties: int = 5000,
) -> dict[str, Any]:
    """
    Run a single scraper by name.

    Args:
        name: Scraper name (e.g., 'gpleiloes', 'portalzuk')
        headless: Run browser in headless mode
        max_properties: Maximum properties to scrape

    Returns:
        Dict with execution results
    """
    start_time = datetime.now()
    print(f"[{start_time.strftime('%H:%M:%S')}] Starting {name} scraper...")

    try:
        scraper_class = get_scraper(name)
        import inspect
        sig = inspect.signature(scraper_class)
        if "max_properties" in sig.parameters:
            scraper = scraper_class(headless=headless, max_properties=max_properties)
        else:
            scraper = scraper_class(headless=headless)

        result = await scraper.execute()
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        print(
            f"[{end_time.strftime('%H:%M:%S')}] {name}: Found {result.get('total_found', 0)} properties in {duration:.1f}s"
        )
        return {"name": name, "status": "success", "duration_seconds": duration, **result}

    except Exception as e:
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        print(f"[{end_time.strftime('%H:%M:%S')}] {name}: FAILED - {str(e)}")
        return {
            "name": name,
            "status": "failed",
            "error": str(e),
            "duration_seconds": duration,
        }


async def run_scrapers(
    names: list[str] | None = None,
    headless: bool = True,
    max_properties: int = 5000,
    concurrent: int = 1,
    stagger_seconds: float = 3.0,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    """
    Run multiple scrapers.

    Args:
        names: List of scraper names to run (None = the curated enabled set)
        headless: Run browsers in headless mode
        max_properties: Maximum properties per scraper
        concurrent: Number of scrapers to run concurrently
        stagger_seconds: Delay between starting each scraper (seconds)
        include_disabled: When names is None, also run scrapers that are not
            in the curated enabled set (full-registry census run)

    Returns:
        List of results for each scraper
    """
    if names is None:
        names = _ordered_scrapers(include_disabled=include_disabled)

    if concurrent > 5:
        logger.warning(f"High concurrency ({concurrent}) may trigger rate limits (429). Recommended: <= 3")

    results = []

    if concurrent == 1:
        # Sequential execution
        for name in names:
            result = await run_scraper(name, headless, max_properties)
            results.append(result)
    else:
        # Concurrent execution with semaphore and staggering
        semaphore = asyncio.Semaphore(concurrent)

        async def run_with_semaphore(name: str, delay: float) -> dict[str, Any]:
            # Stagger start to avoid "thundering herd" on Browserless.io
            if delay > 0:
                logger.info(f"Waiting {delay:.1f}s to start {name} (staggering)...")
                await asyncio.sleep(delay)

            async with semaphore:
                return await run_scraper(name, headless, max_properties)

        # Stagger starts by stagger_seconds
        tasks = [run_with_semaphore(name, i * stagger_seconds) for i, name in enumerate(names)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to error dicts
        results = [
            r if isinstance(r, dict) else {"name": "unknown", "status": "error", "error": str(r)} for r in results
        ]

    return results


def print_summary(results: list[dict[str, Any]]) -> tuple[int, int]:
    """Print a summary of scraping results.

    Returns:
        Tuple of (success_count, failed_count)
    """
    print("\n" + "=" * 60)
    print("SCRAPING SUMMARY")
    print("=" * 60)

    total_found = 0
    total_saved = 0
    total_removed = 0
    total_errors = 0
    total_pages = 0
    success_count = 0
    failed_count = 0

    for result in results:
        status = result.get("status", "unknown")
        name = result.get("name", "unknown")

        if status == "success":
            success_count += 1
            found = result.get("total_found", 0)
            saved = result.get("total_saved", 0)
            removed = result.get("total_removed", 0)
            errors = result.get("total_errors", 0)
            pages = result.get("pages_scraped", 0)
            total_found += found
            total_saved += saved
            total_removed += removed
            total_errors += errors
            total_pages += pages
            error_str = f", {errors} errors" if errors > 0 else ""
            removed_str = f", {removed} removed" if removed > 0 else ""
            print(f"  {name}: {found} found, {saved} saved{removed_str}, {pages} pages{error_str}")
        else:
            failed_count += 1
            error = result.get("error", "Unknown error")
            print(f"  {name}: FAILED - {error[:50]}...")

    print("-" * 60)
    print(f"Total: {success_count} succeeded, {failed_count} failed")
    print(f"Properties: {total_found} found, {total_saved} saved, {total_removed} removed")
    print(f"Pages scraped: {total_pages}, Extraction errors: {total_errors}")
    print("=" * 60)

    return success_count, failed_count


def write_alerts_file(results: list[dict[str, Any]], path: Path = ALERTS_FILE) -> list[dict[str, Any]]:
    """Persist silent-regression alerts for the CI workflow to page on.

    A scraper that *fails* already pages via the workflow's failure steps.
    The dangerous case is the silent one: the run "succeeds" with zero items
    from a scraper that historically produced them (broken selectors after a
    site redesign). Health tracking flags those runs; here we collect them
    into a JSON file so a workflow step can post them to a chat without
    failing the job.

    Returns the list of alerts written (empty list = no file written).
    """
    alerts = [
        {
            "scraper": r.get("name") or r.get("source", "unknown"),
            "type": "zero_yield",
            "detail": r.get("zero_yield_detail", "0 items found; scraper historically produced results"),
        }
        for r in results
        if r.get("zero_yield_alert")
    ]

    # Remove a stale file so the workflow only pages on fresh alerts.
    if path.exists():
        path.unlink()

    if alerts:
        path.write_text(json.dumps({"alerts": alerts}, indent=2))
        logger.warning(f"{len(alerts)} silent zero-yield alert(s) written to {path}")

    return alerts


async def main() -> int:
    """CLI entry point.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    parser = argparse.ArgumentParser(
        description="Run auction property scrapers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m leiloes_scraper.scrapers.runner --list
  python -m leiloes_scraper.scrapers.runner --scraper gpleiloes
  python -m leiloes_scraper.scrapers.runner --scraper gpleiloes portalzuk
  python -m leiloes_scraper.scrapers.runner --all --concurrent 3
  python -m leiloes_scraper.scrapers.runner --all --include-disabled   # full-registry census
  python -m leiloes_scraper.scrapers.runner --scraper gpleiloes --max 100 --no-headless
        """,
    )

    parser.add_argument("--list", action="store_true", help="List all available scrapers")
    parser.add_argument("--scraper", nargs="+", help="Scraper(s) to run (works for disabled scrapers too)")
    parser.add_argument("--all", action="store_true", help="Run the curated (enabled) scraper set")
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="With --all: also run scrapers whose selectors are not verified (disabled by default)",
    )
    parser.add_argument(
        "--max", type=int, default=5000, help="Maximum properties to scrape per scraper (default: 5000)"
    )
    parser.add_argument("--concurrent", type=int, default=1, help="Number of scrapers to run concurrently (default: 1)")
    parser.add_argument(
        "--stagger", type=float, default=3.0, help="Delay in seconds between starting each scraper (default: 3.0)"
    )
    parser.add_argument("--no-headless", action="store_true", help="Run browsers with UI (not headless)")

    args = parser.parse_args()

    # Optional .env in the working directory (see .env.example).
    from dotenv import load_dotenv

    load_dotenv()

    if args.list:
        enabled = set(list_enabled_scrapers())
        print(f"Available scrapers ({len(enabled)} enabled / {len(list_scrapers())} registered):")
        for name in list_scrapers():
            status = "enabled" if name in enabled else "disabled (selectors not verified)"
            print(f"  - {name}  [{status}]")
        return 0

    if not args.scraper and not args.all:
        parser.print_help()
        return 0

    names = None if args.all else args.scraper
    headless = not args.no_headless

    if args.scraper:
        for name in args.scraper:
            if not is_scraper_enabled(name):
                logger.info(f"'{name}' is not in the curated enabled set — running it explicitly for tuning")

    print(f"Running scrapers: {names or ('all registered' if args.include_disabled else 'curated enabled set')}")
    print(f"Headless: {headless}, Max properties: {args.max}, Concurrent: {args.concurrent}, Stagger: {args.stagger}s")
    print()

    results = await run_scrapers(
        names=names,
        headless=headless,
        max_properties=args.max,
        concurrent=args.concurrent,
        stagger_seconds=args.stagger,
        include_disabled=args.include_disabled,
    )

    success_count, failed_count = print_summary(results)

    # Surface silent zero-yield regressions (successful runs that extracted
    # nothing from a historically-producing scraper) for the CI pager.
    write_alerts_file(results)

    # Calculate and update daily stats after running all scrapers
    if args.all and using_database():
        try:
            logger.info("Calculating daily statistics...")
            stats = await calculate_daily_stats()
            logger.info(
                f"Daily stats updated: {stats.total_active} active properties "
                f"(Caixa: {stats.caixa_active}, Scrapers: {stats.scrapers_active})"
            )
        except Exception as e:
            logger.error(f"Failed to calculate daily stats: {e}")
            logger.warning("Daily stats failure will not fail the overall job")
            # Don't fail the job if daily stats fails - scrapers succeeded

    # Return failure status if any scrapers failed
    if failed_count > 0:
        if success_count == 0:
            logger.error(f"All {failed_count} scrapers failed")
        else:
            logger.warning(f"{failed_count} of {success_count + failed_count} scrapers failed")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
