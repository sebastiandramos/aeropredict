"""Download OurAirports CSV and generate/validate IATA→ICAO mapping.

Two modes:

* ``--generate``: downloads the CSV, filters airports matching ICAO codes
  in ``AEROPUERTOS``, and writes ``src/aeropredict/sources/airport_codes.py``.
* ``--check``: downloads the CSV, compares with the existing file, prints
  discrepancies, and exits with code 1 if any mismatch is found.

Usage::

    python scripts/update_airport_codes.py --generate
    python scripts/update_airport_codes.py --check
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
from pathlib import Path
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OURAIRPORTS_URL = (
    "https://raw.githubusercontent.com/davidmegginson/ourairports-data/"
    "master/airports.csv"
)
TARGET_PATH = Path("src/aeropredict/sources/airport_codes.py")

# ICAO prefixes → region
_REGION_PREFIXES: list[tuple[str, str]] = [
    ("GC", "España (Canarias)"),
    ("LE", "España (peninsular)"),
    ("LP", "Portugal"),
]


def _classify_region(icao: str) -> str:
    """Classify an ICAO code into a region string."""
    # Baleares special case: LEMH (MAH), LEIB (IBZ), LEPA (PMI)
    if icao in {"LEMH", "LEIB", "LEPA"}:
        return "España (Baleares)"
    for prefix, region in _REGION_PREFIXES:
        if icao.startswith(prefix):
            return region
    return "Europa"


# Ordered region list for output grouping
_REGION_ORDER = [
    "España (peninsular)",
    "España (Canarias)",
    "España (Baleares)",
    "Portugal",
    "Europa",
]

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def _fetch_csv() -> str:
    """Download the OurAirports CSV and return its text content."""
    print(f"Downloading OurAirports CSV from {OURAIRPORTS_URL} ...")
    try:
        with urllib.request.urlopen(OURAIRPORTS_URL, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except (URLError, OSError) as exc:
        print(f"Error: failed to download CSV: {exc}", file=sys.stderr)
        sys.exit(1)


def _parse_airports(csv_text: str, icao_codes: set[str]) -> dict[str, str]:
    """Parse the CSV and return IATA→ICAO mapping for matching airports.

    Args:
        csv_text: Raw CSV content.
        icao_codes: Set of ICAO codes from AEROPUERTOS to filter on.

    Returns:
        Dict mapping IATA code → ICAO code.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    mapping: dict[str, str] = {}
    for row in reader:
        icao = row.get("ident", "").strip()
        iata = row.get("iata_code", "").strip()
        airport_type = row.get("type", "").strip()
        if not iata:
            continue
        if airport_type not in ("large_airport", "medium_airport"):
            continue
        if icao not in icao_codes:
            continue
        mapping[iata] = icao
    return mapping


# ---------------------------------------------------------------------------
# File generation
# ---------------------------------------------------------------------------

_MODULE_DOCSTRING = '''\
"""IATA-to-ICAO airport code mapping.

Provides a static dictionary covering all airports in the AEROPUERTOS list
(42 Spanish + 12 European hubs) for enriching AENA IATA codes with ICAO
codes during the bronze-to-silver transition.

Source: OurAirports open dataset (https://github.com/davidmegginson/ourairports-data)
— ``airports.csv`` columns ``iata_code`` / ``icao_code``, public domain.
Verified {date} against latest commit.
"""
'''


def _build_module(mapping: dict[str, str], date_str: str) -> str:
    """Build the full content of airport_codes.py from a mapping dict."""
    # Group by region
    by_region: dict[str, list[tuple[str, str]]] = {r: [] for r in _REGION_ORDER}
    for iata, icao in sorted(mapping.items()):
        region = _classify_region(icao)
        by_region[region].append((iata, icao))

    lines: list[str] = []
    lines.append(_MODULE_DOCSTRING.format(date=date_str))
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("IATA_TO_ICAO: dict[str, str] = {")

    for region in _REGION_ORDER:
        entries = by_region[region]
        if not entries:
            continue
        lines.append(f"    # --- {region} ---")
        for iata, icao in entries:
            lines.append(f'    "{iata}": "{icao}",')

    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append(
        "def get_icao_for_iata(iata: str) -> str | None:"
    )
    lines.append(
        '    """Return the ICAO code for a given IATA code (case-insensitive).'
    )
    lines.append("")
    lines.append("    Args:")
    lines.append("        iata: Three-letter IATA airport code.")
    lines.append("")
    lines.append("    Returns:")
    lines.append(
        "        The corresponding ICAO code, or None if not found."
    )
    lines.append('    """')
    lines.append("    return IATA_TO_ICAO.get(iata.upper())")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------


def _run_generate(icao_codes: set[str]) -> None:
    """Download CSV and write airport_codes.py."""
    csv_text = _fetch_csv()
    mapping = _parse_airports(csv_text, icao_codes)
    print(f"Found {len(mapping)} airports matching AEROPUERTOS ICAO codes.")
    if not mapping:
        print("Error: no airports found — check AEROPUERTOS list.", file=sys.stderr)
        sys.exit(1)

    from datetime import date

    content = _build_module(mapping, date.today().isoformat())
    TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGET_PATH.write_text(content, encoding="utf-8")
    print(f"Written {TARGET_PATH}")


def _load_existing_mapping() -> dict[str, str] | None:
    """Parse the existing airport_codes.py and return IATA→ICAO dict."""
    if not TARGET_PATH.exists():
        return None
    text = TARGET_PATH.read_text(encoding="utf-8")
    # Quick parse: extract "IATA": "ICAO" pairs from the dict literal
    import re

    pattern = re.compile(r'"([A-Z]{3})":\s*"([A-Z]{4})"')
    return {m.group(1): m.group(2) for m in pattern.finditer(text)}


def _run_check(icao_codes: set[str]) -> None:
    """Download CSV, compare with existing file, report discrepancies."""
    csv_text = _fetch_csv()
    expected = _parse_airports(csv_text, icao_codes)
    print(f"Found {len(expected)} airports in OurAirports matching AEROPUERTOS.")

    existing = _load_existing_mapping()
    if existing is None:
        print(f"Error: {TARGET_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    discrepancies: list[str] = []

    # Check for missing or wrong mappings
    for iata in sorted(expected):
        if iata not in existing:
            discrepancies.append(
                f"  MISSING: {iata} \u2192 {expected[iata]} (not in file)"
            )
        elif existing[iata] != expected[iata]:
            discrepancies.append(
                f"  MISMATCH: {iata} \u2192 expected {expected[iata]}, "
                f"got {existing[iata]}"
            )

    # Check for extra entries in file not in OurAirports
    for iata in sorted(existing):
        if iata not in expected:
            discrepancies.append(
                f"  EXTRA: {iata} \u2192 {existing[iata]} (not in OurAirports)"
            )

    if discrepancies:
        print(f"Found {len(discrepancies)} discrepancies:")
        for d in discrepancies:
            print(d)
        sys.exit(1)
    else:
        print("✅ All mappings correct")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the CLI."""
    # Ensure UTF-8 output on Windows (cp1252 default can't handle arrows/emoji)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(
        description="Generate or validate IATA-to-ICAO mapping from OurAirports."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--generate",
        action="store_true",
        help="Download CSV and overwrite airport_codes.py.",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Download CSV and compare with existing airport_codes.py.",
    )
    args = parser.parse_args()

    # Import AEROPUERTOS ICAO codes
    from aeropredict.opensky.config import AEROPUERTOS

    icao_codes = {code for code, *_ in AEROPUERTOS}

    if args.generate:
        _run_generate(icao_codes)
    elif args.check:
        _run_check(icao_codes)


if __name__ == "__main__":
    main()
