-- Postgres schema for DATABASE_URL mode.
-- Apply once:  psql "$DATABASE_URL" -f sql/schema.sql
-- (docker compose up applies it automatically on first start.)
-- Safe to re-run: every statement is IF NOT EXISTS / ON CONFLICT DO NOTHING.

-- One row per listing; link_de_acesso (the listing URL) is the identity.
-- A listing that disappears from its site is soft-deleted (status_leilao = false),
-- never removed.
CREATE TABLE IF NOT EXISTS imoveis (
  link_de_acesso      text PRIMARY KEY,
  leiloeiro           text,
  n_do_imovel         text,
  uf                  text,
  cidade              text,
  bairro              text,
  endereco            text,
  preco               numeric,
  valor_de_avaliacao  numeric,
  desconto            numeric,
  descricao           text,
  modalidade_de_venda text NOT NULL,
  status_leilao       boolean,
  geocode_status      boolean,
  img_imovel          text,
  tipo_imovel         text,
  link_pdf_matricula  text,
  updated_at          timestamptz NOT NULL,
  first_seen_at       timestamptz NOT NULL DEFAULT now(),
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_imoveis_leiloeiro_updated_at ON imoveis (leiloeiro, updated_at);
CREATE INDEX IF NOT EXISTS idx_imoveis_status_created_at   ON imoveis (status_leilao, created_at);

-- The auction houses the scrapers cover.
CREATE TABLE IF NOT EXISTS leiloeiros (
  slug        text PRIMARY KEY,
  name        text NOT NULL,
  website_url text NOT NULL UNIQUE
);
INSERT INTO leiloeiros (slug, name, website_url) VALUES
  ('portalzuk',         'Portal Zuk',          'https://www.portalzuk.com.br'),
  ('gpleiloes',         'GP Leilões',          'https://www.gpleiloes.com.br'),
  ('francoleiloes',     'Franco Leilões',      'https://www.francoleiloes.com.br'),
  ('palaciodosleiloes', 'Palácio dos Leilões', 'https://www.palaciodosleiloes.com.br')
ON CONFLICT DO NOTHING;

-- One row per scraper execution.
CREATE TABLE IF NOT EXISTS scraper_runs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id           text NOT NULL UNIQUE,
  source           text NOT NULL,
  started_at       timestamp NOT NULL,
  ended_at         timestamp,
  duration_seconds numeric,
  status           text NOT NULL DEFAULT 'running',
  total_found      integer DEFAULT 0,
  total_new        integer DEFAULT 0,
  total_updated    integer DEFAULT 0,
  total_removed    integer DEFAULT 0,
  total_errors     integer DEFAULT 0,
  requests_made    integer DEFAULT 0,
  pages_scraped    integer DEFAULT 0,
  memory_used_mb   numeric,
  error_message    text,
  error_details    jsonb,
  triggered_by     text DEFAULT 'manual',
  workflow_run_url text,
  created_at       timestamp DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_source_date ON scraper_runs (source, started_at);

-- Daily snapshot of the catalog, written after `runner --all`.
-- (caixa_* columns: the stats model also counts a 'caixa' source when one is
-- present in imoveis; they stay 0 otherwise.)
CREATE TABLE IF NOT EXISTS daily_stats (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  stat_date         date NOT NULL UNIQUE,
  total_active      integer NOT NULL DEFAULT 0,
  total_inactive    integer NOT NULL DEFAULT 0,
  caixa_active      integer NOT NULL DEFAULT 0,
  caixa_inactive    integer NOT NULL DEFAULT 0,
  scrapers_active   integer NOT NULL DEFAULT 0,
  scrapers_inactive integer NOT NULL DEFAULT 0,
  added_today       integer NOT NULL DEFAULT 0,
  removed_today     integer NOT NULL DEFAULT 0,
  added_by_source   jsonb NOT NULL DEFAULT '{}'::jsonb,
  removed_by_source jsonb NOT NULL DEFAULT '{}'::jsonb,
  active_by_source  jsonb NOT NULL DEFAULT '{}'::jsonb,
  caixa_by_uf       jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Durable health per scraper, so "N consecutive failures" and
-- "zero results vs. historical average" survive ephemeral CI runners.
CREATE TABLE IF NOT EXISTS scraper_health (
  scraper_name             text PRIMARY KEY,
  last_run                 timestamp,
  last_success             timestamp,
  last_failure             timestamp,
  last_error_message       text,
  total_runs               integer NOT NULL DEFAULT 0,
  successful_runs          integer NOT NULL DEFAULT 0,
  failed_runs              integer NOT NULL DEFAULT 0,
  partial_runs             integer NOT NULL DEFAULT 0,
  consecutive_failures     integer NOT NULL DEFAULT 0,
  items_found_last_run     integer NOT NULL DEFAULT 0,
  items_saved_last_run     integer NOT NULL DEFAULT 0,
  avg_items_found          numeric NOT NULL DEFAULT 0,
  selector_issues_detected integer NOT NULL DEFAULT 0,
  last_selector_issue      text,
  updated_at               timestamp DEFAULT now()
);
