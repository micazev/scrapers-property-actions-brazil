"""
Output backend selection.

Two ways to keep what a run scraped:

- **Postgres** — when ``DATABASE_URL`` is set. Properties are upserted into
  ``imoveis``, runs into ``scraper_runs``, health into ``scraper_health``, and
  listings that dropped off a site are soft-deleted (``status_leilao = false``).
  The schema is in ``sql/schema.sql``.
- **JSON files** — when it is not. Each scraper writes
  ``<OUTPUT_DIR>/<source>.json`` (default ``output/``) with the run summary and
  every property, so the project runs with nothing but a browser.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Imovel, ScraperRun


def using_database() -> bool:
    """True when results go to Postgres, False when they go to JSON files."""
    return bool(os.environ.get("DATABASE_URL"))


def output_dir() -> Path:
    return Path(os.environ.get("OUTPUT_DIR", "output"))


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):  # enums
        return value.value
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def write_json_output(source: str, run: ScraperRun | None, properties: list[Imovel]) -> Path:
    """Write one scraper's results to ``<OUTPUT_DIR>/<source>.json`` and return the path."""
    directory = output_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{source}.json"
    payload = {
        "source": source,
        "run": run.model_dump() if run else None,
        "count": len(properties),
        "properties": [p.to_db_dict() for p in properties],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")
    return path
