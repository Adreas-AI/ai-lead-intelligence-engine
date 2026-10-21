"""Country names / ISO-3166 alpha-2 codes."""

from __future__ import annotations

COUNTRY_NAMES: dict[str, str] = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia",
    "ES": "Spain", "SE": "Sweden", "GB": "United Kingdom", "CH": "Switzerland", "NO": "Norway",
    "RS": "Serbia", "MK": "North Macedonia", "AL": "Albania", "ME": "Montenegro", "TR": "Turkey",
    "UA": "Ukraine", "EG": "Egypt", "MA": "Morocco", "TN": "Tunisia", "IL": "Israel",
    "JO": "Jordan", "LB": "Lebanon", "AE": "United Arab Emirates", "SA": "Saudi Arabia",
    "US": "United States", "CA": "Canada", "MX": "Mexico", "BR": "Brazil", "AR": "Argentina",
    "ZA": "South Africa", "IN": "India", "CN": "China", "JP": "Japan", "AU": "Australia",
}  # fmt: skip

COUNTRY_ALIASES: dict[str, str] = {
    "hellas": "GR", "greek": "GR", "ελλάδα": "GR", "ελλαδα": "GR", "uk": "GB",
    "great britain": "GB", "england": "GB", "holland": "NL", "the netherlands": "NL",
    "czech republic": "CZ", "usa": "US", "united states of america": "US", "türkiye": "TR",
    "turkiye": "TR", "uae": "AE", "deutschland": "DE", "españa": "ES", "espana": "ES",
    "italia": "IT",
}  # fmt: skip

REGION_NAMES: tuple[str, ...] = (
    "Europe", "European Union", "EU", "Middle East", "North Africa", "Balkans", "Scandinavia",
    "Gulf", "Asia", "North America",
)  # fmt: skip


def normalize_country(value: str | None) -> str | None:
    """Return an ISO-3166 alpha-2 code, or None if it cannot be resolved confidently."""
    if not value or not value.strip():
        return None
    v = value.strip()
    if len(v) == 2 and v.upper() in COUNTRY_NAMES:
        return v.upper()
    lowered = v.lower()
    if lowered in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[lowered]
    for code, name in COUNTRY_NAMES.items():
        if name.lower() == lowered:
            return code
    return None
