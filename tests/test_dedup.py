"""Tests for within-run and cross-source deduplication."""

import pytest

from leiloes_scraper.models import Imovel
from leiloes_scraper.pipelines import (
    CrossSourceDeduplicationPipeline,
    DeduplicationPipeline,
    build_dedup_key,
)


def make_imovel(**overrides) -> Imovel:
    defaults = {
        "link_de_acesso": "https://leiloeiro.test/lote/1",
        "leiloeiro": "portalzuk",
        "uf": "SP",
        "cidade": "São Paulo",
        "endereco": "Rua Domingos de Morais, 1234",
        "modalidade_de_venda": "Leilão",
    }
    defaults.update(overrides)
    return Imovel(**defaults)


class TestBuildDedupKey:
    def test_same_property_different_formatting_matches(self):
        # Caixa-style vs. auctioneer-style rendering of the same address
        key_a = build_dedup_key("SP", "SAO PAULO", "RUA DOMINGOS DE MORAIS, N. 1234, CEP: 04010-100")
        key_b = build_dedup_key("sp", "São Paulo", "R. Domingos de Morais 1234")
        assert key_a is not None
        assert key_a == key_b

    def test_street_type_and_number_marker_normalized(self):
        key_a = build_dedup_key("GO", "Goiânia", "Avenida T-63, Nº 500")
        key_b = build_dedup_key("GO", "GOIANIA", "AV T 63 500")
        assert key_a is not None
        assert key_a == key_b

    def test_different_number_does_not_match(self):
        key_a = build_dedup_key("SP", "São Paulo", "Rua Domingos de Morais, 1234")
        key_b = build_dedup_key("SP", "São Paulo", "Rua Domingos de Morais, 1236")
        assert key_a != key_b

    def test_missing_fields_return_none(self):
        assert build_dedup_key(None, "São Paulo", "Rua X, 123") is None
        assert build_dedup_key("SP", None, "Rua X, 123") is None
        assert build_dedup_key("SP", "São Paulo", None) is None

    def test_vague_address_returns_none(self):
        # No street number -> too weak to identify a single property
        assert build_dedup_key("SP", "São Paulo", "Centro") is None
        assert build_dedup_key("SP", "São Paulo", "Rua das Flores") is None


class TestDeduplicationPipeline:
    async def test_within_run_duplicate_dropped(self):
        pipeline = DeduplicationPipeline()
        pipeline.open_pipeline()

        first = await pipeline.process_item(make_imovel())
        second = await pipeline.process_item(make_imovel())

        assert first is not None
        assert second is None
        assert pipeline.get_stats()["duplicates_removed"] == 1


class TestCrossSourceDeduplicationPipeline:
    async def test_drops_item_matching_canonical_listing(self):
        canonical_key = build_dedup_key("SP", "SAO PAULO", "RUA DOMINGOS DE MORAIS, N. 1234")
        pipeline = CrossSourceDeduplicationPipeline(
            source_name="portalzuk",
            canonical_index={canonical_key: "https://venda-imoveis.caixa.gov.br/imovel/999"},
        )

        result = await pipeline.process_item(make_imovel())

        assert result is None
        assert pipeline.get_stats()["cross_source_duplicates_removed"] == 1

    async def test_keeps_item_without_canonical_match(self):
        pipeline = CrossSourceDeduplicationPipeline(source_name="portalzuk", canonical_index={})

        item = make_imovel()
        assert await pipeline.process_item(item) is item

    async def test_canonical_source_never_dedupes_itself(self):
        canonical_key = build_dedup_key("SP", "São Paulo", "Rua Domingos de Morais, 1234")
        pipeline = CrossSourceDeduplicationPipeline(
            source_name="caixa",
            canonical_index={canonical_key: "https://venda-imoveis.caixa.gov.br/imovel/999"},
        )

        item = make_imovel(leiloeiro="caixa", link_de_acesso="https://venda-imoveis.caixa.gov.br/imovel/999")
        assert await pipeline.process_item(item) is item

    async def test_index_load_failure_passes_items_through(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
        async def boom(_sources):
            raise RuntimeError("no database in this environment")

        import leiloes_scraper.database as database

        monkeypatch.setattr(database, "get_cross_source_rows", boom)

        pipeline = CrossSourceDeduplicationPipeline(source_name="portalzuk")
        item = make_imovel()

        # Ingestion must never be blocked by dedup; and the failed load is
        # only attempted once.
        assert await pipeline.process_item(item) is item
        assert pipeline._index_load_failed is True
        assert await pipeline.process_item(make_imovel(link_de_acesso="https://leiloeiro.test/lote/2")) is not None

    async def test_no_database_skips_the_index(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)

        pipeline = CrossSourceDeduplicationPipeline(source_name="portalzuk")
        item = make_imovel()

        assert await pipeline.process_item(item) is item
        assert pipeline._index_load_failed is True

    async def test_index_built_from_database_rows(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
        rows = [
            {
                "link_de_acesso": "https://venda-imoveis.caixa.gov.br/imovel/999",
                "uf": "SP",
                "cidade": "SAO PAULO",
                "endereco": "RUA DOMINGOS DE MORAIS, N. 1234",
            },
            # weak address -> no key, must not blow up
            {"link_de_acesso": "x", "uf": "SP", "cidade": "SAO PAULO", "endereco": "CENTRO"},
        ]

        async def fake_rows(sources):
            assert sources == ["caixa"]
            return rows

        import leiloes_scraper.database as database

        monkeypatch.setattr(database, "get_cross_source_rows", fake_rows)

        pipeline = CrossSourceDeduplicationPipeline(source_name="portalzuk")
        assert await pipeline.process_item(make_imovel()) is None


@pytest.mark.parametrize("source_name", ["portalzuk", "francoleiloes"])
def test_default_pipeline_includes_cross_source_for_scrapers(source_name):
    from leiloes_scraper.pipelines import create_default_pipeline

    runner = create_default_pipeline(source_name=source_name)
    names = [p.__class__.__name__ for p in runner.pipelines]
    assert "CrossSourceDeduplicationPipeline" in names


def test_default_pipeline_skips_cross_source_for_caixa():
    from leiloes_scraper.pipelines import create_default_pipeline

    runner = create_default_pipeline(source_name="caixa")
    names = [p.__class__.__name__ for p in runner.pipelines]
    assert "CrossSourceDeduplicationPipeline" not in names
