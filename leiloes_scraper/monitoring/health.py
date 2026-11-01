"""
Scraper Health Monitoring

Tracks scraper health metrics and detects broken selectors.

Persistence: metrics are stored per scraper in the scraper_health Postgres
table (durable across the ephemeral GitHub Actions containers the daily run
executes in). When DATABASE_URL is not configured — local dry runs — state
falls back to a scraper_health.json file, which is NOT durable in CI and
exists only so local tuning sessions still get feedback.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("monitoring.health")

# A successful run that finds zero items is flagged as a possible selector
# break only when the scraper historically averaged more than this many items,
# so freshly-added or naturally tiny sources don't page anyone.
ZERO_YIELD_MIN_AVG = 10


@dataclass
class ScraperHealthMetrics:
    """Health metrics for a single scraper."""

    scraper_name: str
    last_run: datetime | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    last_error_message: str | None = None

    # Counters
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    partial_runs: int = 0

    # Recent performance
    consecutive_failures: int = 0
    items_found_last_run: int = 0
    items_saved_last_run: int = 0
    avg_items_found: float = 0.0

    # Selector health
    selector_issues_detected: int = 0
    last_selector_issue: str | None = None

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.total_runs == 0:
            return 0.0
        return (self.successful_runs / self.total_runs) * 100

    @property
    def is_healthy(self) -> bool:
        """Check if scraper is healthy."""
        # Unhealthy if 3+ consecutive failures
        if self.consecutive_failures >= 3:
            return False
        # Unhealthy if success rate below 50% with 5+ runs
        return not (self.total_runs >= 5 and self.success_rate < 50)

    @property
    def health_status(self) -> str:
        """Get health status string."""
        if self.consecutive_failures >= 5:
            return "CRITICAL"
        if self.consecutive_failures >= 3:
            return "UNHEALTHY"
        if self.consecutive_failures >= 1:
            return "DEGRADED"
        if self.total_runs == 0:
            return "UNKNOWN"
        if self.success_rate >= 90:
            return "HEALTHY"
        if self.success_rate >= 70:
            return "GOOD"
        return "FAIR"

    def record_run(
        self,
        success: bool,
        items_found: int = 0,
        items_saved: int = 0,
        error_message: str | None = None,
        partial: bool = False,
    ) -> None:
        """Record a scraper run."""
        self.last_run = datetime.now()
        self.total_runs += 1
        self.items_found_last_run = items_found
        self.items_saved_last_run = items_saved

        # Update running average
        if self.total_runs == 1:
            self.avg_items_found = float(items_found)
        else:
            self.avg_items_found = (self.avg_items_found * (self.total_runs - 1) + items_found) / self.total_runs

        if success:
            self.successful_runs += 1
            self.last_success = datetime.now()
            self.consecutive_failures = 0
        elif partial:
            self.partial_runs += 1
            self.last_success = datetime.now()  # Partial is still somewhat successful
            self.consecutive_failures = 0
        else:
            self.failed_runs += 1
            self.last_failure = datetime.now()
            self.last_error_message = error_message
            self.consecutive_failures += 1

    def record_selector_issue(self, issue: str) -> None:
        """Record a selector issue (e.g., zero results)."""
        self.selector_issues_detected += 1
        self.last_selector_issue = issue

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Convert datetime objects to ISO strings
        for key in ["last_run", "last_success", "last_failure"]:
            if data[key] is not None:
                data[key] = data[key].isoformat()
        data["success_rate"] = self.success_rate
        data["health_status"] = self.health_status
        data["is_healthy"] = self.is_healthy
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScraperHealthMetrics":
        """Create from dictionary."""
        # Parse datetime strings
        for key in ["last_run", "last_success", "last_failure"]:
            if data.get(key):
                data[key] = datetime.fromisoformat(data[key])
        # Remove computed properties that shouldn't be passed to __init__
        data.pop("success_rate", None)
        data.pop("health_status", None)
        data.pop("is_healthy", None)
        return cls(**data)

    def to_db_dict(self) -> dict[str, Any]:
        """Convert to a scraper_health row dict.

        Datetimes stay native (asyncpg binds them to timestamp columns
        directly and rejects ISO strings); floats stay floats.
        """
        return asdict(self)

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "ScraperHealthMetrics":
        """Create from a scraper_health row (native datetimes, extra columns ignored)."""
        field_names = {f.name for f in fields(cls)}
        data = {k: v for k, v in row.items() if k in field_names}
        # numeric columns can come back as Decimal from asyncpg
        if data.get("avg_items_found") is not None:
            data["avg_items_found"] = float(data["avg_items_found"])
        return cls(**data)


class HealthMonitor:
    """
    Central health monitoring for all scrapers.

    Tracks health metrics, detects issues, and generates alerts.

    State is persisted per scraper in the scraper_health Postgres table when
    DATABASE_URL is configured (the CI case — containers are ephemeral, so a
    local file would reset every run). Without DATABASE_URL it falls back to
    a local JSON file so tuning sessions still get feedback.
    """

    def __init__(self, storage_path: Path | None = None, use_database: bool | None = None):
        """
        Initialize health monitor.

        Args:
            storage_path: Path for the JSON fallback file
            use_database: Force the storage backend; default: use Postgres
                when DATABASE_URL is set
        """
        self.storage_path = storage_path or Path("scraper_health.json")
        self.use_database = use_database if use_database is not None else bool(os.environ.get("DATABASE_URL"))
        # In-memory view of the metrics touched/loaded this process, used by
        # get_health_summary()/generate_report().
        self.metrics: dict[str, ScraperHealthMetrics] = {}
        if not self.use_database:
            self._load_metrics_json()

    # ------------------------------------------------------------------
    # JSON fallback storage (local runs without a database)
    # ------------------------------------------------------------------

    def _load_metrics_json(self) -> None:
        """Load metrics from the JSON fallback file."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path) as f:
                    data = json.load(f)
                    for name, metrics_data in data.items():
                        self.metrics[name] = ScraperHealthMetrics.from_dict(metrics_data)
                logger.info(f"Loaded health metrics for {len(self.metrics)} scrapers")
            except Exception as e:
                logger.error(f"Failed to load health metrics: {e}")

    def _save_metrics_json(self) -> None:
        """Save metrics to the JSON fallback file."""
        try:
            data = {name: m.to_dict() for name, m in self.metrics.items()}
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save health metrics: {e}")

    # ------------------------------------------------------------------
    # Durable storage (Postgres scraper_health table)
    # ------------------------------------------------------------------

    async def _load_metrics_db(self, scraper_name: str) -> ScraperHealthMetrics | None:
        from ..database import load_scraper_health

        row = await load_scraper_health(scraper_name)
        return ScraperHealthMetrics.from_db_row(row) if row else None

    async def _save_metrics_db(self, metrics: ScraperHealthMetrics) -> None:
        from ..database import upsert_scraper_health

        await upsert_scraper_health(metrics.to_db_dict())

    async def load_all(self) -> dict[str, ScraperHealthMetrics]:
        """Populate the in-memory view with every persisted health row."""
        if self.use_database:
            from ..database import load_all_scraper_health

            for row in await load_all_scraper_health():
                m = ScraperHealthMetrics.from_db_row(row)
                self.metrics[m.scraper_name] = m
        return self.metrics

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def get_metrics(self, scraper_name: str) -> ScraperHealthMetrics:
        """Get or create in-memory metrics for a scraper."""
        if scraper_name not in self.metrics:
            self.metrics[scraper_name] = ScraperHealthMetrics(scraper_name=scraper_name)
        return self.metrics[scraper_name]

    async def record_run_result(
        self,
        scraper_name: str,
        success: bool,
        items_found: int = 0,
        items_saved: int = 0,
        error_message: str | None = None,
        partial: bool = False,
    ) -> tuple[ScraperHealthMetrics, bool]:
        """
        Record the result of a scraper run.

        Args:
            scraper_name: Name of the scraper
            success: Whether the run was successful
            items_found: Number of items found
            items_saved: Number of items saved
            error_message: Error message if failed
            partial: Whether this was a partial success

        Returns:
            Tuple of (updated metrics, zero_yield_alert) — the alert is True
            when this run "succeeded" with zero items from a scraper that
            historically produced them (likely broken selectors).
        """
        if self.use_database:
            # Load durable state so consecutive_failures / avg_items_found
            # accumulate across (ephemeral) containers.
            try:
                metrics = await self._load_metrics_db(scraper_name)
            except Exception as e:
                logger.warning(f"Failed to load durable health state for {scraper_name}: {e}")
                metrics = None
            metrics = metrics or ScraperHealthMetrics(scraper_name=scraper_name)
            self.metrics[scraper_name] = metrics
        else:
            metrics = self.get_metrics(scraper_name)

        metrics.record_run(
            success=success,
            items_found=items_found,
            items_saved=items_saved,
            error_message=error_message,
            partial=partial,
        )

        # Check for selector issues (zero results with previous successful runs)
        zero_yield_alert = False
        if success and items_found == 0 and metrics.avg_items_found > ZERO_YIELD_MIN_AVG:
            zero_yield_alert = True
            metrics.record_selector_issue(f"Zero results when average is {metrics.avg_items_found:.0f}")
            logger.warning(
                f"Possible selector issue for {scraper_name}: "
                f"0 items found but average is {metrics.avg_items_found:.0f}"
            )

        # Log health alerts
        if metrics.consecutive_failures == 3:
            logger.error(f"HEALTH ALERT: {scraper_name} has failed 3 consecutive times")
        elif metrics.consecutive_failures == 5:
            logger.critical(f"CRITICAL ALERT: {scraper_name} has failed 5 consecutive times")

        if self.use_database:
            try:
                await self._save_metrics_db(metrics)
            except Exception as e:
                logger.error(f"Failed to persist health state for {scraper_name}: {e}")
        else:
            self._save_metrics_json()

        return metrics, zero_yield_alert

    def get_unhealthy_scrapers(self) -> list[ScraperHealthMetrics]:
        """Get list of unhealthy scrapers."""
        return [m for m in self.metrics.values() if not m.is_healthy]

    def get_health_summary(self) -> dict[str, Any]:
        """Get overall health summary."""
        total = len(self.metrics)
        healthy = sum(1 for m in self.metrics.values() if m.is_healthy)
        unhealthy = total - healthy

        by_status = {}
        for m in self.metrics.values():
            status = m.health_status
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "total_scrapers": total,
            "healthy": healthy,
            "unhealthy": unhealthy,
            "by_status": by_status,
            "unhealthy_scrapers": [
                {"name": m.scraper_name, "status": m.health_status, "failures": m.consecutive_failures}
                for m in self.get_unhealthy_scrapers()
            ],
        }

    def generate_report(self) -> str:
        """Generate a text health report."""
        summary = self.get_health_summary()
        lines = [
            "=" * 60,
            "SCRAPER HEALTH REPORT",
            "=" * 60,
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Total Scrapers: {summary['total_scrapers']}",
            f"Healthy: {summary['healthy']}",
            f"Unhealthy: {summary['unhealthy']}",
            "",
            "Status Breakdown:",
        ]

        for status, count in sorted(summary["by_status"].items()):
            lines.append(f"  - {status}: {count}")

        if summary["unhealthy_scrapers"]:
            lines.append("")
            lines.append("UNHEALTHY SCRAPERS:")
            for scraper in summary["unhealthy_scrapers"]:
                lines.append(
                    f"  ❌ {scraper['name']}: {scraper['status']} ({scraper['failures']} consecutive failures)"
                )

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)


# Global health monitor instance
_health_monitor: HealthMonitor | None = None


def get_health_monitor() -> HealthMonitor:
    """Get the global health monitor instance."""
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = HealthMonitor()
    return _health_monitor
