"""
Extraction tests for the tuned connectors, against committed HTML fixtures.

This is the guard that catches "scraper silently extracts nothing" at PR
time instead of weeks later in production: each tuned scraper's parser runs
against a fixture replicating the site's card DOM and must extract the
expected properties with correct fields.

The fixtures are replicas of the DOM structure the scrapers were tuned
against (the live sites are not reachable from CI). When retuning a
connector, capture the real listing HTML and replace the fixture — the
harness stays the same.
"""


async def load_fixture(page, fixtures_dir, name: str) -> None:
    await page.set_content((fixtures_dir / f"{name}.html").read_text())


class TestPortalZukExtraction:
    async def test_extracts_cards_with_praca_prices(self, page, fixtures_dir):
        from leiloes_scraper.scrapers.custom.portalzuk import PortalZukScraper

        scraper = PortalZukScraper()
        scraper.page = page
        await load_fixture(page, fixtures_dir, "portalzuk")

        props = await scraper._extract_properties()

        assert len(props) == 2

        first = props[0]
        assert first.link_de_acesso == "https://www.portalzuk.com.br/imovel/sp/sao-paulo/apartamento-vila-mariana-55m2"
        assert first.leiloeiro == "portalzuk"
        assert first.uf == "SP"
        assert first.cidade == "São Paulo"
        assert first.endereco == "Rua Domingos de Morais, 1234 - apto 52"
        assert first.descricao == "Apartamento 55m² na Vila Mariana"
        assert first.valor_de_avaliacao == 350000.0
        # 2º leilão (minimum) price wins when present
        assert first.preco == 210000.0

        second = props[1]
        assert second.uf == "PR"
        assert second.cidade == "Curitiba"
        # no 2º leilão price on this card -> falls back to the 1º leilão value
        assert second.preco == 480000.0


class TestFrancoLeiloesExtraction:
    async def test_extracts_cards_with_praca_logic(self, page, fixtures_dir):
        from leiloes_scraper.scrapers.leiloeiros.francoleiloes import FrancoLeiloesScraper

        scraper = FrancoLeiloesScraper()
        scraper.page = page
        await load_fixture(page, fixtures_dir, "francoleiloes")

        props = await scraper._extract_properties()

        assert len(props) == 2

        first = props[0]
        assert first.link_de_acesso == "https://www.francoleiloes.com.br/leilao/1021/lote/7"
        assert first.cidade == "Águas Lindas de Goiás"
        assert first.uf == "GO"
        assert first.bairro == "Parque da Barragem"
        assert first.valor_de_avaliacao == 200000.0
        # praça 1 still active -> praça-1 lance inicial
        assert first.preco == 150000.0
        assert first.img_imovel == "https://www.francoleiloes.com.br/imagens-center/350x282/casa-aguas-lindas.jpg"

        second = props[1]
        assert second.cidade == "Goiânia"
        assert second.bairro == "Setor Bueno"
        # praça 1 ended (dg-opacidade) -> praça-2 lance inicial
        assert second.preco == 160000.0


class TestGPLeiloesExtraction:
    async def test_extracts_hash_route_cards(self, page, fixtures_dir):
        from leiloes_scraper.scrapers.custom.gpleiloes import GPLeiloesScraper

        scraper = GPLeiloesScraper()
        scraper.page = page
        await load_fixture(page, fixtures_dir, "gpleiloes")

        props = await scraper._extract_properties()

        assert len(props) == 2

        first = props[0]
        assert first.link_de_acesso == "https://www.gpleiloes.com.br/gp/#/lote/3647/1"
        assert first.descricao == "Apartamento 3 quartos no Centro de Belo Horizonte"
        assert first.cidade == "Belo Horizonte"
        assert first.uf == "MG"
        assert first.preco == 95000.0

        assert props[1].preco == 1234567.89


class TestPalacioDosLeiloesExtraction:
    async def test_extracts_onclick_lote_cards(self, page, fixtures_dir):
        from leiloes_scraper.scrapers.custom.palaciodosleiloes import PalacioDoLeiloesScraper

        scraper = PalacioDoLeiloesScraper()
        scraper.page = page
        await load_fixture(page, fixtures_dir, "palaciodosleiloes")

        props = await scraper._extract_all_properties()

        assert len(props) == 2

        first = props[0]
        assert first.link_de_acesso == "https://www.palaciodosleiloes.com.br/site/?lote=1479630&leilao=8193"
        assert first.descricao == "Casa com 2 quartos, Bairro Satélite"
        assert first.preco == 180000.0
        assert first.cidade == "Juatuba"
        assert first.img_imovel == "https://www.palaciodosleiloes.com.br/site/fotos/lote-1479630.jpg"

        second = props[1]
        assert second.link_de_acesso == "https://www.palaciodosleiloes.com.br/site/?lote=1479655&leilao=8193"
        assert second.cidade == "Betim"
