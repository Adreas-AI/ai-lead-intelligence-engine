"""Generate the SYNTHETIC demo dataset.

Every company here is fictional. Websites use the reserved ``.example`` TLD
(RFC 2606), so they cannot collide with real businesses and are served from
local HTML fixtures by the enrichment engine.

Outputs (committed to the repo, regenerate with ``python scripts/generate_demo_data.py``):
  data/demo/<profile>.csv             - CSV imports per profile, incl. duplicates and invalid rows
  data/demo/mock_discovery.json       - results returned by the "mock" discovery provider
  data/demo/websites/*.html           - synthetic website fixtures
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "demo"
SITES = DEMO / "websites"
BANNER = "SYNTHETIC DEMO DATA - fictional company for demonstration purposes only."

# domain -> page spec. "about" adds an /about page; "jsonld" adds schema.org Organization.
SITE_SPECS: dict[str, dict] = {
    "aegean-citrus.example": {
        "name": "Aegean Citrus Growers Cooperative",
        "description": "Cooperative of citrus growers in Argolida supplying oranges, mandarins "
        "and lemons to wholesalers and exporters across Europe.",
        "body": "Our 140 member farms grow navel oranges, Valencia oranges, mandarins and lemons. "
        "We pack in our own packhouse and ship by pallet and container to importers in "
        "Germany, the Netherlands and Poland. GLOBALG.A.P certified. Trade enquiries welcome.",
        "about": "Founded by local growers, the cooperative handles grading, packing and export "
        "logistics. We supply wholesale buyers and retail chains with bulk citrus.",
        "jsonld": ("Greece", "Argos"),
    },
    "kalamata-valley-olive.example": {
        "name": "Kalamata Valley Olive Oil S.A.",
        "description": "Producer and exporter of extra virgin olive oil and Kalamata table olives.",
        "body": "We produce extra virgin olive oil from Koroneiki olives and Kalamata olives. "
        "Bulk olive oil in IBC containers, private label bottling for distributors and "
        "export to Europe and the Middle East. Minimum order quantity applies.",
        "about": "Family-run olive mill and bottling plant in Messinia supplying B2B customers.",
        "jsonld": ("Greece", "Kalamata"),
    },
    "thessaly-grain.example": {
        "name": "Thessaly Grain Traders",
        "description": "Trading company for durum wheat, barley and maize from Central Greece.",
        "body": "We buy grain from farmers and cooperatives and sell durum wheat, soft wheat, "
        "barley and maize in bulk to mills, feed producers and importers. Truckload and "
        "container shipments, export documentation handled in-house.",
        "jsonld": ("Greece", "Larissa"),
    },
    "andalucia-fresh.example": {
        "name": "Andalucía Fresh Produce S.L.",
        "description": "Wholesaler of greenhouse vegetables from Almería.",
        "body": "Wholesale supply of tomatoes, peppers, cucumbers and zucchini from Almería "
        "greenhouses. We serve distributors, HORECA suppliers and supermarket buyers "
        "and export to France and Germany. Daily pallet shipments.",
        "about": "Our team works directly with growers in Almería and Granada.",
        "jsonld": ("Spain", "Almería"),
    },
    "levante-citrus.example": {
        "name": "Levante Citrus Export S.L.",
        "description": "Exporter of Valencian oranges and mandarins.",
        "body": "Export of oranges, mandarins and lemons from Valencia to importers in Europe "
        "and Scandinavia. Packing house with BRC certification.",
        "jsonld": ("Spain", "Valencia"),
    },
    "sicilia-nuts.example": {
        "name": "Sicilia Pistachio & Nut Co. S.r.l.",
        "description": "Grower and packer of Sicilian pistachios and almonds.",
        "body": "We grow and pack pistachios and almonds in Sicily and supply shelled and in-shell "
        "nuts in bulk to confectioners, wholesalers and exporters. Private label available.",
        "jsonld": ("Italy", "Bronte"),
    },
    "po-valley-rice.example": {
        "name": "Po Valley Rice Mills S.p.A.",
        "description": "Rice mill producing Arborio and Carnaroli rice and rice flour.",
        "body": "Our mill processes Arborio, Carnaroli and long grain rice and produces rice flour "
        "for food manufacturers. Bulk bags and private label packaging for distributors.",
        "jsonld": ("Italy", "Vercelli"),
    },
    "rotterdam-fresh-import.example": {
        "name": "Rotterdam Fresh Import B.V.",
        "description": "Importer and distributor of fresh fruit for the Benelux market.",
        "body": "We import avocados, citrus, table grapes and pomegranates from the Mediterranean "
        "and distribute to wholesalers and retail chains in the Netherlands and Belgium. "
        "Looking for new growers and exporters to source from.",
        "about": "Import and distribution company located near the port of Rotterdam.",
        "jsonld": ("Netherlands", "Rotterdam"),
    },
    "berlin-organic-wholesale.example": {
        "name": "Berlin Organic Wholesale GmbH",
        "description": "B2B wholesaler of organic dry goods for retailers and foodservice.",
        "body": "Wholesale of organic lentils, chickpeas, beans, almonds, walnuts and dried fruit. "
        "We supply organic shops, HORECA and foodservice businesses across Germany. "
        "Organic certified. Trade customers only.",
        "jsonld": ("Germany", "Berlin"),
    },
    "danube-honey.example": {
        "name": "Danube Honey Producers",
        "description": "Association of beekeepers producing acacia and sunflower honey.",
        "body": "Our cooperative of beekeepers produces acacia honey, linden honey and bee pollen. "
        "We sell honey in bulk drums to packers and export to Germany and Austria.",
        "jsonld": ("Bulgaria", "Ruse"),
    },
    "carpathian-berries.example": {
        "name": "Carpathian Berry Farms SRL",
        "description": "Grower of strawberries, raspberries and blueberries; fresh and frozen fruit.",
        "body": "We grow strawberries, raspberries and blueberries and supply fresh berries and "
        "frozen fruit to processors, wholesalers and export customers.",
        "jsonld": ("Romania", "Brasov"),
    },
    "lisbon-foodservice.example": {
        "name": "Lisbon Foodservice Distribuição Lda",
        "description": "Foodservice distributor for restaurants, hotels and catering in Portugal.",
        "body": "Distribution of fresh vegetables, potatoes, olive oil and rice to HORECA customers "
        "in Lisbon and the Algarve. Our distribution network covers 1,200 customers.",
        "jsonld": ("Portugal", "Lisbon"),
    },
    "cyprus-dairy.example": {
        "name": "Cyprus Halloumi & Dairy Ltd",
        "description": "Producer of halloumi cheese and yoghurt.",
        "body": "We produce halloumi cheese, yoghurt and milk products in Nicosia and export cheese "
        "to the UK and Germany.",
        "jsonld": ("Cyprus", "Nicosia"),
    },
    "marmara-dried-fruit.example": {
        "name": "Marmara Dried Fruit Exporters",
        "description": "Exporter of dried figs, dried apricots and hazelnuts.",
        "body": "Export of dried figs, dried apricots, raisins and hazelnuts in bulk cartons to "
        "importers in Europe. HACCP and BRC certified packing plant.",
        "jsonld": ("Turkey", "Izmir"),
    },
    "nile-delta-agro.example": {
        "name": "Nile Delta Agro Exports",
        "description": "Exporter of oranges, potatoes and onions.",
        "body": "We export oranges, potatoes and onions from the Nile Delta to importers in Europe "
        "and the Gulf. Containers shipped weekly in season.",
        "jsonld": ("Egypt", "Alexandria"),
    },
    "atlas-spice.example": {
        "name": "Atlas Spice Trading",
        "description": "Trader of Moroccan herbs and spices.",
        "body": "Trading of saffron, paprika, cumin, thyme and rosemary. We supply spice packers "
        "and wholesalers.",
        "jsonld": ("Morocco", "Marrakesh"),
    },
    "corner-deli.example": {
        "name": "Corner Deli Athens",
        "description": "Neighbourhood delicatessen with an online shop.",
        "body": "Shop now for our selection of cheeses, olive oil and wines. Add to cart and enjoy "
        "free shipping on orders over 50 euros within Athens.",
        "jsonld": ("Greece", "Athens"),
    },
    "taverna-thalassa.example": {
        "name": "Taverna Thalassa",
        "description": "Seaside restaurant serving traditional Greek food.",
        "body": "Book a table at our seaside taverna. See our menu of fresh fish and meze.",
        "jsonld": ("Greece", "Nafplio"),
    },
    "agricloud.example": {
        "name": "AgriCloud Analytics",
        "description": "Software platform for farm management.",
        "body": "SaaS for farm management: field mapping, irrigation scheduling and analytics "
        "dashboards for agronomists. Request a software demo.",
        "jsonld": ("Greece", "Thessaloniki"),
    },
    "golden-state-almonds.example": {
        "name": "Golden State Almond Growers",
        "description": "Almond grower in California.",
        "body": "We grow and export almonds and walnuts from the Central Valley.",
        "jsonld": ("United States", "Fresno"),
    },
    # The listed company is "Olympus Agro Supplies", but the site belongs to someone
    # else: the engine must NOT verify it as the official website.
    "olympus-agro.example": {
        "name": "Hermes Garden Centre",
        "description": "Garden centre selling plants and garden tools to the public.",
        "body": "Visit our garden centre for plants, seedlings and garden tools. Shop now online.",
    },
    # --- returned by the mock discovery provider ---
    "crete-avocado.example": {
        "name": "Crete Avocado Growers",
        "description": "Growers of Hass avocados in Chania, Crete.",
        "body": "Hass and Fuerte avocados grown in Chania. We supply wholesalers and export "
        "to Europe by pallet.",
        "jsonld": ("Greece", "Chania"),
    },
    "puglia-durum.example": {
        "name": "Puglia Durum Wheat Consortium",
        "description": "Consortium of durum wheat producers in Puglia.",
        "body": "Durum wheat for pasta makers, sold in bulk to mills. Cooperative of farmers.",
        "jsonld": ("Italy", "Foggia"),
    },
    "murcia-lettuce.example": {
        "name": "Murcia Lettuce Exporters S.L.",
        "description": "Exporter of lettuce and leafy greens.",
        "body": "Export of lettuce, leafy greens and broccoli to distributors in Europe.",
        "jsonld": ("Spain", "Murcia"),
    },
    "mazovia-apples.example": {
        "name": "Mazovia Apple Union",
        "description": "Union of apple growers near Grójec.",
        "body": "Apples and pears from our growers, packed for wholesale and export.",
        "jsonld": ("Poland", "Grójec"),
    },
    "szeged-paprika.example": {
        "name": "Szeged Paprika Traders Kft.",
        "description": "Trader of Hungarian paprika and spices.",
        "body": "Trading of paprika and spices in bulk for food manufacturers and wholesalers.",
        "jsonld": ("Hungary", "Szeged"),
    },
    "lyon-fresh.example": {
        "name": "Lyon Fresh Distribution SAS",
        "description": "Distributor of fruit and vegetables for foodservice.",
        "body": "Distribution of fresh fruit and vegetables to foodservice and HORECA customers.",
        "jsonld": ("France", "Lyon"),
    },
}

# --- Restaurants & hair salons (profiles/restaurants.yaml, profiles/hair_salons.yaml) ---
SITE_SPECS.update(
    {
        "limani-ouzeri.example": {
            "name": "To Limani Ouzeri",
            "type": "Restaurant",
            "description": "Family-run seafood taverna by the port of Piraeus since 1978.",
            "body": "Family-run ouzeri serving fresh fish, octopus and traditional Greek meze. "
            "Book a table online or call us. We host private events and group bookings. "
            "Opening hours: open daily from noon. See our menu.",
            "jsonld": ("Greece", "Piraeus"),
        },
        "nonna-elena.example": {
            "name": "Trattoria Nonna Elena",
            "type": "Restaurant",
            "description": "Italian trattoria in Thessaloniki: fresh pasta and wood-fired pizza.",
            "body": "Our family trattoria makes fresh pasta, risotto and pizza every day. "
            "Reserve a table or order online for delivery and takeaway.",
            "jsonld": ("Greece", "Thessaloniki"),
        },
        "kafeneio-plateia.example": {
            "name": "Kafeneio Plateia",
            "type": "CafeOrCoffeeShop",
            "description": "Neighbourhood cafe in Athens serving coffee and brunch.",
            "body": "Specialty coffee, espresso, brunch and homemade pastries. Opening hours: "
            "open daily from morning to evening. Find our menu on this page.",
            "jsonld": ("Greece", "Athens"),
        },
        "burgerhub.example": {
            "name": "BurgerHub",
            "type": "Restaurant",
            "description": "Burger franchise with stores across Greece.",
            "body": "Fast food burgers. Franchise opportunities available - see our store locator. "
            "Order online for delivery.",
            "jsonld": ("Greece", "Athens"),
        },
        "palia-poli.example": {
            "name": "Taverna Palia Poli",
            "type": "Restaurant",
            "description": "Taverna in Ano Poli, Thessaloniki.",
            "body": "Thank you for all these years together. The taverna is now permanently closed.",
            "jsonld": ("Greece", "Thessaloniki"),
        },
        "menusoft.example": {
            "name": "MenuSoft",
            "description": "SaaS platform for restaurants: digital menus and POS.",
            "body": "Software platform for restaurants. Request a demo of our SaaS.",
            "jsonld": ("Greece", "Athens"),
        },
        "lumiere-hair.example": {
            "name": "Lumière Hair Studio",
            "type": "HairSalon",
            "description": "Independent hair salon in Kolonaki, Athens.",
            "body": "Our team specialises in balayage, highlights and keratin treatments, as well "
            "as haircuts and styling. Book online at any time. Gift vouchers available.",
            "jsonld": ("Greece", "Athens"),
        },
        "kerasi-barber.example": {
            "name": "Kerasi Barber Co.",
            "type": "HairSalon",
            "description": "Barbershop in the centre of Thessaloniki.",
            "body": "Classic barbershop: haircuts, fade, beard trim and hot-towel shave. "
            "Book now - online booking for all our barbers.",
            "jsonld": ("Greece", "Thessaloniki"),
        },
        "bella-nails.example": {
            "name": "Bella Nails & Beauty",
            "type": "NailSalon",
            "description": "Nail salon offering manicure and pedicure.",
            "body": "Manicure, pedicure, gel nails and waxing. Call us for an appointment.",
            "jsonld": ("Greece", "Athens"),
        },
        "salonica-academy.example": {
            "name": "Salonica Hair Academy",
            "description": "Hairdressing school with professional training courses.",
            "body": "Training courses and seminars for hairdressers: cutting, colour and styling.",
            "jsonld": ("Greece", "Thessaloniki"),
        },
        "cosmo-supplies.example": {
            "name": "Cosmo Beauty Supplies",
            "type": "Store",
            "description": "Online shop for professional hair products.",
            "body": "Hair products and salon equipment at wholesale prices. Add to cart, "
            "free shipping on orders over a minimum amount.",
            "jsonld": ("Greece", "Athens"),
        },
        "studio-nine.example": {
            "name": "Studio Nine Hair",
            "type": "HairSalon",
            "description": "Hair studio in Patras: colour, cuts and bridal hair.",
            "body": "Our salon offers haircuts, colour, ombre and bridal hair for weddings. "
            "Book an appointment online.",
            "jsonld": ("Greece", "Patras"),
        },
    }
)

RESTAURANT_ROWS = [
    (
        "To Limani Ouzeri",
        "https://limani-ouzeri.example",
        "GR",
        "Piraeus",
        "Taverna",
        "Family-run",
        "",
        "seafood",
    ),
    (
        "Trattoria Nonna Elena",
        "https://nonna-elena.example",
        "GR",
        "Thessaloniki",
        "Restaurant",
        "",
        "",
        "italian",
    ),
    (
        "Kafeneio Plateia",
        "https://kafeneio-plateia.example",
        "GR",
        "Athens",
        "Cafe",
        "Independent",
        "",
        "",
    ),
    ("BurgerHub", "https://burgerhub.example", "GR", "Athens", "Fast food", "Franchise", "", ""),
    (
        "Taverna Palia Poli",
        "https://palia-poli.example",
        "GR",
        "Thessaloniki",
        "Taverna",
        "",
        "",
        "",
    ),
    ("MenuSoft", "https://menusoft.example", "GR", "Athens", "Software", "Software vendor", "", ""),
    ("Psistaria O Nikos", "", "GR", "Athens", "Grill", "", "Neighbourhood grill house.", ""),
    # duplicates (same domain; same name in another format)
    ("To Limani Ouzeri", "https://www.limani-ouzeri.example/", "GR", "", "", "", "", ""),
    ("TRATTORIA NONNA ELENA", "", "GR", "", "", "", "", ""),
]

SALON_ROWS = [
    (
        "Lumière Hair Studio",
        "https://lumiere-hair.example",
        "GR",
        "Athens",
        "Hair salon",
        "",
        "",
        "",
    ),
    (
        "Kerasi Barber Co.",
        "https://kerasi-barber.example",
        "GR",
        "Thessaloniki",
        "Barbershop",
        "",
        "",
        "",
    ),
    (
        "Bella Nails & Beauty",
        "https://bella-nails.example",
        "GR",
        "Athens",
        "Nail salon",
        "",
        "",
        "",
    ),
    (
        "Salonica Hair Academy",
        "https://salonica-academy.example",
        "GR",
        "Thessaloniki",
        "Hairdressing school",
        "Academy",
        "",
        "",
    ),
    (
        "Cosmo Beauty Supplies",
        "https://cosmo-supplies.example",
        "GR",
        "Athens",
        "Cosmetics store",
        "Supplier",
        "",
        "",
    ),
    (
        "Studio Nine Hair",
        "https://studio-nine.example",
        "GR",
        "Patras",
        "Hair studio",
        "Independent",
        "",
        "",
    ),
    # duplicates (accent-insensitive name; near-identical name)
    ("Lumiere Hair Studio", "", "GR", "", "", "", "", ""),
    ("Kerasi Barber Co", "", "GR", "", "", "", "", ""),
]

CSV_ROWS = [
    # name, website, country, city, industry, company_type, description, keywords
    (
        "Aegean Citrus Growers Cooperative",
        "https://aegean-citrus.example",
        "GR",
        "Argos",
        "Agriculture",
        "Cooperative",
        "",
        "citrus;export",
    ),
    (
        "Kalamata Valley Olive Oil S.A.",
        "https://kalamata-valley-olive.example",
        "GR",
        "Kalamata",
        "Food manufacturing",
        "Exporter",
        "",
        "olive oil",
    ),
    (
        "Thessaly Grain Traders",
        "thessaly-grain.example",
        "GR",
        "Larissa",
        "Import/Export",
        "Trader",
        "",
        "grains",
    ),
    (
        "Andalucía Fresh Produce S.L.",
        "https://andalucia-fresh.example",
        "ES",
        "Almería",
        "Food wholesale",
        "Wholesaler",
        "",
        "vegetables",
    ),
    (
        "Levante Citrus Export S.L.",
        "https://levante-citrus.example",
        "Spain",
        "Valencia",
        "Import export",
        "Exporter",
        "",
        "",
    ),
    (
        "Sicilia Pistachio & Nut Co. S.r.l.",
        "https://sicilia-nuts.example",
        "IT",
        "Bronte",
        "Agriculture",
        "Grower",
        "",
        "nuts",
    ),
    (
        "Po Valley Rice Mills S.p.A.",
        "https://po-valley-rice.example",
        "IT",
        "Vercelli",
        "Food processing",
        "Manufacturer",
        "",
        "rice",
    ),
    (
        "Rotterdam Fresh Import B.V.",
        "https://rotterdam-fresh-import.example",
        "NL",
        "Rotterdam",
        "Import/Export",
        "Importer",
        "",
        "fruit",
    ),
    (
        "Berlin Organic Wholesale GmbH",
        "https://berlin-organic-wholesale.example",
        "DE",
        "Berlin",
        "Food wholesale",
        "Wholesaler",
        "",
        "organic",
    ),
    (
        "Danube Honey Producers",
        "https://danube-honey.example",
        "BG",
        "Ruse",
        "Agriculture",
        "Cooperative",
        "",
        "honey",
    ),
    (
        "Carpathian Berry Farms SRL",
        "https://carpathian-berries.example",
        "RO",
        "Brasov",
        "Agriculture",
        "Grower",
        "",
        "berries",
    ),
    (
        "Lisbon Foodservice Distribuição Lda",
        "https://lisbon-foodservice.example",
        "PT",
        "Lisbon",
        "Food distribution",
        "Distributor",
        "",
        "horeca",
    ),
    (
        "Cyprus Halloumi & Dairy Ltd",
        "https://cyprus-dairy.example",
        "CY",
        "Nicosia",
        "Dairy",
        "Manufacturer",
        "",
        "",
    ),
    (
        "Marmara Dried Fruit Exporters",
        "https://marmara-dried-fruit.example",
        "TR",
        "Izmir",
        "Import/Export",
        "Exporter",
        "",
        "dried fruit",
    ),
    (
        "Nile Delta Agro Exports",
        "https://nile-delta-agro.example",
        "EG",
        "Alexandria",
        "Agriculture",
        "Exporter",
        "",
        "",
    ),
    (
        "Atlas Spice Trading",
        "https://atlas-spice.example",
        "MA",
        "Marrakesh",
        "Trading",
        "Trader",
        "",
        "spices",
    ),
    (
        "Corner Deli Athens",
        "https://corner-deli.example",
        "GR",
        "Athens",
        "Food retail",
        "Retailer",
        "",
        "",
    ),
    (
        "Taverna Thalassa",
        "https://taverna-thalassa.example",
        "GR",
        "Nafplio",
        "Hospitality",
        "Restaurant",
        "",
        "",
    ),
    (
        "AgriCloud Analytics",
        "https://agricloud.example",
        "GR",
        "Thessaloniki",
        "Software",
        "Service provider",
        "",
        "saas",
    ),
    (
        "Golden State Almond Growers",
        "https://golden-state-almonds.example",
        "US",
        "Fresno",
        "Agriculture",
        "Grower",
        "",
        "almonds",
    ),
    ("Olympus Agro Supplies", "https://olympus-agro.example", "GR", "Katerini", "", "", "", ""),
    # Website exists in the CSV but the site is unreachable (no fixture) -> enrichment fails.
    (
        "Macedonia Fresh Logistics",
        "https://macedonia-fresh.example",
        "GR",
        "Thessaloniki",
        "Food distribution",
        "Distributor",
        "",
        "",
    ),
    # No website: only the source description is available.
    (
        "Peloponnese Fig Farm",
        "",
        "GR",
        "Kalamata",
        "Agriculture",
        "Grower",
        "Small family farm growing figs; sells dried figs to local packers.",
        "figs",
    ),
    ("Unknown Trading Co", "", "", "", "", "", "", ""),
    # --- duplicates (should be detected and merged, not inserted) ---
    (
        "Aegean Citrus Growers Co-operative",
        "https://www.aegean-citrus.example/",
        "GR",
        "",
        "",
        "",
        "",
        "",
    ),  # same domain
    ("KALAMATA VALLEY OLIVE OIL SA", "", "GR", "", "", "", "", ""),  # same legal name + country
    ("Thessaly Grain Trader", "", "GR", "", "", "", "", ""),  # fuzzy name match
    # --- invalid rows (reported, not inserted) ---
    ("X", "https://tiny.example", "GR", "", "", "", "", ""),  # name too short
    ("Atlantis Olive Imports", "https://atlantis.example", "Atlantis", "", "", "", "", ""),
]

MOCK_DISCOVERY = [
    (
        "Crete Avocado Growers",
        "https://crete-avocado.example",
        "GR",
        "Chania",
        "Agriculture",
        "Grower",
        "Avocado grower on Crete.",
        ["avocados"],
    ),
    (
        "Puglia Durum Wheat Consortium",
        "https://puglia-durum.example",
        "IT",
        "Foggia",
        "Agriculture",
        "Cooperative",
        "Durum wheat producer consortium.",
        ["wheat"],
    ),
    (
        "Murcia Lettuce Exporters S.L.",
        "https://murcia-lettuce.example",
        "ES",
        "Murcia",
        "Import/Export",
        "Exporter",
        "Leafy greens exporter.",
        ["lettuce"],
    ),
    (
        "Mazovia Apple Union",
        "https://mazovia-apples.example",
        "PL",
        "Grójec",
        "Agriculture",
        "Cooperative",
        "Apple growers' union.",
        ["apples"],
    ),
    (
        "Szeged Paprika Traders Kft.",
        "https://szeged-paprika.example",
        "HU",
        "Szeged",
        "Trading",
        "Trader",
        "Paprika and spice trader.",
        ["paprika"],
    ),
    (
        "Lyon Fresh Distribution SAS",
        "https://lyon-fresh.example",
        "FR",
        "Lyon",
        "Food distribution",
        "Distributor",
        "Foodservice fruit & veg distributor.",
        ["foodservice"],
    ),
    # Duplicate of a CSV lead: discovery must not create it twice.
    (
        "Andalucia Fresh Produce SL",
        "https://andalucia-fresh.example",
        "ES",
        "Almería",
        "Food wholesale",
        "Wholesaler",
        "Almería vegetable wholesaler.",
        ["vegetables"],
    ),
]


def render_page(domain: str, spec: dict, *, about: bool = False) -> str:
    name = html.escape(spec["name"])
    jsonld = ""
    if spec.get("jsonld"):
        country, city = spec["jsonld"]
        data = {
            "@context": "https://schema.org",
            "@type": spec.get("type", "Organization"),
            "name": spec["name"],
            "url": f"https://{domain}",
            "description": spec["description"],
            "address": {
                "@type": "PostalAddress",
                "addressLocality": city,
                "addressCountry": country,
            },
        }
        jsonld = (
            f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'
        )
    nav_about = '<a href="/about">About us</a>' if spec.get("about") else ""
    content = spec["about"] if about else spec["body"]
    title = f"About - {name}" if about else f"{name} | {html.escape(spec['description'][:60])}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="description" content="{html.escape(spec["description"])}">
<meta property="og:site_name" content="{name}">
<meta name="robots" content="noindex">
{jsonld}
</head>
<body>
<!-- {BANNER} -->
<header><nav><a href="/">Home</a> {nav_about} <a href="/contact">Contact</a></nav></header>
<main>
<h1>{name}</h1>
<p>{html.escape(content)}</p>
</main>
<footer><p>{BANNER}</p></footer>
</body>
</html>
"""


def main() -> None:
    SITES.mkdir(parents=True, exist_ok=True)
    for old in SITES.glob("*.html"):
        old.unlink()
    for domain, spec in SITE_SPECS.items():
        (SITES / f"{domain}.html").write_text(render_page(domain, spec), encoding="utf-8")
        if spec.get("about"):
            (SITES / f"{domain}__about.html").write_text(
                render_page(domain, spec, about=True), encoding="utf-8"
            )

    header = [
        "name",
        "website",
        "country",
        "city",
        "industry",
        "company_type",
        "description",
        "keywords",
        "source_url",
        "is_synthetic",
    ]
    for old in DEMO.glob("*.csv"):
        old.unlink()
    datasets = {"agrifood_b2b": CSV_ROWS, "restaurants": RESTAURANT_ROWS, "hair_salons": SALON_ROWS}
    for profile, rows in datasets.items():
        with (DEMO / f"{profile}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            for row in rows:
                writer.writerow([*row, "https://example.org/synthetic-dataset", "true"])

    mock = {
        "notice": BANNER,
        "companies": [
            {
                "name": n,
                "website": w,
                "country": c,
                "city": city,
                "industry": i,
                "company_type": t,
                "description": d,
                "keywords": k,
                "source_url": "https://example.org/synthetic-discovery",
                "profile": "agrifood_b2b",
            }
            for n, w, c, city, i, t, d, k in MOCK_DISCOVERY
        ],
    }
    (DEMO / "mock_discovery.json").write_text(
        json.dumps(mock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {len(SITE_SPECS)} sites, {len(datasets)} CSV datasets, "
        f"{len(MOCK_DISCOVERY)} mock results"
    )


if __name__ == "__main__":
    main()
