# Image with Python, the pinned dependencies and a local Chromium.
#   docker build -t leiloes-scraper .
#   docker run --rm -v "$PWD/output:/app/output" leiloes-scraper --scraper portalzuk --max 20
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Chromium and its system libraries at build time, so runs need no apt.
RUN python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY leiloes_scraper/ leiloes_scraper/

ENTRYPOINT ["python", "-m", "leiloes_scraper.scrapers.runner"]
CMD ["--all", "--concurrent", "2", "--stagger", "5"]
