"""Agri-food B2B taxonomy used by discovery, enrichment and scoring.

Everything here is plain data so the scoring model is transparent and easy to
tune in code review. Keyword matching is case-insensitive and word-bounded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

# --- Industries ----------------------------------------------------------------
# slug -> (relevance points out of 25, synonyms used to classify free text)
INDUSTRIES: dict[str, tuple[int, tuple[str, ...]]] = {
    "agriculture": (
        25,
        (
            "agriculture",
            "agricultural",
            "farming",
            "farm",
            "grower",
            "growers",
            "horticulture",
            "cultivation",
            "orchard",
            "agri",
            "agro",
        ),
    ),
    "food_wholesale": (
        25,
        ("food wholesale", "wholesale food", "wholesale", "wholesaler", "cash and carry"),
    ),
    "import_export": (
        25,
        (
            "import export",
            "import/export",
            "import-export",
            "trading",
            "commodity trading",
            "agri trading",
            "food trade",
            "exporter",
            "importer",
        ),
    ),
    "food_distribution": (
        23,
        (
            "food distribution",
            "distribution",
            "distributor",
            "foodservice",
            "food service",
            "horeca supply",
            "logistics food",
        ),
    ),
    "food_manufacturing": (
        18,
        (
            "food manufacturing",
            "food processing",
            "food production",
            "food industry",
            "packing",
            "packaging of food",
            "mill",
            "dairy",
            "bakery",
            "canning",
            "olive mill",
        ),
    ),
    "agri_inputs": (
        14,
        (
            "fertilizer",
            "fertiliser",
            "seeds",
            "agrochemical",
            "crop protection",
            "irrigation",
            "agricultural machinery",
            "agri inputs",
        ),
    ),
    "food_retail": (8, ("supermarket", "grocery", "food retail", "delicatessen", "retail")),
    "hospitality": (5, ("restaurant", "hotel", "catering", "hospitality", "cafe")),
    "other": (0, ()),
    "unknown": (0, ()),
}

# --- Company types ---------------------------------------------------------------
# slug -> (marketplace compatibility points out of 20, is_b2b_type, synonyms)
COMPANY_TYPES: dict[str, tuple[int, bool, tuple[str, ...]]] = {
    "wholesaler": (20, True, ("wholesaler", "wholesale", "cash and carry")),
    "exporter": (20, True, ("exporter", "export", "exports")),
    "importer": (20, True, ("importer", "import", "imports")),
    "trader": (18, True, ("trader", "trading", "commodity", "broker")),
    "distributor": (18, True, ("distributor", "distribution", "foodservice")),
    "cooperative": (18, True, ("cooperative", "co-op", "coop", "union of producers")),
    "grower": (16, True, ("grower", "producer", "farm", "farmer", "estate", "orchard")),
    "packer": (16, True, ("packer", "packhouse", "packing house", "packing")),
    "food_manufacturer": (14, True, ("manufacturer", "processor", "processing", "mill", "factory")),
    "agri_input_supplier": (10, True, ("fertilizer", "seed", "agrochemical", "machinery")),
    "retailer": (6, False, ("retailer", "shop", "store", "supermarket", "grocery")),
    "restaurant": (4, False, ("restaurant", "taverna", "cafe", "catering")),
    "service_provider": (2, False, ("agency", "software", "consulting", "consultancy", "saas")),
    "unknown": (0, False, ()),
}


# --- Product categories ------------------------------------------------------------
@dataclass(frozen=True)
class ProductCategory:
    slug: str
    label: str
    core: bool  # core marketplace category vs. adjacent
    keywords: tuple[str, ...]


PRODUCT_CATEGORIES: tuple[ProductCategory, ...] = (
    ProductCategory(
        "fresh_fruit",
        "Fresh fruit",
        True,
        (
            "fresh fruit",
            "citrus",
            "oranges",
            "lemons",
            "mandarins",
            "apples",
            "pears",
            "peaches",
            "nectarines",
            "apricots",
            "cherries",
            "grapes",
            "table grapes",
            "kiwi",
            "kiwifruit",
            "watermelon",
            "melons",
            "strawberries",
            "berries",
            "pomegranates",
            "figs",
            "avocados",
            "bananas",
        ),
    ),
    ProductCategory(
        "fresh_vegetables",
        "Fresh vegetables",
        True,
        (
            "vegetables",
            "fresh vegetables",
            "tomatoes",
            "cucumbers",
            "peppers",
            "potatoes",
            "onions",
            "garlic",
            "lettuce",
            "leafy greens",
            "zucchini",
            "courgettes",
            "aubergines",
            "eggplants",
            "carrots",
            "cabbage",
            "broccoli",
            "asparagus",
        ),
    ),
    ProductCategory(
        "olive_oil_olives",
        "Olive oil & olives",
        True,
        ("olive oil", "extra virgin olive oil", "evoo", "olives", "table olives", "kalamata"),
    ),
    ProductCategory(
        "grains_cereals",
        "Grains & cereals",
        True,
        (
            "grains",
            "cereals",
            "wheat",
            "durum wheat",
            "barley",
            "maize",
            "corn",
            "rice",
            "oats",
            "rye",
            "flour",
            "sunflower seeds",
        ),
    ),
    ProductCategory(
        "legumes_pulses",
        "Legumes & pulses",
        True,
        ("legumes", "pulses", "lentils", "chickpeas", "beans", "fava", "split peas"),
    ),
    ProductCategory(
        "nuts_dried_fruit",
        "Nuts & dried fruit",
        True,
        (
            "nuts",
            "almonds",
            "walnuts",
            "pistachios",
            "hazelnuts",
            "chestnuts",
            "cashews",
            "dried fruit",
            "raisins",
            "currants",
            "dried figs",
            "dates",
            "prunes",
        ),
    ),
    ProductCategory(
        "herbs_spices",
        "Herbs & spices",
        True,
        (
            "herbs",
            "spices",
            "oregano",
            "thyme",
            "saffron",
            "mountain tea",
            "sage",
            "rosemary",
            "paprika",
            "aromatic plants",
        ),
    ),
    ProductCategory(
        "honey_bee_products",
        "Honey & bee products",
        True,
        ("honey", "thyme honey", "pine honey", "bee pollen", "royal jelly", "beeswax"),
    ),
    ProductCategory(
        "dairy_eggs",
        "Dairy & eggs",
        False,
        ("dairy", "cheese", "feta", "yogurt", "yoghurt", "milk", "eggs"),
    ),
    ProductCategory(
        "wine_beverages",
        "Wine & beverages",
        False,
        ("wine", "wines", "juice", "juices", "spirits", "beer"),
    ),
    ProductCategory(
        "processed_foods",
        "Processed & packaged foods",
        False,
        (
            "canned",
            "preserves",
            "jams",
            "sauces",
            "frozen vegetables",
            "frozen fruit",
            "packaged food",
            "snacks",
            "pasta",
            "tahini",
            "halva",
        ),
    ),
    ProductCategory(
        "meat_seafood",
        "Meat & seafood",
        False,
        ("meat", "poultry", "beef", "lamb", "pork", "seafood", "fish", "aquaculture"),
    ),
    ProductCategory(
        "agri_inputs",
        "Agricultural inputs",
        False,
        (
            "fertilizers",
            "fertilisers",
            "seeds",
            "seedlings",
            "crop protection",
            "irrigation",
            "greenhouse equipment",
            "greenhouse films",
            "agricultural machinery",
            "tractors",
        ),
    ),
)

# --- Business-model signals ------------------------------------------------------------
B2B_SIGNALS: dict[str, tuple[str, ...]] = {
    "wholesale": ("wholesale", "wholesaler", "wholesale prices"),
    "bulk_supply": (
        "bulk",
        "in bulk",
        "bulk orders",
        "pallet",
        "pallets",
        "container",
        "containers",
        "tonnes",
        "tons",
        "truckload",
    ),
    "export": (
        "export",
        "exports",
        "exporting",
        "exporter",
        "exporters",
        "worldwide shipping to businesses",
    ),
    "import": ("import", "imports", "importing", "importer", "importers"),
    "distribution": ("distributor", "distributors", "distribution network", "we distribute"),
    "b2b_explicit": (
        "b2b",
        "business to business",
        "trade customers",
        "trade enquiries",
        "trade inquiries",
        "professional buyers",
        "for businesses",
    ),
    "horeca": ("horeca", "foodservice", "food service", "catering suppliers"),
    "private_label": ("private label", "white label", "own label"),
    "moq": ("minimum order", "moq", "minimum order quantity"),
    "certifications": (
        "globalgap",
        "global g.a.p",
        "brc",
        "ifs",
        "iso 22000",
        "haccp",
        "organic certified",
        "fssc 22000",
    ),
}

B2C_SIGNALS: tuple[str, ...] = (
    "add to cart",
    "add to basket",
    "shop now",
    "free shipping on orders",
    "checkout",
    "my cart",
    "book a table",
    "reserve a table",
    "our menu",
)

# --- Countries ---------------------------------------------------------------------
COUNTRY_NAMES: dict[str, str] = {
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "HR": "Croatia",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DK": "Denmark",
    "EE": "Estonia",
    "FI": "Finland",
    "FR": "France",
    "DE": "Germany",
    "GR": "Greece",
    "HU": "Hungary",
    "IE": "Ireland",
    "IT": "Italy",
    "LV": "Latvia",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MT": "Malta",
    "NL": "Netherlands",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SK": "Slovakia",
    "SI": "Slovenia",
    "ES": "Spain",
    "SE": "Sweden",
    "GB": "United Kingdom",
    "CH": "Switzerland",
    "NO": "Norway",
    "RS": "Serbia",
    "MK": "North Macedonia",
    "AL": "Albania",
    "ME": "Montenegro",
    "TR": "Turkey",
    "UA": "Ukraine",
    "EG": "Egypt",
    "MA": "Morocco",
    "TN": "Tunisia",
    "IL": "Israel",
    "JO": "Jordan",
    "LB": "Lebanon",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "US": "United States",
    "CA": "Canada",
    "MX": "Mexico",
    "BR": "Brazil",
    "AR": "Argentina",
    "ZA": "South Africa",
    "IN": "India",
    "CN": "China",
    "JP": "Japan",
    "AU": "Australia",
}

COUNTRY_ALIASES: dict[str, str] = {
    "hellas": "GR",
    "greek": "GR",
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
    "holland": "NL",
    "the netherlands": "NL",
    "czech republic": "CZ",
    "usa": "US",
    "united states of america": "US",
    "türkiye": "TR",
    "turkiye": "TR",
    "uae": "AE",
    "deutschland": "DE",
    "españa": "ES",
    "espana": "ES",
    "italia": "IT",
}

REGION_NAMES: tuple[str, ...] = (
    "Europe",
    "European Union",
    "EU",
    "Middle East",
    "North Africa",
    "Balkans",
    "Scandinavia",
    "Gulf",
    "Asia",
    "North America",
)


@lru_cache(maxsize=2048)
def keyword_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(keyword.lower())}(?![\w-])")


def find_keywords(text: str, keywords: tuple[str, ...] | list[str]) -> list[str]:
    lowered = text.lower()
    return [kw for kw in keywords if keyword_pattern(kw).search(lowered)]


def classify_industry(text: str | None) -> str:
    """Map free-text industry descriptions to a canonical slug."""
    if not text or not text.strip():
        return "unknown"
    lowered = text.lower().replace("_", " ")
    for slug in INDUSTRIES:
        if lowered == slug.replace("_", " "):
            return slug
    best, best_len = "other", 0
    for slug, (_, synonyms) in INDUSTRIES.items():
        for syn in synonyms:
            # Prefer the most specific (longest) synonym that matches.
            if len(syn) > best_len and keyword_pattern(syn).search(lowered):
                best, best_len = slug, len(syn)
    return best


def classify_company_type(text: str | None) -> str:
    if not text or not text.strip():
        return "unknown"
    lowered = text.lower().replace("_", " ")
    for slug in COMPANY_TYPES:
        if lowered == slug.replace("_", " "):
            return slug
    best, best_len = "unknown", 0
    for slug, (_, _, synonyms) in COMPANY_TYPES.items():
        for syn in synonyms:
            if len(syn) > best_len and keyword_pattern(syn).search(lowered):
                best, best_len = slug, len(syn)
    return best


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
