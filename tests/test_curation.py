"""Tests for fleet curation: only verified scrapers run in the daily fleet."""

import json

from leiloes_scraper.scrapers import is_scraper_enabled, list_enabled_scrapers, list_scrapers
from leiloes_scraper.scrapers.runner import PRIORITY_SCRAPERS, _ordered_scrapers, write_alerts_file

# Connectors whose selectors were verified against the live sites.
EXPECTED_ENABLED = {"portalzuk", "gpleiloes", "francoleiloes", "palaciodosleiloes"}


class TestCuratedSet:
    def test_enabled_set_is_the_verified_connectors(self):
        assert set(list_enabled_scrapers()) == EXPECTED_ENABLED

    def test_every_registered_scraper_is_enabled(self):
        # This repository ships only connectors whose selectors are verified
        # and covered by a fixture test (tests/test_extraction_fixtures.py).
        assert set(list_scrapers()) == EXPECTED_ENABLED
        for name in EXPECTED_ENABLED:
            assert is_scraper_enabled(name)


class TestPriorityOrdering:
    def test_priority_scrapers_are_all_enabled(self):
        # An untuned scraper in the priority list wastes the best slot of
        # every run (frazaoleiloes used to run first with empty selectors).
        for name in PRIORITY_SCRAPERS:
            assert is_scraper_enabled(name), f"priority scraper {name} must be enabled/tuned"

    def test_ordered_scrapers_defaults_to_enabled_only(self):
        ordered = _ordered_scrapers()
        assert set(ordered) == EXPECTED_ENABLED
        assert ordered[: len(PRIORITY_SCRAPERS)] == PRIORITY_SCRAPERS

    def test_include_disabled_returns_full_registry(self):
        ordered = _ordered_scrapers(include_disabled=True)
        assert set(ordered) == set(list_scrapers())
        assert ordered[: len(PRIORITY_SCRAPERS)] == PRIORITY_SCRAPERS


class TestAlertsFile:
    def test_zero_yield_alerts_written(self, tmp_path):
        path = tmp_path / "scraper_alerts.json"
        results = [
            {"name": "portalzuk", "status": "success", "zero_yield_alert": True},
            {"name": "gpleiloes", "status": "success", "zero_yield_alert": False},
            {"name": "brokenone", "status": "failed", "error": "boom"},
        ]

        alerts = write_alerts_file(results, path=path)

        assert [a["scraper"] for a in alerts] == ["portalzuk"]
        written = json.loads(path.read_text())
        assert written["alerts"][0]["type"] == "zero_yield"

    def test_stale_alerts_file_removed_when_clean(self, tmp_path):
        path = tmp_path / "scraper_alerts.json"
        path.write_text('{"alerts": [{"scraper": "old"}]}')

        alerts = write_alerts_file([{"name": "portalzuk", "status": "success"}], path=path)

        assert alerts == []
        assert not path.exists()
