"""Unit tests for shared parsing utilities."""

from leiloes_scraper.scrapers.utils import (
    clean_text,
    extract_property_type,
    extract_state_from_text,
    normalize_cidade,
    parse_currency,
    parse_location,
)


class TestParseCurrency:
    def test_brazilian_full_format(self):
        assert parse_currency("R$ 1.234.567,89") == 1234567.89

    def test_no_thousands_separator(self):
        assert parse_currency("R$ 1234567,89") == 1234567.89

    def test_plain_decimal(self):
        assert parse_currency("1234567.89") == 1234567.89

    def test_no_symbol(self):
        assert parse_currency("350.000,00") == 350000.0

    def test_garbage_returns_zero(self):
        assert parse_currency("consulte o edital") == 0.0

    def test_empty_returns_zero(self):
        assert parse_currency("") == 0.0


class TestParseLocation:
    def test_cidade_uf_slash(self):
        assert parse_location("Belo Horizonte/MG") == ("Belo Horizonte", "MG", "")

    def test_cidade_uf_dash(self):
        assert parse_location("Curitiba - PR") == ("Curitiba", "PR", "")

    def test_bairro_cidade_uf(self):
        assert parse_location("Centro, São Paulo/SP") == ("São Paulo", "SP", "Centro")

    def test_only_cidade(self):
        assert parse_location("Salvador") == ("Salvador", "", "")


class TestExtractPropertyType:
    def test_apartamento_beats_casa(self):
        assert extract_property_type("Apartamento com casa de fundos") == "apartamento"

    def test_terreno(self):
        assert extract_property_type("Terreno 300m² plano") == "terreno"

    def test_fallback(self):
        assert extract_property_type("Galpão industrial") == "outros"

    def test_empty(self):
        assert extract_property_type("") == "outros"


class TestNormalizeCidade:
    def test_strips_accents_and_uppercases(self):
        assert normalize_cidade("Águas Lindas de Goiás") == "AGUAS LINDAS DE GOIAS"

    def test_none_passthrough(self):
        assert normalize_cidade(None) is None


class TestMisc:
    def test_clean_text_collapses_whitespace(self):
        assert clean_text("  Casa \n  150m²  ") == "Casa 150m²"

    def test_extract_state_from_text(self):
        assert extract_state_from_text("Águas Lindas de Goiás/GO - Casa") == "GO"
