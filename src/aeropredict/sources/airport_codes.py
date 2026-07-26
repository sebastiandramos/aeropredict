"""IATA-to-ICAO airport code mapping.

Provides a static dictionary covering all airports in the AEROPUERTOS list
(42 Spanish + 12 European hubs) for enriching AENA IATA codes with ICAO
codes during the bronze-to-silver transition.

Source: OurAirports open dataset (https://github.com/davidmegginson/ourairports-data)
— ``airports.csv`` columns ``iata_code`` / ``icao_code``, public domain.
Verified 2026-07-26 against latest commit.
"""

from __future__ import annotations

IATA_TO_ICAO: dict[str, str] = {
    # --- España (peninsular) ---
    "ABC": "LEAB",
    "AGP": "LEMG",
    "ALC": "LEAL",
    "BCN": "LEBL",
    "BIO": "LEBB",
    "EAS": "LESO",
    "GRO": "LEGE",
    "GRX": "LEGR",
    "IBZ": "LEIB",
    "LCG": "LECO",
    "LEN": "LELN",
    "MAD": "LEMD",
    "OVD": "LEAS",
    "OZP": "LEMO",
    "PMI": "LEPA",
    "SDR": "LEXJ",
    "SVQ": "LEZL",
    "VGO": "LEVX",
    "VLC": "LEVC",
    "XRY": "LEJR",
    "ZAZ": "LEZG",
    # --- España (Canarias) ---
    "FUE": "GCFV",
    "LPA": "GCLP",
    "SPC": "GCLA",
    "TFN": "GCXO",
    "TFS": "GCTS",
    "VDE": "GCHI",
    # --- España (Baleares) ---
    "MAH": "LEMH",
    # --- Portugal ---
    "FAO": "LPFR",
    "LIS": "LPPT",
    "OPO": "LPPR",
    "PDL": "LPPD",
    # --- Europa ---
    "AMS": "EHAM",
    "ARN": "ESSA",
    "ATH": "LGAV",
    "BER": "EDDB",
    "BUD": "LHBP",
    "CDG": "LFPG",
    "CPH": "EKCH",
    "DME": "UUDD",
    "FCO": "LIRF",
    "FRA": "EDDF",
    "GVA": "LSGG",
    "HEL": "EFHK",
    "IST": "LTFM",
    "LED": "ULLI",
    "LGW": "EGKK",
    "LHR": "EGLL",
    "LIN": "LIML",
    "MUC": "EDDM",
    "ORY": "LFPO",
    "OSL": "ENGM",
    "PRG": "LKPR",
    "SOF": "LBSF",
    "VIE": "LOWW",
    "WAW": "EPWA",
    "ZRH": "LSZH",
}


def get_icao_for_iata(iata: str) -> str | None:
    """Return the ICAO code for a given IATA code (case-insensitive).

    Args:
        iata: Three-letter IATA airport code.

    Returns:
        The corresponding ICAO code, or None if not found.
    """
    return IATA_TO_ICAO.get(iata.upper())
