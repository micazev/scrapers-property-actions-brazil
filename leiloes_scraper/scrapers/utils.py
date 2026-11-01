"""
Scraper Utilities

Shared utilities for all scrapers.
"""

import re
import unicodedata


def parse_currency(value: str) -> float:
    """
    Parse Brazilian currency format to float.

    Handles formats like:
    - R$ 1.234.567,89
    - R$ 1234567,89
    - 1.234.567,89
    - 1234567.89
    """
    if not value:
        return 0.0

    try:
        # Remove currency symbols and extra whitespace
        cleaned = value.replace("R$", "").replace("RS", "").strip()
        cleaned = re.sub(r"\s+", "", cleaned)

        if "," in cleaned and "." in cleaned:
            # Brazilian format: 1.234.567,89
            parts = cleaned.split(",")
            if len(parts) == 2 and len(parts[1]) <= 2:
                integer_part = parts[0].replace(".", "")
                decimal_part = parts[1]
                cleaned = f"{integer_part}.{decimal_part}"
            else:
                # Unusual format, just remove dots
                cleaned = cleaned.replace(".", "").replace(",", ".")
        elif "," in cleaned:
            # Could be decimal separator or thousands
            parts = cleaned.split(",")
            if len(parts) == 2 and len(parts[1]) <= 2:
                # Decimal separator
                cleaned = cleaned.replace(",", ".")
            else:
                # Thousands separator
                cleaned = cleaned.replace(",", "")
        elif cleaned.count(".") > 1:
            # Multiple dots - thousands separators
            parts = cleaned.split(".")
            if len(parts[-1]) <= 2:
                # Last part is decimal
                integer_parts = parts[:-1]
                decimal_part = parts[-1]
                cleaned = "".join(integer_parts) + "." + decimal_part
            else:
                # All are thousands separators
                cleaned = cleaned.replace(".", "")

        return float(cleaned)

    except (ValueError, AttributeError):
        return 0.0


def normalize_cidade(s: str | None) -> str | None:
    """
    Canonical form for cidade values written to imoveis.cidade.

    Strips accents (NFD + Mn filter) and uppercases so every source writes
    the same form and a city filter can compare with plain equality.
    """
    if not s:
        return s
    return "".join(c for c in unicodedata.normalize("NFD", s.strip()) if unicodedata.category(c) != "Mn").upper()


def parse_location(location_text: str) -> tuple[str, str, str]:
    """
    Parse location string to extract city, state, and neighborhood.

    Common formats:
    - "Cidade/UF"
    - "Cidade - UF"
    - "Bairro, Cidade/UF"
    - "Bairro, Cidade - UF"

    Returns:
        Tuple of (cidade, uf, bairro)
    """
    if not location_text:
        return ("", "", "")

    location_text = location_text.strip()

    cidade = ""
    uf = ""
    bairro = ""

    # Try to extract bairro first (before comma)
    if "," in location_text:
        parts = location_text.split(",", 1)
        bairro = parts[0].strip()
        location_text = parts[1].strip()

    # Extract UF (usually after / or -)
    if "/" in location_text:
        parts = location_text.rsplit("/", 1)
        cidade = parts[0].strip()
        uf = parts[1].strip()[:2].upper()  # Take first 2 chars
    elif " - " in location_text:
        parts = location_text.rsplit(" - ", 1)
        cidade = parts[0].strip()
        uf = parts[1].strip()[:2].upper()
    else:
        cidade = location_text

    return (cidade, uf, bairro)


def extract_state_from_text(text: str) -> str:
    """
    Extract Brazilian state code from text.

    Looks for 2-letter state codes.
    """
    if not text:
        return ""

    # Valid Brazilian state codes
    valid_states = {
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    }

    # Look for state patterns
    patterns = [
        r"\b([A-Z]{2})\b",  # Standalone 2-letter code
        r"/\s*([A-Z]{2})\b",  # After slash
        r"-\s*([A-Z]{2})\b",  # After hyphen
    ]

    text_upper = text.upper()
    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            state = match.group(1)
            if state in valid_states:
                return state

    return ""


def clean_text(text: str | None) -> str:
    """Clean and normalize text content."""
    if not text:
        return ""

    # Remove extra whitespace
    cleaned = " ".join(text.split())
    # Remove common artifacts
    cleaned = cleaned.replace("Font Awesome fontawesome.com", "").strip()
    return cleaned


def parse_brazilian_decimal(value: str | None) -> float | None:
    """
    Parse Brazilian decimal format to float.

    Handles raw numeric values like "1.234,56" (no currency symbol).
    Strips dots (thousands separator), replaces comma with dot (decimal separator).

    Returns None if value is empty or not a valid number.
    """
    if not value or not value.strip():
        return None
    normalized = value.strip().replace(".", "").replace(",", ".")
    try:
        result = float(normalized)
        return result if result == result else None  # NaN check
    except (ValueError, TypeError):
        return None


def extract_property_type(text: str) -> str:
    """
    Extract property type from description text.

    Searches for keywords: "casa", "sala", "terreno", or "apartamento".
    Returns the found keyword, or "outros" if none are found.
    Never returns empty string.

    Args:
        text: Description text to search

    Returns:
        One of: "casa", "sala", "terreno", "apartamento", or "outros"
    """
    if not text:
        return "outros"

    text_lower = text.lower()

    # Check for each keyword in priority order
    if "apartamento" in text_lower:
        return "apartamento"
    if "casa" in text_lower:
        return "casa"
    if "terreno" in text_lower:
        return "terreno"
    if "sala" in text_lower:
        return "sala"

    return "outros"
