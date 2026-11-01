"""DATABASE_URL mode against a real Postgres.

Skipped unless TEST_DATABASE_URL points at a throwaway database: the test
applies sql/schema.sql and TRUNCATEs every table it touches.

    export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
    python -m pytest tests/test_database_mode.py
"""

import os
from pathlib import Path

import asyncpg
import pytest
from test_json_output import FakeScraper, make_imovel

from leiloes_scraper.database import calculate_daily_stats, get_client

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.chdir(tmp_path)
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    await conn.execute(SCHEMA.read_text(encoding="utf-8"))
    await conn.execute("TRUNCATE imoveis, scraper_runs, scraper_health, daily_stats")
    yield conn
    await conn.close()
    await get_client().close()


async def test_upsert_soft_delete_and_stats(db):
    await FakeScraper([make_imovel(1), make_imovel(2)]).execute()
    second = await FakeScraper([make_imovel(1)]).execute()

    # Listing 2 dropped off the site: kept, but marked inactive.
    assert second["total_removed"] == 1
    rows = {r["link_de_acesso"]: r["status_leilao"] for r in await db.fetch("SELECT * FROM imoveis")}
    assert rows == {"https://leiloeiro.test/lote/1": True, "https://leiloeiro.test/lote/2": False}

    runs = await db.fetch("SELECT status, total_found FROM scraper_runs ORDER BY started_at")
    assert [(r["status"], r["total_found"]) for r in runs] == [("success", 2), ("success", 1)]

    health = await db.fetchrow(
        "SELECT total_runs, successful_runs FROM scraper_health WHERE scraper_name = 'fakeleiloes'"
    )
    assert (health["total_runs"], health["successful_runs"]) == (2, 2)

    # Daily stats count active listings only.
    stats = await calculate_daily_stats()
    assert stats.scrapers_active == 1
    assert stats.active_by_source == {"fakeleiloes": 1}
