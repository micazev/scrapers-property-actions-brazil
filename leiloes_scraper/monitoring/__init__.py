"""
Monitoring Module

Health monitoring and alerting for scrapers.
"""

# yield_report is deliberately not imported here: it is a CLI module
# (`python -m leiloes_scraper.monitoring.yield_report`), and importing it from the
# package __init__ triggers a double-import RuntimeWarning under `-m`.
from .health import (
    HealthMonitor,
    ScraperHealthMetrics,
    get_health_monitor,
)

__all__ = [
    "ScraperHealthMetrics",
    "HealthMonitor",
    "get_health_monitor",
]
