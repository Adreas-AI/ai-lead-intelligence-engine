"""Built-in dictionary of business categories (English + Greek) -> OpenStreetMap tags.

Used to understand free-text searches such as "κρεοπωλεία" or "dental clinics" without
an LLM. Patterns are matched against the accent-folded, lower-cased query:

* ``word``    matches the whole word, with an optional English plural (-s / -es)
* ``stem*``   matches any word starting with ``stem`` (handles Greek inflection)
* multi-word patterns match as a phrase
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.domain.keywords import fold


@dataclass(frozen=True)
class BusinessCategory:
    key: str
    label_en: str
    label_el: str
    patterns: tuple[str, ...]
    osm_tags: tuple[str, ...]


def _c(key: str, en: str, el: str, patterns: str, tags: str) -> BusinessCategory:
    return BusinessCategory(
        key, en, el, tuple(p.strip() for p in patterns.split("|")), tuple(tags.split())
    )


CATALOG: tuple[BusinessCategory, ...] = (
    # --- food & drink retail
    _c("butcher", "Butcher shops", "Κρεοπωλεία", "butcher|butchery|meat shop|κρεοπωλ*|κρεατοπωλ*|χασαπ*", "shop=butcher"),
    _c("bakery", "Bakeries", "Αρτοποιεία / φούρνοι", "bakery|bakeries|φουρν*|αρτοποι*|αρτοπωλ*", "shop=bakery"),
    _c("pastry", "Pastry shops", "Ζαχαροπλαστεία", "patisserie|pastry shop|confectionery|ζαχαροπλαστ*", "shop=pastry shop=confectionery"),
    _c("greengrocer", "Greengrocers", "Μανάβικα / οπωροπωλεία", "greengrocer|fruit shop|οπωροπωλ*|μαναβ*|οπωροκηπευτικ*", "shop=greengrocer"),
    _c("fishmonger", "Fishmongers", "Ιχθυοπωλεία", "fishmonger|fish shop|ιχθυοπωλ*|ψαραδικ*", "shop=seafood"),
    _c("supermarket", "Supermarkets & groceries", "Σούπερ μάρκετ / παντοπωλεία", "supermarket|grocery|groceries|grocery store|σουπερ μαρκετ|σουπερμαρκετ*|παντοπωλ*|μπακαλ*", "shop=supermarket shop=convenience"),
    _c("deli", "Delicatessens", "Ντελικατέσεν", "deli|delicatessen|ντελικατεσ*", "shop=deli"),
    _c("cheese_dairy", "Cheese & dairy shops", "Τυροκομικά / γαλακτοπωλεία", "cheese shop|dairy shop|τυροκομ*|γαλακτοπωλ*", "shop=cheese shop=dairy"),
    _c("wine_shop", "Wine & liquor shops", "Κάβες", "wine shop|liquor store|off licence|καβα|καβες|ποτοπωλ*", "shop=wine shop=alcohol"),
    _c("kiosk", "Kiosks", "Περίπτερα", "kiosk|περιπτερ*", "shop=kiosk"),
    # --- food service & hospitality
    _c("restaurant", "Restaurants", "Εστιατόρια / ταβέρνες", "restaurant|taverna|tavern|bistro|εστιατορ*|ταβερν*|ψησταρ*|μεζεδοπωλ*|ουζερ*", "amenity=restaurant"),
    _c("cafe", "Cafés", "Καφετέριες", "cafe|coffee shop|coffee house|καφετερ*|καφεν*|καφε", "amenity=cafe"),
    _c("bar", "Bars & pubs", "Μπαρ", "bar|pub|cocktail bar|wine bar|μπαρ", "amenity=bar amenity=pub"),
    _c("fast_food", "Fast food & takeaway", "Fast food / σουβλατζίδικα", "fast food|takeaway|souvlaki|gyros|pizzeria|pizza|σουβλατζ*|πιτσαρ*|ψητοπωλ*", "amenity=fast_food"),
    _c("hotel", "Hotels", "Ξενοδοχεία", "hotel|resort|ξενοδοχ*", "tourism=hotel"),
    _c("guest_house", "Guest houses & rooms", "Ενοικιαζόμενα δωμάτια", "guest house|guesthouse|rooms to let|bed and breakfast|ενοικιαζομεν*|πανσιον*", "tourism=guest_house tourism=apartment"),
    _c("camping", "Campsites", "Κάμπινγκ", "camping|campsite|καμπινγκ", "tourism=camp_site"),
    # --- beauty & personal care
    _c("hairdresser", "Hair salons", "Κομμωτήρια", "hair salon|hairdresser|hair studio|κομμωτ*|κομμωσ*", "shop=hairdresser"),
    _c("barber", "Barbershops", "Κουρεία", "barber|barbershop|barber shop|κουρει*", "shop=barber shop=hairdresser"),
    _c("beauty", "Beauty salons", "Κέντρα αισθητικής", "beauty salon|beauty centre|beauty center|aesthetics|αισθητικ*|ινστιτουτ* ομορφ*", "shop=beauty"),
    _c("nails", "Nail salons", "Νυχάκια / μανικιούρ", "nail salon|nail studio|manicure|μανικιουρ*|νυχακ*", "shop=beauty"),
    _c("cosmetics", "Cosmetics shops", "Καταστήματα καλλυντικών", "cosmetics|perfumery|καλλυντικ*|αρωματοπωλ*", "shop=cosmetics shop=perfumery"),
    _c("tattoo", "Tattoo studios", "Tattoo studios", "tattoo|τατουαζ", "shop=tattoo"),
    # --- health
    _c("pharmacy", "Pharmacies", "Φαρμακεία", "pharmacy|pharmacies|chemist|φαρμακει*", "amenity=pharmacy"),
    _c("dentist", "Dental clinics", "Οδοντιατρεία", "dentist|dental|dental clinic|οδοντιατρ*", "amenity=dentist healthcare=dentist"),
    _c("doctor", "Medical practices & clinics", "Ιατρεία / κλινικές", "doctor|medical practice|clinic|ιατρει*|ιατρ*|γιατρ*|κλινικ*|πολυιατρει*", "amenity=doctors amenity=clinic"),
    _c("physio", "Physiotherapy centres", "Φυσικοθεραπευτήρια", "physiotherapy|physiotherapist|physio|φυσικοθεραπ*", "healthcare=physiotherapist"),
    _c("optician", "Opticians", "Οπτικά", "optician|optical|eyewear|οπτικ*", "shop=optician"),
    _c("vet", "Veterinary clinics", "Κτηνιατρεία", "vet|veterinary|veterinarian|κτηνιατρ*", "amenity=veterinary"),
    _c("pet_shop", "Pet shops", "Pet shops", "pet shop|pet store|pet supplies|κατοικιδ*", "shop=pet"),
    # --- fitness & education
    _c("gym", "Gyms & fitness studios", "Γυμναστήρια", "gym|fitness|fitness centre|crossfit|pilates|yoga|γυμναστηρ*", "leisure=fitness_centre"),
    _c("dance_school", "Dance schools", "Σχολές χορού", "dance school|dance studio|σχολ* χορ*", "leisure=dance"),
    _c("tutoring", "Tutoring & language schools", "Φροντιστήρια / κέντρα ξένων γλωσσών", "tutoring|language school|φροντιστηρ*|ξενων γλωσσων|κεντρ* ξεν* γλωσσ*", "amenity=prep_school amenity=language_school"),
    _c("kindergarten", "Kindergartens & nurseries", "Παιδικοί σταθμοί", "kindergarten|nursery school|daycare|παιδικ* σταθμ*|νηπιαγωγ*", "amenity=kindergarten"),
    _c("driving_school", "Driving schools", "Σχολές οδηγών", "driving school|σχολ* οδηγ*", "amenity=driving_school"),
    # --- automotive
    _c("car_repair", "Car repair shops", "Συνεργεία αυτοκινήτων", "car repair|garage|mechanic|auto repair|συνεργει*", "shop=car_repair"),
    _c("car_dealer", "Car dealers", "Αντιπροσωπείες αυτοκινήτων", "car dealer|car dealership|used cars|αντιπροσωπει* αυτοκινητ*|εμπορι* αυτοκινητ*", "shop=car"),
    _c("car_wash", "Car washes", "Πλυντήρια αυτοκινήτων", "car wash|πλυντηρι* αυτοκινητ*", "amenity=car_wash"),
    _c("tyres", "Tyre shops", "Βουλκανιζατέρ / ελαστικά", "tyre|tire|tyres|tires|ελαστικ*|βουλκανιζατερ", "shop=tyres"),
    _c("car_rental", "Car rentals", "Ενοικιάσεις αυτοκινήτων", "car rental|car hire|rent a car|ενοικιασ* αυτοκινητ*", "amenity=car_rental"),
    _c("fuel", "Petrol stations", "Πρατήρια καυσίμων", "gas station|petrol station|fuel station|βενζιναδ*|πρατηρι* καυσιμ*", "amenity=fuel"),
    _c("bicycle", "Bicycle shops", "Καταστήματα ποδηλάτων", "bicycle shop|bike shop|ποδηλατ*", "shop=bicycle"),
    # --- retail
    _c("florist", "Florists", "Ανθοπωλεία", "florist|flower shop|ανθοπωλ*|λουλουδ*", "shop=florist"),
    _c("garden_centre", "Garden centres & nurseries", "Φυτώρια", "garden centre|garden center|plant nursery|φυτωρ*", "shop=garden_centre"),
    _c("hardware", "Hardware stores", "Σιδηροπωλεία / χρώματα", "hardware store|diy store|paint shop|σιδηροπωλ*|χρωματοπωλ*|ειδη κιγκαλεριας", "shop=hardware shop=doityourself shop=paint"),
    _c("furniture", "Furniture stores", "Καταστήματα επίπλων", "furniture|επιπλ*", "shop=furniture"),
    _c("electronics", "Electronics stores", "Ηλεκτρονικά / ηλεκτρικά είδη", "electronics|electronics store|appliances|ηλεκτρονικ*|ηλεκτρικ* ειδ*", "shop=electronics shop=appliance"),
    _c("mobile_phones", "Mobile phone shops", "Καταστήματα κινητών", "mobile phone shop|phone shop|κινητ* τηλεφων*|κινητα", "shop=mobile_phone"),
    _c("computers", "Computer shops", "Καταστήματα υπολογιστών", "computer shop|computer store|υπολογιστ*", "shop=computer"),
    _c("clothes", "Clothing stores", "Καταστήματα ρούχων", "clothing|clothes|fashion boutique|boutique|ρουχ*|ενδυματ*|μπουτικ*", "shop=clothes shop=boutique"),
    _c("shoes", "Shoe stores", "Υποδήματα", "shoe|shoes|shoe store|υποδηματ*|παπουτσ*", "shop=shoes"),
    _c("jewellery", "Jewellers", "Κοσμηματοπωλεία", "jeweller|jeweler|jewellery|jewelry|κοσμηματ*", "shop=jewelry"),
    _c("books", "Bookshops", "Βιβλιοπωλεία", "bookshop|bookstore|βιβλιοπωλ*", "shop=books"),
    _c("stationery", "Stationery shops", "Χαρτοπωλεία", "stationery|χαρτοπωλ*|χαρτικ*", "shop=stationery"),
    _c("toys", "Toy stores", "Καταστήματα παιχνιδιών", "toy store|toy shop|toys|παιχνιδ*", "shop=toys"),
    _c("sports", "Sports shops", "Αθλητικά είδη", "sports shop|sporting goods|αθλητικ* ειδ*", "shop=sports"),
    _c("tobacco", "Tobacco & vape shops", "Καπνοπωλεία", "tobacconist|vape shop|καπνοπωλ*", "shop=tobacco shop=e-cigarette"),
    # --- services
    _c("laundry", "Laundries & dry cleaners", "Καθαριστήρια / πλυντήρια ρούχων", "laundry|laundromat|dry cleaner|dry cleaning|καθαριστηρ*|πλυντηρι* ρουχ*", "shop=laundry shop=dry_cleaning"),
    _c("tailor", "Tailors & alterations", "Ραφεία", "tailor|alterations|ραφει*|μοδιστρ*", "craft=tailor shop=tailor"),
    _c("photo", "Photo studios", "Φωτογραφεία", "photo studio|photographer|φωτογραφ*", "shop=photo craft=photographer"),
    _c("printing", "Print & copy shops", "Εκτυπώσεις / τυπογραφεία", "print shop|copy shop|printing|εκτυπωσ*|τυπογραφ*|φωτοτυπ*", "shop=copyshop craft=printer"),
    _c("real_estate", "Real-estate agencies", "Μεσιτικά γραφεία", "real estate|estate agent|realtor|μεσιτ*|κτηματομεσιτ*", "office=estate_agent"),
    _c("accountant", "Accounting firms", "Λογιστικά γραφεία", "accountant|accountants|accounting firm|bookkeeping|λογιστ*", "office=accountant"),
    _c("lawyer", "Law firms", "Δικηγορικά γραφεία", "lawyer|law firm|attorney|solicitor|δικηγορ*", "office=lawyer"),
    _c("insurance", "Insurance agencies", "Ασφαλιστικά γραφεία", "insurance|insurance agent|ασφαλιστ*|ασφαλει*", "office=insurance"),
    _c("architect", "Architecture firms", "Αρχιτεκτονικά γραφεία", "architect|architects|architecture firm|αρχιτεκτ*", "office=architect"),
    _c("travel_agency", "Travel agencies", "Ταξιδιωτικά γραφεία", "travel agency|travel agent|tour operator|ταξιδιωτικ*|τουριστικ* γραφει*", "shop=travel_agency office=travel_agent"),
    _c("it_company", "IT & software companies", "Εταιρείες πληροφορικής", "software company|it company|web design|web agency|πληροφορικ*|λογισμικ*", "office=it office=company"),
    _c("marketing", "Marketing & advertising agencies", "Διαφημιστικές εταιρείες", "marketing agency|advertising agency|digital agency|διαφημιστ*|μαρκετινγκ", "office=advertising_agency"),
    _c("coworking", "Coworking spaces", "Coworking spaces", "coworking|co-working", "amenity=coworking_space"),
    _c("funeral", "Funeral homes", "Γραφεία τελετών", "funeral home|funeral director|γραφει* τελετ*", "shop=funeral_directors"),
    _c("logistics", "Logistics & courier companies", "Μεταφορικές / courier", "logistics|courier|freight|μεταφορικ*|κουριερ", "office=logistics office=courier"),
    # --- trades & production
    _c("electrician", "Electricians", "Ηλεκτρολόγοι", "electrician|ηλεκτρολογ*", "craft=electrician"),
    _c("plumber", "Plumbers", "Υδραυλικοί", "plumber|plumbing|υδραυλικ*", "craft=plumber"),
    _c("carpenter", "Carpenters & joiners", "Ξυλουργεία", "carpenter|joiner|joinery|ξυλουργ*", "craft=carpenter"),
    _c("locksmith", "Locksmiths", "Κλειδαράδες", "locksmith|κλειδαρ*", "craft=locksmith shop=locksmith"),
    _c("winery", "Wineries", "Οινοποιεία", "winery|wineries|wine estate|οινοποι*", "craft=winery"),
    _c("brewery", "Breweries", "Ζυθοποιεία", "brewery|breweries|craft beer|ζυθοποι*", "craft=brewery"),
    _c("wholesale", "Wholesalers", "Χονδρέμποροι", "wholesale|wholesaler|cash and carry|χονδρεμπορ*|χονδρικ*", "shop=wholesale"),
)  # fmt: skip


@lru_cache(maxsize=512)
def _pattern(p: str) -> re.Pattern[str]:
    parts = []
    for word in fold(p).split():
        if word.endswith("*"):
            parts.append(re.escape(word[:-1]) + r"\w*")
        else:
            parts.append(re.escape(word))
    body = r"\s+".join(parts)
    suffix = "" if p.endswith("*") else r"(?:s|es)?"
    return re.compile(rf"(?<!\w){body}{suffix}(?!\w)")


def match_categories(query: str) -> list[BusinessCategory]:
    """All catalog categories mentioned in ``query`` (most specific first)."""
    folded = fold(query)
    hits: list[tuple[int, BusinessCategory, list[tuple[int, int]]]] = []
    for cat in CATALOG:
        spans = [m.span() for p in cat.patterns for m in _pattern(p).finditer(folded)]
        if spans:
            hits.append((max(e - s for s, e in spans), cat, spans))
    hits.sort(key=lambda h: -h[0])
    # Drop a match whose text is entirely inside a more specific one
    # ("dental clinic" should not also yield generic "clinic").
    kept: list[tuple[int, BusinessCategory, list[tuple[int, int]]]] = []
    for length, cat, spans in hits:
        covered = all(
            any(
                ks <= s and e <= ke and (ke - ks) > (e - s)
                for _, _, kspans in kept
                for ks, ke in kspans
            )
            for s, e in spans
        )
        if not covered:
            kept.append((length, cat, spans))
    return [cat for _, cat, _ in kept]


def get_category(key: str) -> BusinessCategory | None:
    return next((c for c in CATALOG if c.key == key), None)
