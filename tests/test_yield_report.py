"""Tests for the per-source yield report assembly.

The listing and run aggregations happen in SQL (GROUP BY / DISTINCT ON);
these tests stub the client with pre-aggregated rows and cover how
collect_yield_data merges them with the scraper registry.
"""

from datetime import datetime, timedelta

from leiloes_scraper.monitoring import yield_report


class FakeClient:
    """Stands in for PgClient.fetch(); dispatches on the queried table."""

    TABLE_IMOVEIS = "imoveis"
    TABLE_RUNS = "scraper_runs"

    def __init__(self, listing_rows, run_rows):
        self._listing_rows = listing_rows
        self._run_rows = run_rows
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, query, *params):
        self.queries.append((query, params))
        if f"FROM {self.TABLE_IMOVEIS}" in query:
            return self._listing_rows
        return self._run_rows


def _patch_registry(monkeypatch, registered: dict[str, bool]) -> None:
    """registered maps scraper name -> enabled flag."""
    monkeypatch.setattr("leiloes_scraper.scrapers.list_scrapers", lambda: list(registered))
    monkeypatch.setattr("leiloes_scraper.scrapers.is_scraper_enabled", lambda name: registered.get(name, False))


async def test_merges_listings_runs_and_registry(monkeypatch):
    last_run_at = datetime(2026, 7, 1, 12, 30)
    client = FakeClient(
        listing_rows=[
            {"source": "caixa", "active": 3, "inactive": 2},
            {"source": "freitas", "active": 2, "inactive": 0},
            {"source": "portalzuk", "active": 0, "inactive": 4},
            {"source": "unknown", "active": 2, "inactive": 2},
        ],
        run_rows=[
            {
                "source": "freitas",
                "runs": 2,
                "found_sum": 10,
                "last_run_at": last_run_at,
                "last_status": "failed",
                "last_found": 0,
            },
            # Runs but no listings and no registration: still reported.
            {
                "source": "megaleiloes",
                "runs": 3,
                "found_sum": 8,
                "last_run_at": last_run_at,
                "last_status": "success",
                "last_found": 5,
            },
        ],
    )
    monkeypatch.setattr(yield_report, "get_client", lambda: client)
    # vipleiloes is registered but has no listings and no runs: still reported.
    _patch_registry(monkeypatch, {"freitas": True, "portalzuk": False, "vipleiloes": True})

    data = await yield_report.collect_yield_data(days=7)

    # Pin the row count too: the by_source dict below would hide duplicates.
    assert len(data["sources"]) == 6
    by_source = {s["leiloeiro"]: s for s in data["sources"]}
    assert sorted(by_source) == [
        "caixa",
        "freitas",
        "megaleiloes",
        "portalzuk",
        "unknown",
        "vipleiloes",
    ]

    freitas = by_source["freitas"]
    assert freitas["registered"] and freitas["enabled"]
    assert freitas["active"] == 2 and freitas["inactive"] == 0
    assert freitas["runs"] == 2
    assert freitas["last_run_at"] == last_run_at
    assert freitas["last_status"] == "failed"
    assert freitas["last_found"] == 0
    assert freitas["avg_found"] == 5.0  # 10 found over 2 runs

    assert not by_source["portalzuk"]["enabled"]
    assert not by_source["megaleiloes"]["registered"]
    assert by_source["vipleiloes"]["runs"] == 0
    assert by_source["vipleiloes"]["avg_found"] is None
    assert by_source["megaleiloes"]["avg_found"] == round(8 / 3, 1)

    assert data["totals"] == {
        "caixa_active": 3,
        "non_caixa_active": 4,  # freitas 2 + unknown 2
        "total_active": 7,
        "non_caixa_share_pct": 57.1,
    }
    assert data["days"] == 7


async def test_runs_window_parameter_is_a_datetime_cutoff(monkeypatch):
    client = FakeClient(listing_rows=[], run_rows=[])
    monkeypatch.setattr(yield_report, "get_client", lambda: client)
    _patch_registry(monkeypatch, {})

    await yield_report.collect_yield_data(days=30)

    runs_queries = [(query, params) for query, params in client.queries if f"FROM {client.TABLE_RUNS}" in query]
    assert len(runs_queries) == 1
    (_, params) = runs_queries[0]
    assert len(params) == 1 and isinstance(params[0], datetime)
    # The cutoff reflects the requested window (allow slack for test runtime).
    assert abs((datetime.now() - timedelta(days=30)) - params[0]) < timedelta(minutes=5)


async def test_empty_database_reports_registry_only(monkeypatch):
    client = FakeClient(listing_rows=[], run_rows=[])
    monkeypatch.setattr(yield_report, "get_client", lambda: client)
    _patch_registry(monkeypatch, {"freitas": True})

    data = await yield_report.collect_yield_data(days=7)

    assert [s["leiloeiro"] for s in data["sources"]] == ["freitas"]
    assert data["totals"] == {
        "caixa_active": 0,
        "non_caixa_active": 0,
        "total_active": 0,
        "non_caixa_share_pct": 0.0,
    }
