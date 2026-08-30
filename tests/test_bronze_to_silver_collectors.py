"""Tests de los doc builders puros de Bronze → Silver (fuentes complementarias).

Patrón importlib de ``tests/test_bronze_to_silver_weather.py``: se carga
``scripts/bronze_to_silver.py`` sin ejecutar su ``main`` y se prueban los
builders puros (sin Delta, sin Mongo) con fixtures pequeños y realistas.
"""

import importlib.util
import json
from pathlib import Path


def _load_bronze_to_silver_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "bronze_to_silver.py"
    spec = importlib.util.spec_from_file_location("bronze_to_silver_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _bronze_row(source, endpoint, params, response):
    """Fila Bronze realista según RAW_ROW_SCHEMA (fetched_at se omite aquí)."""
    return {
        "source": source,
        "endpoint": endpoint,
        "params": json.dumps(params),
        "response": response,
    }


# ---------------------------------------------------------------------------
# METAR (NOAA AWC) — CSV camelCase, skip filas sin icaoId, coerción numérica
# ---------------------------------------------------------------------------

METAR_CSV = (
    "icaoId,rawOb,receiptTime,obsTime,temp,dewp,wdir,wspd,wgst,visib,altim,fltCat,clouds_base\n"
    "LEMD,LEMD 010900Z 28005KT 9999 SCT030 18/12 Q1020 NOSIG,2026-08-01T09:00:00Z,1754046000,"
    "18.0,12.0,280,5,8,9999,1020.0,VFR,3000\n"
    ",,,,,,,,,,,,\n"
    "LEBL,LEBL 010905Z 09003KT 9000 -RA BKN012 20/M,2026-08-01T09:05:00Z,1754046300,"
    "M,,N/A,3,,9000,1019.0,MVFR,\n"
)


def test_build_metar_docs_maps_camelcase_and_skips_empty_icao():
    module = _load_bronze_to_silver_module()
    rows = [_bronze_row("metar_awc", "https://aviationweather.gov/api/data/metar",
                        {"ids": "LEMD,LEBL", "hours": 48}, METAR_CSV)]

    docs = module._build_metar_docs(rows)

    assert len(docs) == 2  # la fila con icaoId vacío se omite
    assert docs[0] == {
        "icao_id": "LEMD",
        "raw_ob": "LEMD 010900Z 28005KT 9999 SCT030 18/12 Q1020 NOSIG",
        "receipt_time": "2026-08-01T09:00:00Z",
        "obs_time": 1754046000,
        "temp": 18.0,
        "dewp": 12.0,
        "wdir": 280,
        "wspd": 5,
        "wgst": 8,
        "visib": "9999",
        "altim": 1020.0,
        "flt_cat": "VFR",
        "clouds_base": 3000,
    }
    assert docs[1] == {
        "icao_id": "LEBL",
        "raw_ob": "LEBL 010905Z 09003KT 9000 -RA BKN012 20/M",
        "receipt_time": "2026-08-01T09:05:00Z",
        "obs_time": 1754046300,
        "temp": None,          # "M" → None
        "dewp": None,          # vacío → None
        "wdir": None,          # "N/A" → None
        "wspd": 3,
        "wgst": None,          # vacío → None
        "visib": "9000",
        "altim": 1019.0,
        "flt_cat": "MVFR",
        "clouds_base": None,   # vacío → None
    }


def test_build_metar_docs_empty_response_yields_no_docs():
    module = _load_bronze_to_silver_module()

    assert module._build_metar_docs([]) == []
    assert module._build_metar_docs([{"response": None}]) == []


# ---------------------------------------------------------------------------
# Nager.Date — CSV con global/counties/types
# ---------------------------------------------------------------------------

NAGER_CSV = (
    "date,localName,name,countryCode,global,counties,types\n"
    "2026-01-01,Año Nuevo,New Year's Day,ES,true,,Public\n"
    "2026-03-19,San José,San Jose,ES,false,VC|AN,Public|Local\n"
)


def test_build_nager_docs_maps_csv_row_and_splits_pipes():
    module = _load_bronze_to_silver_module()
    rows = [_bronze_row("holidays_nager_date", "https://date.nager.at/api/v4/Holidays/ES/2026",
                        {"country": "ES", "year": 2026}, NAGER_CSV)]

    docs = module._build_nager_docs(rows)

    assert len(docs) == 2
    assert docs[0] == {
        "date": "2026-01-01",
        "name": "New Year's Day",
        "local_name": "Año Nuevo",
        "country_code": "ES",
        "is_global": True,
        "counties": [],
        "types": ["Public"],
        "source": "nager_date",
        "subdivision": "",
    }
    assert docs[1] == {
        "date": "2026-03-19",
        "name": "San Jose",
        "local_name": "San José",
        "country_code": "ES",
        "is_global": False,
        "counties": ["VC", "AN"],
        "types": ["Public", "Local"],
        "source": "nager_date",
        "subdivision": "",
    }


# ---------------------------------------------------------------------------
# python-holidays — JSON snapshot {subdiv: {fecha: nombre}}
# ---------------------------------------------------------------------------

PYTHON_PAYLOAD = {
    "raw": {
        "AN": {"2026-01-01": "Año Nuevo", "2026-02-28": "Día de Andalucía"},
        "ES": {"2026-01-01": "Año Nuevo"},
    },
    "count": 3,
    "subdivisions": "all",
}


def test_build_python_holidays_docs_emits_one_doc_per_subdivision_date():
    module = _load_bronze_to_silver_module()
    rows = [_bronze_row(
        "holidays_python", "python_holidays://ES/2026",
        {"country": "ES", "year": 2026, "subdivisions": "all"},
        json.dumps(PYTHON_PAYLOAD),
    )]

    docs = module._build_python_holidays_docs(rows)

    assert len(docs) == 3
    assert docs[0] == {
        "date": "2026-01-01",
        "name": "Año Nuevo",
        "local_name": "",
        "country_code": "ES",
        "is_global": False,
        "counties": [],
        "types": [],
        "source": "python_holidays",
        "subdivision": "AN",
    }
    assert docs[1]["date"] == "2026-02-28"
    assert docs[1]["name"] == "Día de Andalucía"
    assert docs[1]["subdivision"] == "AN"
    assert docs[2] == {
        "date": "2026-01-01",
        "name": "Año Nuevo",
        "local_name": "",
        "country_code": "ES",
        "is_global": False,
        "counties": [],
        "types": [],
        "source": "python_holidays",
        "subdivision": "ES",
    }


def test_build_python_holidays_docs_invalid_json_yields_no_docs():
    module = _load_bronze_to_silver_module()

    assert module._build_python_holidays_docs([{"response": "{not json"}]) == []


# ---------------------------------------------------------------------------
# EUROCONTROL PRU — CSV extranjero con BOM, spread de columnas + source_file/year
# ---------------------------------------------------------------------------

EUROCONTROL_CSV = (
    "\ufeffYear,Month,APT_ICAO,APT_NAME,Flights\r\n"
    "2026,1,LEMD,Madrid Barajas,1200\r\n"
    "2026,1,LEBL,Barcelona El Prat,900\r\n"
)


def test_build_eurocontrol_docs_spreads_csv_columns_and_adds_metadata():
    module = _load_bronze_to_silver_module()
    rows = [_bronze_row(
        "eurocontrol_pru", "https://ansperformance.eu/economics/...",
        {"filename": "airport_traffic", "year": 2026},
        EUROCONTROL_CSV,
    )]

    docs = module._build_eurocontrol_docs(rows)

    assert len(docs) == 2
    # El BOM se elimina: la primera clave es "Year", no "\ufeffYear".
    assert docs[0] == {
        "Year": "2026",
        "Month": "1",
        "APT_ICAO": "LEMD",
        "APT_NAME": "Madrid Barajas",
        "Flights": "1200",
        "source_file": "airport_traffic",
        "year": 2026,
    }
    assert docs[1] == {
        "Year": "2026",
        "Month": "1",
        "APT_ICAO": "LEBL",
        "APT_NAME": "Barcelona El Prat",
        "Flights": "900",
        "source_file": "airport_traffic",
        "year": 2026,
    }


def test_build_eurocontrol_docs_missing_params_yields_no_docs():
    module = _load_bronze_to_silver_module()

    rows = [_bronze_row("eurocontrol_pru", "u", {}, EUROCONTROL_CSV)]
    assert module._build_eurocontrol_docs(rows) == []


# ---------------------------------------------------------------------------
# NOTAM (ENAIRE) — JSON snapshot con layers → un doc por feature GeoJSON
# ---------------------------------------------------------------------------

NOTAM_PAYLOAD = {
    "source": "notam_enaire",
    "snapshot_at": "2026-08-04T06:00:00Z",
    "layers": [
        {
            "raw": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"id": "P1"}, "geometry": None},
                    {"type": "Feature", "properties": {"id": "P2"}, "geometry": None},
                ],
            },
            "count": 2,
            "layer": 0,
        },
        {
            "raw": {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"id": "A1"}, "geometry": None}],
            },
            "count": 1,
            "layer": 1,
        },
    ],
}


def test_build_notam_docs_emits_one_doc_per_feature():
    module = _load_bronze_to_silver_module()
    rows = [_bronze_row(
        "notam_enaire", "https://servais.enaire.es/.../FeatureServer",
        {"layers": "0,1"},
        json.dumps(NOTAM_PAYLOAD),
    )]

    docs = module._build_notam_docs(rows)

    assert len(docs) == 3
    assert docs[0]["feature"] == {"type": "Feature", "properties": {"id": "P1"}, "geometry": None}
    assert docs[0]["layer"] == 0
    assert docs[0]["snapshot_at"] == "2026-08-04T06:00:00Z"
    assert docs[1]["feature"] == {"type": "Feature", "properties": {"id": "P2"}, "geometry": None}
    assert docs[1]["layer"] == 0
    assert docs[2]["feature"] == {"type": "Feature", "properties": {"id": "A1"}, "geometry": None}
    assert docs[2]["layer"] == 1


def test_build_notam_docs_skips_layer_without_features():
    module = _load_bronze_to_silver_module()
    payload = {
        "snapshot_at": "2026-08-04T06:00:00Z",
        "layers": [
            {"raw": {"features": []}, "count": 0, "layer": 0},
            {"raw": {"features": [{"type": "Feature"}]}, "count": 1, "layer": 1},
        ],
    }
    rows = [_bronze_row("notam_enaire", "u", {"layers": "0,1"}, json.dumps(payload))]

    docs = module._build_notam_docs(rows)

    assert len(docs) == 1
    assert docs[0]["layer"] == 1
