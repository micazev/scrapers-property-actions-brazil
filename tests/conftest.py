"""
Shared test fixtures.

Run from the repository root:
    python -m pytest
"""

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Make the `leiloes_scraper` package importable regardless of pytest's rootdir.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest_asyncio.fixture
async def page():
    """A Playwright page for parser tests (skips if Chromium is unavailable).

    Extraction tests load captured/replica HTML with page.set_content() and
    run the scraper's parsing methods against it — no network involved.
    """
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = None
    try:
        try:
            browser = await playwright.chromium.launch(headless=True)
        except Exception:
            # Environments with a pre-provisioned browser at a fixed path
            # (revision mismatch with the pip-installed playwright).
            executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "/opt/pw-browsers/chromium")
            if not Path(executable).exists():
                raise
            browser = await playwright.chromium.launch(headless=True, executable_path=executable)
    except Exception as e:
        await playwright.stop()
        pytest.skip(f"Chromium unavailable for extraction tests: {e}")

    page = await browser.new_page()
    yield page
    await browser.close()
    await playwright.stop()
