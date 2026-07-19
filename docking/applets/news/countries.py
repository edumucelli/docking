"""Country display names and locale matching for the News source catalog."""

from __future__ import annotations

import locale

from docking.i18n import _

# The upstream catalog is keyed by ISO 3166-1 alpha-3 codes, plus GLOBAL and
# the commonly used user-assigned XKK code for Kosovo. Keep this mapping local
# so opening the source picker does not add a large country-library dependency.
_COUNTRIES: dict[str, tuple[str, str]] = {
    "AFG": ("AF", _("Afghanistan")),
    "ALB": ("AL", _("Albania")),
    "AND": ("AD", _("Andorra")),
    "ARG": ("AR", _("Argentina")),
    "ARM": ("AM", _("Armenia")),
    "AUS": ("AU", _("Australia")),
    "AUT": ("AT", _("Austria")),
    "AZE": ("AZ", _("Azerbaijan")),
    "BDI": ("BI", _("Burundi")),
    "BEL": ("BE", _("Belgium")),
    "BEN": ("BJ", _("Benin")),
    "BGD": ("BD", _("Bangladesh")),
    "BGR": ("BG", _("Bulgaria")),
    "BHS": ("BS", _("Bahamas")),
    "BIH": ("BA", _("Bosnia and Herzegovina")),
    "BLR": ("BY", _("Belarus")),
    "BLZ": ("BZ", _("Belize")),
    "BMU": ("BM", _("Bermuda")),
    "BOL": ("BO", _("Bolivia")),
    "BRA": ("BR", _("Brazil")),
    "BRB": ("BB", _("Barbados")),
    "CAN": ("CA", _("Canada")),
    "CHE": ("CH", _("Switzerland")),
    "CHL": ("CL", _("Chile")),
    "CHN": ("CN", _("China")),
    "CMR": ("CM", _("Cameroon")),
    "COD": ("CD", _("Democratic Republic of the Congo")),
    "COL": ("CO", _("Colombia")),
    "CRI": ("CR", _("Costa Rica")),
    "CUB": ("CU", _("Cuba")),
    "CYM": ("KY", _("Cayman Islands")),
    "CYP": ("CY", _("Cyprus")),
    "CZE": ("CZ", _("Czechia")),
    "DEU": ("DE", _("Germany")),
    "DJI": ("DJ", _("Djibouti")),
    "DMA": ("DM", _("Dominica")),
    "DNK": ("DK", _("Denmark")),
    "DOM": ("DO", _("Dominican Republic")),
    "DZA": ("DZ", _("Algeria")),
    "ECU": ("EC", _("Ecuador")),
    "EGY": ("EG", _("Egypt")),
    "ERI": ("ER", _("Eritrea")),
    "ESP": ("ES", _("Spain")),
    "EST": ("EE", _("Estonia")),
    "ETH": ("ET", _("Ethiopia")),
    "FIN": ("FI", _("Finland")),
    "FRA": ("FR", _("France")),
    "GAB": ("GA", _("Gabon")),
    "GBR": ("GB", _("United Kingdom")),
    "GHA": ("GH", _("Ghana")),
    "GIB": ("GI", _("Gibraltar")),
    "GIN": ("GN", _("Guinea")),
    "GRC": ("GR", _("Greece")),
    "GTM": ("GT", _("Guatemala")),
    "GUF": ("GF", _("French Guiana")),
    "GUY": ("GY", _("Guyana")),
    "HKG": ("HK", _("Hong Kong")),
    "HRV": ("HR", _("Croatia")),
    "HTI": ("HT", _("Haiti")),
    "HUN": ("HU", _("Hungary")),
    "IDN": ("ID", _("Indonesia")),
    "IMN": ("IM", _("Isle of Man")),
    "IND": ("IN", _("India")),
    "IRL": ("IE", _("Ireland")),
    "IRN": ("IR", _("Iran")),
    "ISL": ("IS", _("Iceland")),
    "ISR": ("IL", _("Israel")),
    "ITA": ("IT", _("Italy")),
    "JAM": ("JM", _("Jamaica")),
    "JPN": ("JP", _("Japan")),
    "KAZ": ("KZ", _("Kazakhstan")),
    "KEN": ("KE", _("Kenya")),
    "KGZ": ("KG", _("Kyrgyzstan")),
    "KHM": ("KH", _("Cambodia")),
    "LBR": ("LR", _("Liberia")),
    "LKA": ("LK", _("Sri Lanka")),
    "LTU": ("LT", _("Lithuania")),
    "LUX": ("LU", _("Luxembourg")),
    "LVA": ("LV", _("Latvia")),
    "MAR": ("MA", _("Morocco")),
    "MCO": ("MC", _("Monaco")),
    "MDA": ("MD", _("Moldova")),
    "MDG": ("MG", _("Madagascar")),
    "MDV": ("MV", _("Maldives")),
    "MEX": ("MX", _("Mexico")),
    "MKD": ("MK", _("North Macedonia")),
    "MLI": ("ML", _("Mali")),
    "MLT": ("MT", _("Malta")),
    "MNE": ("ME", _("Montenegro")),
    "MTQ": ("MQ", _("Martinique")),
    "MWI": ("MW", _("Malawi")),
    "MYS": ("MY", _("Malaysia")),
    "NAM": ("NA", _("Namibia")),
    "NER": ("NE", _("Niger")),
    "NGA": ("NG", _("Nigeria")),
    "NIC": ("NI", _("Nicaragua")),
    "NLD": ("NL", _("Netherlands")),
    "NOR": ("NO", _("Norway")),
    "NPL": ("NP", _("Nepal")),
    "NZL": ("NZ", _("New Zealand")),
    "PAK": ("PK", _("Pakistan")),
    "PAN": ("PA", _("Panama")),
    "PER": ("PE", _("Peru")),
    "PHL": ("PH", _("Philippines")),
    "POL": ("PL", _("Poland")),
    "PRI": ("PR", _("Puerto Rico")),
    "PRT": ("PT", _("Portugal")),
    "PSE": ("PS", _("Palestine")),
    "PYF": ("PF", _("French Polynesia")),
    "ROU": ("RO", _("Romania")),
    "RUS": ("RU", _("Russia")),
    "SEN": ("SN", _("Senegal")),
    "SGP": ("SG", _("Singapore")),
    "SLB": ("SB", _("Solomon Islands")),
    "SLE": ("SL", _("Sierra Leone")),
    "SMR": ("SM", _("San Marino")),
    "SOM": ("SO", _("Somalia")),
    "SRB": ("RS", _("Serbia")),
    "SUR": ("SR", _("Suriname")),
    "SVK": ("SK", _("Slovakia")),
    "SVN": ("SI", _("Slovenia")),
    "SWE": ("SE", _("Sweden")),
    "TGO": ("TG", _("Togo")),
    "THA": ("TH", _("Thailand")),
    "TUN": ("TN", _("Tunisia")),
    "TUR": ("TR", _("Türkiye")),
    "UGA": ("UG", _("Uganda")),
    "UKR": ("UA", _("Ukraine")),
    "URY": ("UY", _("Uruguay")),
    "USA": ("US", _("United States")),
    "UZB": ("UZ", _("Uzbekistan")),
    "VEN": ("VE", _("Venezuela")),
    "VIR": ("VI", _("U.S. Virgin Islands")),
    "VNM": ("VN", _("Vietnam")),
    "XKK": ("XK", _("Kosovo")),
    "YEM": ("YE", _("Yemen")),
    "ZAF": ("ZA", _("South Africa")),
    "ZMB": ("ZM", _("Zambia")),
    "ZWE": ("ZW", _("Zimbabwe")),
}


def country_name(code: str) -> str:
    """Return a translated country name or the normalized unknown code."""
    normalized = str(code or "").strip().upper()
    if normalized == "GLOBAL":
        return _("Global")
    entry = _COUNTRIES.get(normalized)
    return entry[1] if entry is not None else normalized


def country_code_for_locale(locale_name: str | None = None) -> str | None:
    """Convert the locale territory to a catalog alpha-3 country code."""
    current = locale_name
    if current is None:
        current = locale.getlocale()[0]
    if not current:
        return None
    territory = current.split(".", 1)[0].split("@", 1)[0]
    parts = territory.replace("-", "_").split("_", 1)
    if len(parts) != 2:
        return None
    alpha_2 = parts[1].upper()
    for alpha_3, (candidate, _name) in _COUNTRIES.items():
        if candidate == alpha_2:
            return alpha_3
    return None


def sorted_country_codes(codes: set[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Sort available codes by display name while pinning Global first."""
    normalized = {str(code).strip().upper() for code in codes if str(code).strip()}
    global_codes = ("GLOBAL",) if "GLOBAL" in normalized else ()
    normalized.discard("GLOBAL")
    countries = sorted(
        normalized,
        key=lambda code: country_name(code).casefold(),
    )
    return (*global_codes, *countries)
