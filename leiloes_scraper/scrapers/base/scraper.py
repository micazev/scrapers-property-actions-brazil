"""
Base Scraper Classes

Abstract base classes for all property scrapers.
"""

import asyncio
import logging
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from playwright.async_api import Browser, Page, async_playwright

from ...database import (
    complete_run,
    create_run,
    mark_missing_as_inactive,
    upsert_imoveis,
)
from ...models import Imovel, RunStatus, ScraperRun, ScrapingConfig
from ...storage import using_database, write_json_output

if TYPE_CHECKING:
    from ...pipelines import PipelineRunner

# Configure logging for scrapers
logger = logging.getLogger("scrapers")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class BaseScraper(ABC):
    """
    Abstract base class for all scrapers.

    Subclasses must implement:
    - get_source_name(): Return scraper source identifier
    - scrape(): Execute scraping and return properties
    """

    def __init__(
        self,
        config: ScrapingConfig | None = None,
        pipeline_runner: Optional["PipelineRunner"] = None,
    ):
        self.config = config or ScrapingConfig(source_name=self.get_source_name())
        self.run: ScraperRun | None = None
        self.logger = logging.getLogger(f"scrapers.{self.get_source_name()}")
        self._pipeline_runner = pipeline_runner

    @abstractmethod
    def get_source_name(self) -> str:
        """Return scraper source identifier (e.g., 'freitas', 'portalzuk')."""
        pass

    @abstractmethod
    async def scrape(self) -> list[Imovel]:
        """Execute scraping and return list of properties."""
        pass

    def _create_run_id(self) -> str:
        """Create a unique run ID.

        The GitHub run ID is kept for traceability, but it is not unique on its
        own: a re-run attempt reuses it, and one job can run a scraper twice.
        The random suffix keeps scraper_runs.run_id unique in both cases.
        """
        suffix = uuid.uuid4().hex[:8]
        github_run_id = os.environ.get("GITHUB_RUN_ID")
        if github_run_id:
            return f"{self.get_source_name()}_{github_run_id}_{suffix}"
        return f"{self.get_source_name()}_{suffix}"

    def _get_workflow_url(self) -> str | None:
        """Get GitHub Actions workflow URL if available."""
        server = os.environ.get("GITHUB_SERVER_URL")
        repo = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")

        if all([server, repo, run_id]):
            return f"{server}/{repo}/actions/runs/{run_id}"
        return None

    async def start_run(self) -> ScraperRun:
        """Initialize and record a new scraper run."""
        triggered_by = "schedule" if os.environ.get("GITHUB_EVENT_NAME") == "schedule" else "manual"

        self.run = ScraperRun(
            run_id=self._create_run_id(),
            source=self.get_source_name(),
            started_at=datetime.now(),
            triggered_by=triggered_by,
            workflow_run_url=self._get_workflow_url(),
        )

        if using_database():
            await create_run(self.run)
        self.logger.info(f"Started run {self.run.run_id}")
        return self.run

    def log_error(self, message: str, exception: Exception | None = None) -> None:
        """Log an error and increment the error counter."""
        if self.run:
            self.run.total_errors += 1
        if exception:
            self.logger.error(f"{message}: {exception}")
        else:
            self.logger.error(message)

    def increment_pages(self) -> None:
        """Increment the pages scraped counter."""
        if self.run:
            self.run.pages_scraped += 1

    async def finish_run(self, status: RunStatus = RunStatus.SUCCESS, error_message: str | None = None) -> None:
        """Complete the current scraper run."""
        if self.run:
            if error_message:
                self.run.error_message = error_message
            # Determine final status based on errors
            if status == RunStatus.SUCCESS and self.run.total_errors > 0:
                if self.run.total_found > 0:
                    status = RunStatus.PARTIAL
                else:
                    status = RunStatus.FAILED
            if using_database():
                await complete_run(self.run, status)
            else:
                self.run.complete(status)
            self.logger.info(
                f"Finished run {self.run.run_id}: {status.value}, "
                f"found={self.run.total_found}, removed={self.run.total_removed}, "
                f"errors={self.run.total_errors}, pages={self.run.pages_scraped}"
            )

    async def execute(self, use_pipelines: bool = True, track_health: bool = True) -> dict:
        """
        Full execution pipeline:
        1. Start run tracking
        2. Scrape new properties
        3. Run items through processing pipelines (validation, deduplication, etc.)
        4. Upsert to database
        5. Mark missing properties as inactive (soft delete) - only if scrape succeeded
        6. Complete run tracking
        7. Record health metrics

        IMPORTANT: We use soft delete (status_leilao=False) instead of hard delete.
        This ensures that if a scraper fails, existing data is preserved.
        Properties not found in the latest scrape are marked inactive, not deleted.

        Args:
            use_pipelines: If True and no pipeline_runner is set, create default pipelines
            track_health: If True, record health metrics for this run

        Returns:
            Dict with execution results
        """
        await self.start_run()

        # Initialize pipelines if needed
        pipeline_runner = self._pipeline_runner
        if use_pipelines and pipeline_runner is None:
            from ...pipelines import create_default_pipeline

            pipeline_runner = create_default_pipeline(source_name=self.get_source_name())

        try:
            leiloeiro_slug = self.get_source_name()

            # Scrape properties FIRST (before any deletion)
            self.logger.info("Starting scrape...")
            properties = await self.scrape()
            self.run.total_found = len(properties)
            self.logger.info(f"Scraped {len(properties)} properties")

            # Run items through processing pipelines
            if pipeline_runner and properties:
                self.logger.info("Running items through processing pipelines...")
                pipeline_runner.open_all()
                properties = await pipeline_runner.process_items(properties)
                pipeline_runner.close_all()

                pipeline_stats = pipeline_runner.get_stats()
                self.logger.info(
                    f"Pipeline processing complete: "
                    f"{pipeline_stats['items_processed']} processed, "
                    f"{pipeline_stats['items_dropped']} dropped"
                )

            if not using_database():
                # No DATABASE_URL: results go to a JSON file, written below once
                # the run is finished so the file carries the final status.
                self.run.total_new = len(properties)
                self.run.total_removed = 0
            # Upsert to database
            elif properties:
                self.logger.info(f"Upserting {len(properties)} properties to database...")
                # upsert_imoveis marks scraped rows active (status_leilao=True via
                # to_db_dict) and, through its diff-guard, rewrites only rows whose
                # content actually changed — so the old blanket "mark every row of
                # this leiloeiro active" UPDATE is no longer needed (it rewrote the
                # whole source's rows every run).
                result = await upsert_imoveis(properties)
                self.run.total_new = result.get("count", 0)

                # Soft delete: Mark properties not found in this scrape as inactive
                # Only do this if we found at least some properties (prevents wiping on empty scrape)
                active_links = [p.link_de_acesso for p in properties]
                inactive_count = await mark_missing_as_inactive(leiloeiro_slug, active_links)
                self.run.total_removed = inactive_count
                if inactive_count > 0:
                    self.logger.info(f"Marked {inactive_count} properties as inactive (not found in latest scrape)")
            else:
                # No properties found - don't mark anything as inactive
                # This prevents data loss when scraper returns empty results
                self.logger.warning(
                    f"No properties found for {leiloeiro_slug}. Skipping soft delete to preserve existing data."
                )
                self.run.total_removed = 0

            await self.finish_run(RunStatus.SUCCESS)

            if not using_database():
                path = write_json_output(leiloeiro_slug, self.run, properties)
                self.logger.info(f"Wrote {len(properties)} properties to {path}")

            # Record health metrics (durable, DB-backed); a successful run
            # with zero yield from a historically-producing scraper is a
            # silent selector regression and is surfaced to the runner.
            zero_yield_alert = False
            if track_health:
                zero_yield_alert = await self._record_health_metrics(
                    success=True,
                    items_found=self.run.total_found,
                    items_saved=self.run.total_new,
                    partial=(self.run.total_errors > 0),
                )

            return {
                "source": self.get_source_name(),
                "status": "success",
                "total_found": self.run.total_found,
                "total_saved": self.run.total_new,
                "total_removed": self.run.total_removed,
                "total_errors": self.run.total_errors,
                "pages_scraped": self.run.pages_scraped,
                "zero_yield_alert": zero_yield_alert,
            }

        except Exception as e:
            self.logger.exception(f"Fatal error during scraping: {e}")
            # IMPORTANT: On failure, we do NOT delete/mark anything as inactive
            # This preserves existing data when scrapers fail
            await self.finish_run(RunStatus.FAILED, str(e))

            # Record health metrics for failure
            if track_health:
                await self._record_health_metrics(
                    success=False,
                    items_found=self.run.total_found if self.run else 0,
                    items_saved=self.run.total_new if self.run else 0,
                    error_message=str(e),
                )

            raise

    async def _record_health_metrics(
        self,
        success: bool,
        items_found: int = 0,
        items_saved: int = 0,
        error_message: str | None = None,
        partial: bool = False,
    ) -> bool:
        """Record health metrics for this scraper run.

        Returns:
            True when the run triggered a zero-yield (possible selector
            break) alert. Health-recording failures never fail the run.
        """
        try:
            from ...monitoring import get_health_monitor

            monitor = get_health_monitor()
            _, zero_yield_alert = await monitor.record_run_result(
                scraper_name=self.get_source_name(),
                success=success,
                items_found=items_found,
                items_saved=items_saved,
                error_message=error_message,
                partial=partial,
            )
            return zero_yield_alert
        except Exception as e:
            self.logger.warning(f"Failed to record health metrics: {e}")
            return False


class PlaywrightScraper(BaseScraper):
    """
    Playwright-based scraper with browser automation.

    Provides async context manager for browser lifecycle.

    Uses a hosted Chromium (Browserless.io or any CDP-compatible endpoint)
    when BROWSERLESS_TOKEN is set; otherwise launches a local Chromium.
    If a site answers with a bot challenge, scrapers detect it
    (check_for_bot_protection) and stop rather than try to get around it.
    """

    # Browserless.io configuration
    BROWSERLESS_ENDPOINT = "wss://production-sfo.browserless.io"

    def __init__(self, config: ScrapingConfig | None = None):
        super().__init__(config)
        self.playwright = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self._using_browserless = False

    def _get_browserless_url(self) -> str | None:
        """Build Browserless.io WebSocket URL if token is configured."""
        token = os.environ.get("BROWSERLESS_TOKEN")
        if not token:
            return None

        # See: https://docs.browserless.io/baas/connect-playwright
        endpoint = os.environ.get("BROWSERLESS_ENDPOINT", self.BROWSERLESS_ENDPOINT)
        return f"{endpoint}?token={token}"

    async def __aenter__(self) -> "PlaywrightScraper":
        """Start Playwright and browser (local or Browserless.io cloud)."""
        self.playwright = await async_playwright().start()

        browserless_url = self._get_browserless_url()

        if browserless_url:
            # Connect to the hosted browser over CDP
            self.logger.info("Connecting to hosted browser (Browserless)")

            # Retry logic for Browserless connection
            max_retries = 3
            last_exception = None

            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        self.logger.info(f"Retrying connection (attempt {attempt + 1}/{max_retries})...")

                    self.browser = await self.playwright.chromium.connect_over_cdp(browserless_url)
                    self._using_browserless = True
                    break
                except Exception as e:
                    last_exception = e
                    self.logger.warning(f"Connection failed (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        # Exponential backoff: 2s, 4s, 8s...
                        wait_time = 2 * (2**attempt)
                        await asyncio.sleep(wait_time)

            if not self.browser:
                raise Exception(f"Failed to connect to Browserless after {max_retries} attempts") from last_exception

            # Browserless provides a context, create a new page in it
            context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
            self.page = await context.new_page()
        else:
            # Fall back to local browser
            self.logger.debug("Using local Chromium browser")
            self.browser = await self.playwright.chromium.launch(headless=self.config.headless)
            self.page = await self.browser.new_page()

        # Increase timeout for slow sites (2 minutes instead of 1)
        # Many auction sites are slow
        timeout = max(self.config.timeout, 120000)  # At least 120 seconds
        self.page.set_default_timeout(timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close browser and Playwright."""
        if self.page:
            await self.page.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def goto(self, url: str, wait_until: str = "networkidle", max_retries: int = 3) -> None:
        """Navigate to URL with waiting and retry logic."""
        last_exception = None

        for attempt in range(max_retries):
            try:
                await self.page.goto(url, wait_until=wait_until)
                self.run.requests_made += 1
                return
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = 2 * (2**attempt)  # Exponential backoff: 2s, 4s, 8s
                    self.logger.warning(
                        f"Page navigation failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.error(f"Page navigation failed after {max_retries} attempts: {e}")

        if last_exception:
            raise last_exception

    async def screenshot(self, name: str) -> str:
        """Take a screenshot for debugging."""
        os.makedirs("screenshots", exist_ok=True)
        path = f"screenshots/{self.get_source_name()}_{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        await self.page.screenshot(path=path)
        return path

    async def safe_get_text(self, selector: str, default: str = "") -> str:
        """Safely get text from selector."""
        try:
            element = await self.page.query_selector(selector)
            if element:
                return (await element.text_content() or "").strip()
        except Exception:
            pass
        return default

    async def safe_get_attribute(self, selector: str, attribute: str, default: str = "") -> str:
        """Safely get attribute from selector."""
        try:
            element = await self.page.query_selector(selector)
            if element:
                return (await element.get_attribute(attribute) or "").strip()
        except Exception:
            pass
        return default

    async def check_for_bot_protection(self) -> bool:
        """
        Check if we're seeing a bot protection page instead of real content.

        Returns True if bot protection is detected, False otherwise.
        Takes a debug screenshot if protection is detected.
        """
        try:
            page_content = await self.page.content()
            page_content_lower = page_content.lower()
            page_title = await self.page.title()
            page_title_lower = page_title.lower() if page_title else ""

            # Common bot protection indicators
            bot_indicators = [
                "captcha",
                "challenge",
                "cloudflare",
                "access denied",
                "please verify",
                "robot",
                "bot detection",
                "security check",
                "ddos protection",
                "checking your browser",
                "just a moment",
                "ray id",
                "attention required",
                "blocked",
                "forbidden",
                "recaptcha",
                "hcaptcha",
            ]

            detected_indicators = []
            for indicator in bot_indicators:
                if indicator in page_content_lower or indicator in page_title_lower:
                    detected_indicators.append(indicator)

            if detected_indicators:
                self.logger.error(f"Bot protection detected! Indicators found: {detected_indicators}")
                self.logger.error(f"Page title: {page_title}")
                self.logger.error(f"Current URL: {self.page.url}")

                # Take screenshot for debugging
                try:
                    screenshot_path = await self.screenshot("bot_protection")
                    self.logger.info(f"Bot protection screenshot saved: {screenshot_path}")
                except Exception as ss_err:
                    self.logger.warning(f"Failed to save bot protection screenshot: {ss_err}")

                return True

            return False

        except Exception as e:
            self.logger.warning(f"Error checking for bot protection: {e}")
            return False

    async def log_page_debug_info(self) -> None:
        """Log debug information about the current page state."""
        try:
            page_title = await self.page.title()
            page_url = self.page.url

            self.logger.info(f"Page URL: {page_url}")
            self.logger.info(f"Page title: {page_title}")

            # Log a snippet of the page content for debugging
            page_content = await self.page.content()
            content_preview = page_content[:500].replace("\n", " ").strip()
            self.logger.debug(f"Page content preview: {content_preview}...")

        except Exception as e:
            self.logger.warning(f"Error logging page debug info: {e}")


class GenericPaginationScraper(PlaywrightScraper):
    """
    Generic scraper that handles pagination-based auction websites.

    This base class implements the common scraping pattern used by most
    auction websites:
    1. Navigate to listing page
    2. Extract properties from cards
    3. Navigate to next page (via pagination or "load more" button)
    4. Repeat until max properties reached or no more pages

    Subclasses only need to:
    - Call super().__init__() with proper ScrapingConfig
    - Implement get_source_name()
    - Optionally override _extract_property_from_card() for custom extraction
    """

    def __init__(
        self,
        config: ScrapingConfig,
        max_properties: int = 1000,
        max_pages: int = 50,
        default_uf: str = "SP",
        default_cidade: str = "São Paulo",
    ):
        super().__init__(config)
        self.max_properties = max_properties
        self.max_pages = max_pages
        self.default_uf = default_uf
        self.default_cidade = default_cidade
        self.seen_links: set[str] = set()

    async def scrape(self) -> list[Imovel]:
        """Execute the common pagination-based scraping pattern."""
        # Import here to avoid circular imports

        properties = []

        async with self:
            try:
                self.logger.info(f"Navigating to {self.config.base_url}")
                await self.goto(self.config.base_url)
                await asyncio.sleep(self.config.page_load_wait)

                page_num = 1

                while len(properties) < self.max_properties and page_num <= self.max_pages:
                    self.logger.debug(f"Processing page {page_num}")

                    # Extract properties from current page
                    new_properties = await self._extract_properties()

                    if new_properties:
                        properties.extend(new_properties)
                        self.logger.info(
                            f"Page {page_num}: extracted {len(new_properties)} properties (total: {len(properties)})"
                        )
                    else:
                        self.logger.info(f"Page {page_num}: no properties found, stopping")
                        break

                    self.increment_pages()

                    # Try to navigate to next page
                    if not await self._navigate_next_page():
                        self.logger.info("No more pages available")
                        break

                    page_num += 1
                    await asyncio.sleep(self.config.delay_between_requests)

            except Exception as e:
                self.log_error("Error during scraping", e)
                if self.config.screenshot_on_error:
                    await self.screenshot("error")
                raise

        return properties[: self.max_properties]

    async def _extract_properties(self) -> list[Imovel]:
        """Extract properties from current page."""
        properties = []

        try:
            selector = self.config.selectors.get("property_cards", "")
            if not selector:
                self.log_error("No property_cards selector configured")
                return properties

            # Wait for property cards to be present (with timeout)
            try:
                await self.page.wait_for_selector(selector, timeout=10000)
            except Exception as wait_error:
                self.logger.warning(f"Timeout waiting for selector '{selector}': {wait_error}")
                # Take a screenshot for debugging
                try:
                    screenshot_path = await self.screenshot("no_cards_timeout")
                    self.logger.info(f"Debug screenshot saved: {screenshot_path}")
                except Exception:
                    pass

            cards = await self.page.query_selector_all(selector)
            self.logger.info(f"Found {len(cards)} property cards using selector: {selector}")

            if len(cards) == 0:
                # Take screenshot and log page URL for debugging zero results
                self.logger.warning(f"No property cards found on page: {self.page.url}")
                try:
                    screenshot_path = await self.screenshot("zero_cards")
                    self.logger.info(f"Debug screenshot saved: {screenshot_path}")
                except Exception:
                    pass

            for i, card in enumerate(cards):
                try:
                    prop = await self._extract_property_from_card(card)
                    if prop and prop.link_de_acesso and prop.link_de_acesso not in self.seen_links:
                        self.seen_links.add(prop.link_de_acesso)
                        properties.append(prop)
                except Exception as e:
                    self.log_error(f"Error extracting property {i}", e)
                    continue

            # Log if cards were found but no properties extracted (selector mismatch)
            if len(cards) > 0 and len(properties) == 0:
                self.logger.warning(
                    f"Found {len(cards)} cards but extracted 0 properties - likely selector mismatch or missing links"
                )

        except Exception as e:
            self.log_error("Error querying property cards", e)

        return properties

    async def _extract_property_from_card(self, card) -> Imovel | None:
        """
        Extract property data from a card element.

        This method can be overridden by subclasses for custom extraction logic.
        The leiloeiro field is automatically set from get_source_name().
        """
        from ..utils import clean_text, parse_currency, parse_location

        try:
            # Extract link
            link_selector = self.config.selectors.get("card_link", "a")
            link_element = await card.query_selector(link_selector)
            if not link_element:
                link_element = await card.query_selector("a")

            link_href = ""
            if link_element:
                link_href = await link_element.get_attribute("href") or ""
                if link_href and not link_href.startswith("http"):
                    # Construct full URL from base
                    base_domain = self._get_base_domain()
                    link_href = f"{base_domain}{link_href}"

            if not link_href:
                return None

            # Extract title/description
            title_selector = self.config.selectors.get("card_title", ".titulo, .title, h3, h4")
            title_element = await card.query_selector(title_selector)
            descricao = ""
            if title_element:
                descricao = clean_text(await title_element.text_content())

            # Extract price
            price_selector = self.config.selectors.get("card_price", ".preco, .valor")
            price_element = await card.query_selector(price_selector)
            preco = 0.0
            if price_element:
                price_text = await price_element.text_content() or ""
                preco = parse_currency(price_text)

            # Extract location
            location_selector = self.config.selectors.get("card_location", ".localizacao, .endereco")
            location_element = await card.query_selector(location_selector)
            cidade, uf, bairro = "", "", ""
            if location_element:
                location_text = await location_element.text_content() or ""
                cidade, uf, bairro = parse_location(location_text)

            # Extract image URL
            img_url = ""
            image_selector = self.config.selectors.get("card_image", "img")
            if image_selector:
                img_element = await card.query_selector(image_selector)
                if img_element:
                    img_url = await img_element.get_attribute("src") or ""

            # The leiloeiro slug is the source name (derived from filename)
            leiloeiro_slug = self.get_source_name()

            return Imovel(
                link_de_acesso=link_href,
                leiloeiro=leiloeiro_slug,
                uf=uf or self.default_uf,
                cidade=cidade or self.default_cidade,
                bairro=bairro,
                descricao=descricao,
                preco=preco,
                valor_de_avaliacao=preco,
                modalidade_de_venda="Leilão",
                img_imovel=img_url or None,
            )

        except Exception as e:
            self.log_error("Error parsing property card", e)
            return None

    def _get_base_domain(self) -> str:
        """Extract base domain from config base_url."""
        if not self.config.base_url:
            return ""
        # Extract protocol and domain
        from urllib.parse import urlparse

        parsed = urlparse(self.config.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def _navigate_next_page(self) -> bool:
        """
        Try to navigate to next page.

        Tries load more button first, then pagination links.
        """
        try:
            # Try load more button first
            load_more_selector = self.config.selectors.get("load_more", "")
            if load_more_selector:
                load_more = await self.page.query_selector(load_more_selector)
                if load_more and await load_more.is_visible():
                    await load_more.click()
                    await asyncio.sleep(2)
                    return True

            # Try pagination next link
            pagination_selector = self.config.selectors.get("pagination", ".pagination")
            next_selectors = [
                f"{pagination_selector} .next a",
                f"{pagination_selector} .proximo a",
                'a[rel="next"]',
                ".pagination .next a",
                "a.next",
            ]

            for selector in next_selectors:
                try:
                    next_link = await self.page.query_selector(selector)
                    if next_link and await next_link.is_visible():
                        await next_link.click()
                        await asyncio.sleep(2)
                        return True
                except Exception:
                    continue

        except Exception as e:
            self.log_error("Error navigating to next page", e)

        return False
