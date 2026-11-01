# scrapers-property-actions-brazil

Async Python + Playwright scrapers for Brazilian real-estate auction sites
(leiloeiros). Each scraper turns an auctioneer's listing pages into one
normalized `Imovel` record per property: address, city/UF, minimum bid,
appraisal value, discount, sale type, image and the deep link back to the
auctioneer.

The repository ships the framework plus four connectors whose selectors were
verified against the live sites and are pinned by parser tests:

| Scraper | Site | Pattern |
|---|---|---|
| `portalzuk` | portalzuk.com.br | custom, "load more" pagination |
| `gpleiloes` | gpleiloes.com.br | custom, SPA with hash routes |
| `palaciodosleiloes` | palaciodosleiloes.com.br | custom |
| `francoleiloes` | francoleiloes.com.br | config-driven `GenericPaginationScraper` |

## Quick start

Python 3.11. Two ways to run, depending on what you have:

| You have | Browser | Results go to |
|---|---|---|
| nothing | local headless Chromium | `output/<scraper>.json` |
| a [Browserless](https://www.browserless.io) token | hosted Chromium (`BROWSERLESS_TOKEN`) | `output/<scraper>.json` |
| a Postgres too | either | `imoveis` table (`DATABASE_URL`) |

### Locally

```sh
git clone https://github.com/micazev/scrapers-property-actions-brazil
cd scrapers-property-actions-brazil
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
python -m playwright install chromium        # skip if you use BROWSERLESS_TOKEN

cp .env.example .env                         # optional: fill BROWSERLESS_TOKEN / DATABASE_URL
python -m leiloes_scraper.scrapers.runner --list
python -m leiloes_scraper.scrapers.runner --scraper portalzuk --max 20
cat output/portalzuk.json
```

`--all` runs every enabled scraper; `--no-headless` opens a visible browser,
which is how you tune selectors.

### With Postgres

```sh
docker compose up -d                          # Postgres 16, schema applied on first start
export DATABASE_URL=postgresql://scraper:scraper@localhost:5432/leiloes
python -m leiloes_scraper.scrapers.runner --all
```

Or apply `sql/schema.sql` to any Postgres 13+ yourself.

### On GitHub Actions (no machine needed)

1. Fork the repository.
2. Optional, under **Settings → Secrets and variables → Actions**:
   `BROWSERLESS_TOKEN` (otherwise the job installs Chromium) and
   `DATABASE_URL` (otherwise results are JSON).
3. **Actions → Scrape → Run workflow**, pick the scrapers and a max per scraper.
4. Download the `scrape-<run id>` artifact: `output/*.json`, health file,
   any debug screenshots.

To run it on a schedule, uncomment the `schedule:` block in
`.github/workflows/scrape.yml`.

### With Docker

```sh
docker build -t leiloes-scraper .
docker run --rm -v "$PWD/output:/app/output" leiloes-scraper --scraper portalzuk --max 20
```

## Output

Without a database, each scraper writes `output/<scraper>.json`:

```json
{
  "source": "portalzuk",
  "run": { "run_id": "portalzuk_1a2b3c4d", "status": "success", "total_found": 20, "...": "..." },
  "count": 20,
  "properties": [
    {
      "link_de_acesso": "https://www.portalzuk.com.br/imovel/...",
      "leiloeiro": "portalzuk",
      "uf": "SP",
      "cidade": "SAO PAULO",
      "endereco": "...",
      "preco": 250000.0,
      "valor_de_avaliacao": 410000.0,
      "desconto": 39.0,
      "modalidade_de_venda": "...",
      "tipo_imovel": "apartamento",
      "status_leilao": true
    }
  ]
}
```

With `DATABASE_URL` the same records are upserted into `imoveis`
(`link_de_acesso` is the key; unchanged rows are not rewritten), each run is
logged in `scraper_runs`, and `daily_stats` is refreshed after `--all`.

## How it works

```
runner ──► registry (@register_scraper) ──► Scraper.scrape()  (Playwright page)
                                                │  list[Imovel]
                                                ▼
                         pipelines: validation → in-run dedup → cross-source dedup → defaults
                                                │
                       ┌────────────────────────┴───────────────────────┐
                 DATABASE_URL set                                  not set
        upsert imoveis + soft delete,                      output/<scraper>.json
        scraper_runs, scraper_health
```

- **Registry.** A scraper is a class decorated with
  `@register_scraper("name", enabled=True)`; modules under
  `scrapers/custom/` and `scrapers/leiloeiros/` are discovered at import.
  `runner --all` runs only `enabled=True` scrapers, `PRIORITY_SCRAPERS`
  first, so a half-tuned connector never takes a slot in a full run.
- **Two scraper shapes.** `GenericPaginationScraper` is configuration: a base
  URL, CSS selectors for the card fields, a "next page" selector
  (`francoleiloes.py`). Sites with load-more buttons or SPA routing subclass
  `PlaywrightScraper` and write their own loop (`portalzuk.py`, `gpleiloes.py`).
- **Normalization.** `parse_currency`, `parse_location`, `clean_text`,
  property-type detection from the description, and accent-stripped
  uppercase city names (`scrapers/utils.py`, `models/imovel.py`).
- **Deduplication.** Within a run, by URL. Across sources (Postgres mode),
  by a conservative `uf|cidade|endereco` key against canonical sources: a
  missed match is acceptable, merging two different properties is not.
  Dedup never blocks ingestion; if its index can't load, items pass through.
- **Soft delete.** A listing that disappears from its site gets
  `status_leilao = false`; nothing is deleted. A scrape that returns zero
  items, or fails, touches nothing, so a broken selector can't wipe a source.
- **Health.** Per-scraper success rate, consecutive failures and average
  yield, stored in `scraper_health` (or `scraper_health.json` locally). A run
  that "succeeds" with zero items from a scraper that usually yields is the
  classic broken-selector case: the runner writes `scraper_alerts.json` so a
  CI step can post it somewhere.

## Adding a site

1. Copy `scrapers/leiloeiros/francoleiloes.py` (config-driven) or
   `scrapers/custom/portalzuk.py` (custom loop), register it with
   `@register_scraper("newsite")` (disabled by default).
2. Tune against the live site:
   `python -m leiloes_scraper.scrapers.runner --scraper newsite --no-headless --max 20`
3. Save the listing-card HTML (devtools → copy outer HTML of a few cards) to
   `tests/fixtures/newsite.html` and add a case to
   `tests/test_extraction_fixtures.py` asserting the fields you extract.
4. Flip to `enabled=True` and add it to `EXPECTED_ENABLED` in
   `tests/test_curation.py`.

## Tests

```sh
pip install -r requirements-dev.lock
python -m playwright install chromium
python -m pytest -rs
```

The extraction tests load the HTML in `tests/fixtures/` into a real Chromium
and run each scraper's parser against it, with no network. The fixtures are
small replicas of each site's listing-card markup, not full saved pages:
they pin the parser to the DOM the selectors expect, and a site redesign
shows up as a zero-yield run, not as a red test. They skip
if Chromium can't start; `-rs` shows that. The Postgres test
(`tests/test_database_mode.py`) runs only with `TEST_DATABASE_URL` set to a
throwaway database. CI (`.github/workflows/ci.yml`) runs both.

## Scraping etiquette

- **Public listing facts only.** Address, prices, lot link, dates, images.
  Judicial *editais* can name the debtor; don't store personal data
  (Brazil's LGPD applies to it, and nothing here needs it).
- **Attribution.** Every record keeps its source and the deep link to the
  auctioneer's own page.
- **Gentle by default.** `--concurrent 2 --stagger 5` and ≥1 s between
  requests per scraper. Respect `robots.txt` and each site's terms.
- **No getting around blocks.** If a site serves a CAPTCHA or bot challenge,
  `check_for_bot_protection()` detects it and the scraper stops. That's the
  intended behaviour: contact the auctioneer for a feed instead.

Sites change their HTML; any of these connectors can break without notice.

## License

MIT, see `LICENSE`.
