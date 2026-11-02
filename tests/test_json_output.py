"""Without DATABASE_URL, a run writes its results to <OUTPUT_DIR>/<source>.json."""

import json

from leiloes_scraper.models import Imovel
from leiloes_scraper.scrapers.base.scraper import BaseScraper


class FakeScraper(BaseScraper):
    def __init__(self, items):
        super().__init__()
        self._items = items

    def get_source_name(self) -> str:
        return "fakeleiloes"

    async def scrape(self) -> list[Imovel]:
        return self._items


def make_imovel(n: int) -> Imovel:
    return Imovel(
        link_de_acesso=f"https://leiloeiro.test/lote/{n}",
        leiloeiro="fakeleiloes",
        uf="SP",
        cidade="São Paulo",
        endereco=f"RUA TESTE, {n}",
        descricao="Apartamento 2 dormitórios",
        modalidade_de_venda="Leilão",
    )


async def test_run_writes_json_file_without_database(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.chdir(tmp_path)  # the health monitor's JSON fallback lands here

    result = await FakeScraper([make_imovel(1), make_imovel(2)]).execute()

    assert result["status"] == "success"
    assert result["total_found"] == 2
    assert result["total_saved"] == 2

    written = json.loads((tmp_path / "out" / "fakeleiloes.json").read_text(encoding="utf-8"))
    assert written["source"] == "fakeleiloes"
    assert written["count"] == 2
    assert written["run"]["status"] == "success"
    assert {p["link_de_acesso"] for p in written["properties"]} == {
        "https://leiloeiro.test/lote/1",
        "https://leiloeiro.test/lote/2",
    }
    # to_db_dict() canonicalizes the city and derives the property type
    assert written["properties"][0]["cidade"] == "SAO PAULO"
    assert written["properties"][0]["tipo_imovel"]


async def test_empty_run_still_writes_a_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.chdir(tmp_path)

    result = await FakeScraper([]).execute()

    assert result["total_found"] == 0
    written = json.loads((tmp_path / "out" / "fakeleiloes.json").read_text(encoding="utf-8"))
    assert written["count"] == 0
    assert written["properties"] == []


def test_run_ids_are_unique_within_one_github_run(monkeypatch):
    # A re-run attempt reuses GITHUB_RUN_ID; scraper_runs.run_id is UNIQUE.
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    first, second = FakeScraper([])._create_run_id(), FakeScraper([])._create_run_id()
    assert first != second
    assert first.startswith("fakeleiloes_123_")
