"""
Scraper Models

Models for scraper configuration and run tracking.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    """Status of a scraper run."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"  # Some items processed, some errors


class ScrapingConfig(BaseModel):
    """Configuration for scraper execution."""

    source_name: str
    headless: bool = True
    timeout: int = 60000  # milliseconds
    max_retries: int = 3
    delay_between_requests: float = 1.0  # seconds
    max_properties: int = 1000
    screenshot_on_error: bool = True
    debug_mode: bool = False
    page_load_wait: float = 2.0  # seconds to wait after navigation for JS to render

    # URLs and selectors
    base_url: str | None = None
    selectors: dict[str, str] = Field(default_factory=dict)


class ScraperRun(BaseModel):
    """
    Model for scraper_runs table.
    Tracks execution metrics for each scraper run.
    """

    # Run Identification
    run_id: str  # Unique identifier (e.g., GitHub Actions run ID)
    source: str  # Which scraper: freitas, portalzuk, etc.

    # Timing
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = None
    duration_seconds: float | None = None

    # Status
    status: RunStatus = RunStatus.RUNNING

    # Results
    total_found: int = 0
    total_new: int = 0
    total_updated: int = 0
    total_removed: int = 0
    total_errors: int = 0

    # Performance Metrics
    requests_made: int = 0
    pages_scraped: int = 0
    memory_used_mb: float | None = None

    # Error Details
    error_message: str | None = None
    error_details: dict[str, Any] | None = None

    # Metadata
    triggered_by: str = "manual"  # 'schedule', 'manual', 'workflow'
    workflow_run_url: str | None = None

    class Config:
        use_enum_values = True

    def complete(self, status: RunStatus = RunStatus.SUCCESS) -> None:
        """Mark run as complete and calculate duration."""
        self.ended_at = datetime.now()
        self.duration_seconds = (self.ended_at - self.started_at).total_seconds()
        self.status = status

    def fail(self, error_message: str, error_details: dict | None = None) -> None:
        """Mark run as failed with error info."""
        self.error_message = error_message
        self.error_details = error_details
        self.complete(RunStatus.FAILED)

    def to_db_dict(self) -> dict:
        """Convert to dictionary for database insertion."""
        # started_at/ended_at stay as native datetimes: asyncpg binds them to
        # the timestamp columns directly and rejects ISO strings.
        return self.dict()
