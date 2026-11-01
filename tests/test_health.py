"""Tests for durable scraper health monitoring."""

from leiloes_scraper.monitoring.health import HealthMonitor, ScraperHealthMetrics


class TestScraperHealthMetrics:
    def test_consecutive_failures_accumulate(self):
        m = ScraperHealthMetrics(scraper_name="x")
        for _ in range(3):
            m.record_run(success=False, error_message="boom")
        assert m.consecutive_failures == 3
        assert m.health_status == "UNHEALTHY"
        assert not m.is_healthy

    def test_success_resets_consecutive_failures(self):
        m = ScraperHealthMetrics(scraper_name="x")
        m.record_run(success=False)
        m.record_run(success=True, items_found=50)
        assert m.consecutive_failures == 0

    def test_running_average(self):
        m = ScraperHealthMetrics(scraper_name="x")
        m.record_run(success=True, items_found=100)
        m.record_run(success=True, items_found=50)
        assert m.avg_items_found == 75.0

    def test_db_round_trip_preserves_state(self):
        m = ScraperHealthMetrics(scraper_name="x")
        m.record_run(success=True, items_found=42)
        m.record_run(success=False, error_message="selector timeout")

        restored = ScraperHealthMetrics.from_db_row(m.to_db_dict())

        assert restored.scraper_name == "x"
        assert restored.total_runs == 2
        assert restored.consecutive_failures == 1
        assert restored.avg_items_found == m.avg_items_found
        assert restored.last_error_message == "selector timeout"

    def test_from_db_row_ignores_extra_columns(self):
        row = ScraperHealthMetrics(scraper_name="x").to_db_dict()
        row["updated_at"] = "2026-01-01"  # DB-only column
        restored = ScraperHealthMetrics.from_db_row(row)
        assert restored.scraper_name == "x"


class FakeHealthStore:
    """In-memory stand-in for the scraper_health table."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def load(self, scraper_name):
        return self.rows.get(scraper_name)

    async def upsert(self, data):
        self.rows[data["scraper_name"]] = data


class TestHealthMonitorDurable:
    def _patched_monitor(self, monkeypatch, store: FakeHealthStore) -> HealthMonitor:
        import leiloes_scraper.database as database

        monkeypatch.setattr(database, "load_scraper_health", store.load)
        monkeypatch.setattr(database, "upsert_scraper_health", store.upsert)
        return HealthMonitor(use_database=True)

    async def test_state_survives_across_monitor_instances(self, monkeypatch):
        """Each CI run gets a fresh container (fresh HealthMonitor); the
        failure streak must accumulate in the shared store anyway."""
        store = FakeHealthStore()

        for expected_failures in (1, 2, 3):
            monitor = self._patched_monitor(monkeypatch, store)  # "new container"
            metrics, _ = await monitor.record_run_result("brokenscraper", success=False, error_message="x")
            assert metrics.consecutive_failures == expected_failures

        assert store.rows["brokenscraper"]["consecutive_failures"] == 3

    async def test_zero_yield_alert_fires_from_durable_history(self, monkeypatch):
        store = FakeHealthStore()

        # Historical runs (previous containers) produced plenty of items.
        monitor = self._patched_monitor(monkeypatch, store)
        for _ in range(5):
            _, alert = await monitor.record_run_result("goodscraper", success=True, items_found=100)
            assert alert is False

        # New container: run "succeeds" with 0 items -> silent selector break.
        fresh_monitor = self._patched_monitor(monkeypatch, store)
        metrics, alert = await fresh_monitor.record_run_result("goodscraper", success=True, items_found=0)

        assert alert is True
        assert metrics.selector_issues_detected == 1
        assert "Zero results" in metrics.last_selector_issue

    async def test_zero_yield_not_flagged_for_new_scraper(self, monkeypatch):
        store = FakeHealthStore()
        monitor = self._patched_monitor(monkeypatch, store)

        _, alert = await monitor.record_run_result("newscraper", success=True, items_found=0)
        assert alert is False

    async def test_db_write_failure_does_not_raise(self, monkeypatch):
        store = FakeHealthStore()

        async def failing_upsert(data):
            raise RuntimeError("db down")

        import leiloes_scraper.database as database

        monkeypatch.setattr(database, "load_scraper_health", store.load)
        monkeypatch.setattr(database, "upsert_scraper_health", failing_upsert)

        monitor = HealthMonitor(use_database=True)
        metrics, _ = await monitor.record_run_result("x", success=True, items_found=5)
        assert metrics.total_runs == 1  # recorded in memory despite persist failure


class TestHealthMonitorJsonFallback:
    async def test_json_fallback_round_trip(self, tmp_path):
        path = tmp_path / "scraper_health.json"

        monitor = HealthMonitor(storage_path=path, use_database=False)
        await monitor.record_run_result("localscraper", success=True, items_found=7)

        reloaded = HealthMonitor(storage_path=path, use_database=False)
        assert reloaded.get_metrics("localscraper").total_runs == 1
