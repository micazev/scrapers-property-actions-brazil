"""
Database Module

All PostgreSQL database operations.
"""

from .client import PgClient, get_client
from .health import (
    load_all_scraper_health,
    load_scraper_health,
    upsert_scraper_health,
)
from .imoveis import (
    delete_by_leiloeiro,
    delete_old_by_leiloeiro,
    delete_old_inactive_caixa,
    delete_old_inactive_scrapers,
    # Core imoveis operations
    get_active_count,
    get_by_leiloeiro,
    get_by_uf,
    # Caixa operations (aliases)
    get_caixa_active_count,
    get_caixa_by_uf,
    get_cross_source_rows,
    get_links_by_leiloeiro,
    # Scrapers operations (aliases)
    get_scrapers_active_count,
    get_scrapers_by_source,
    mark_active_by_leiloeiro,
    mark_all_caixa_inactive,
    # Soft delete operations (preferred over hard delete)
    mark_missing_as_inactive,
    mark_source_inactive,
    upsert_caixa,
    upsert_imoveis,
    upsert_scrapers,
)
from .scraper_runs import (
    complete_run,
    create_run,
    get_last_run,
    get_runs_by_source,
    get_runs_summary,
    update_run,
)
from .stats import (
    calculate_daily_stats,
    get_daily_stats,
    upsert_daily_stats,
)

__all__ = [
    # Client
    "PgClient",
    "get_client",
    # Core imoveis operations
    "get_active_count",
    "get_by_uf",
    "get_by_leiloeiro",
    "delete_by_leiloeiro",
    "delete_old_by_leiloeiro",
    "upsert_imoveis",
    "get_links_by_leiloeiro",
    "get_cross_source_rows",
    # Soft delete operations (preferred over hard delete)
    "mark_missing_as_inactive",
    "mark_active_by_leiloeiro",
    # Caixa (aliases)
    "get_caixa_active_count",
    "get_caixa_by_uf",
    "mark_all_caixa_inactive",
    "delete_old_inactive_caixa",
    "upsert_caixa",
    # Scrapers (aliases)
    "get_scrapers_active_count",
    "get_scrapers_by_source",
    "mark_source_inactive",
    "delete_old_inactive_scrapers",
    "upsert_scrapers",
    # Runs
    "create_run",
    "update_run",
    "complete_run",
    "get_last_run",
    "get_runs_by_source",
    "get_runs_summary",
    # Stats
    "get_daily_stats",
    "upsert_daily_stats",
    "calculate_daily_stats",
    # Health
    "load_scraper_health",
    "load_all_scraper_health",
    "upsert_scraper_health",
]
