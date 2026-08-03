"""Tests TDD de los helpers Mongo ``write_airports`` / ``write_runways``.

Sin red ni DB real: se monkeypatchea la colección por un fake que registra
cada llamada a ``replace_one`` (filter, doc) y mantiene un store por ``_id``
con semántica upsert fiel a MongoDB.
"""

import pytest

from aeropredict.opensky import storage_silver


class FakeReplaceResult:
    def __init__(self, upserted_id, modified_count):
        self.upserted_id = upserted_id
        self.modified_count = modified_count


class FakeCollection:
    """Fake de pymongo.Collection con upsert por ``_id`` y registro de llamadas."""

    def __init__(self, name):
        self.name = name
        self.docs = {}
        self.replace_calls = []
        self.create_index_calls = []

    def create_index(self, *args, **kwargs):
        self.create_index_calls.append((args, kwargs))

    def replace_one(self, filter, doc, upsert=False):
        self.replace_calls.append((dict(filter), dict(doc)))
        _id = filter["_id"]
        if _id in self.docs:
            changed = self.docs[_id] != doc
            self.docs[_id] = dict(doc)
            return FakeReplaceResult(None, 1 if changed else 0)
        self.docs[_id] = dict(doc)
        return FakeReplaceResult(_id, 0)


class FakeAdmin:
    def command(self, cmd):
        return {"ok": 1.0}


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection(name)
        return self.collections[name]


class FakeClient:
    def __init__(self):
        self.admin = FakeAdmin()
        self.db = FakeDatabase()

    def get_database(self):
        return self.db


def _airport_doc(ident, **overrides):
    doc = {
        "ident": ident,
        "type": "large_airport",
        "name": f"Airport {ident}",
        "latitude_deg": "40.47",
        "longitude_deg": "-3.56",
        "elevation_ft": "1998",
        "iso_country": "ES",
        "iso_region": "ES-MD",
        "municipality": "Madrid",
        "iata_code": "MAD",
        "icao_code": ident,
    }
    doc.update(overrides)
    return doc


def _runway_doc(airport_ident, le_ident, he_ident, **overrides):
    doc = {
        "airport_ident": airport_ident,
        "length_ft": "13450",
        "width_ft": "197",
        "surface": "asphalt",
        "le_ident": le_ident,
        "he_ident": he_ident,
        "le_heading_degT": "143",
        "he_heading_degT": "323",
    }
    doc.update(overrides)
    return doc


def test_write_airports_upserts_by_ident(monkeypatch):
    fake = FakeCollection("airports")
    monkeypatch.setattr(storage_silver, "_get_airport_collection", lambda *a, **k: fake)

    n = storage_silver.write_airports([_airport_doc("LEMD"), _airport_doc("LEAL")])

    assert n == 2
    assert [call[0] for call in fake.replace_calls] == [{"_id": "LEMD"}, {"_id": "LEAL"}]
    assert set(fake.docs) == {"LEMD", "LEAL"}
    assert fake.docs["LEMD"]["_id"] == "LEMD"


def test_write_airports_duplicate_ident_does_not_duplicate(monkeypatch):
    fake = FakeCollection("airports")
    monkeypatch.setattr(storage_silver, "_get_airport_collection", lambda *a, **k: fake)

    docs = [_airport_doc("LEMD"), _airport_doc("LEAL")]
    first = storage_silver.write_airports(docs)
    second = storage_silver.write_airports(docs)

    assert first == 2
    assert second == 0  # docs idénticos: upsert no actualiza nada
    assert len(fake.replace_calls) == 4  # una llamada replace_one por doc e invocación
    assert set(fake.docs) == {"LEMD", "LEAL"}  # mismo _id no duplica


def test_write_runways_composite_id(monkeypatch):
    fake = FakeCollection("runways")
    monkeypatch.setattr(storage_silver, "_get_runway_collection", lambda *a, **k: fake)

    runways = [_runway_doc("LEMD", "14R", "32L"), _runway_doc("LEMD", "18L", "36R")]
    n = storage_silver.write_runways(runways)

    assert n == 2
    assert [call[0] for call in fake.replace_calls] == [
        {"_id": "LEMD:14R:32L"},
        {"_id": "LEMD:18L:36R"},
    ]
    assert runways[0]["_id"] == "LEMD:14R:32L"
    assert runways[1]["_id"] == "LEMD:18L:36R"
    assert set(fake.docs) == {"LEMD:14R:32L", "LEMD:18L:36R"}


def test_write_runways_duplicate_id_is_idempotent(monkeypatch):
    fake = FakeCollection("runways")
    monkeypatch.setattr(storage_silver, "_get_runway_collection", lambda *a, **k: fake)

    runways = [_runway_doc("LEAL", "10", "28")]
    assert storage_silver.write_runways(runways) == 1
    assert storage_silver.write_runways(runways) == 0
    assert set(fake.docs) == {"LEAL:10:28"}
    assert len(fake.replace_calls) == 2


def test_write_airports_missing_optional_fields_still_written(monkeypatch):
    fake = FakeCollection("airports")
    monkeypatch.setattr(storage_silver, "_get_airport_collection", lambda *a, **k: fake)

    n = storage_silver.write_airports([{"ident": "LEMD", "type": "large_airport"}])

    assert n == 1
    stored = fake.docs["LEMD"]
    assert stored["ident"] == "LEMD"
    assert stored["type"] == "large_airport"
    assert stored["_id"] == "LEMD"


def test_write_runways_missing_optional_fields_still_written(monkeypatch):
    fake = FakeCollection("runways")
    monkeypatch.setattr(storage_silver, "_get_runway_collection", lambda *a, **k: fake)

    runways = [{
        "airport_ident": "LEMD",
        "le_ident": "14R",
        "he_ident": "32L",
    }]
    n = storage_silver.write_runways(runways)

    assert n == 1
    assert fake.docs["LEMD:14R:32L"]["airport_ident"] == "LEMD"
    assert fake.docs["LEMD:14R:32L"]["_id"] == "LEMD:14R:32L"


def test_write_airports_missing_ident_raises_key_error(monkeypatch):
    fake = FakeCollection("airports")
    monkeypatch.setattr(storage_silver, "_get_airport_collection", lambda *a, **k: fake)

    with pytest.raises(KeyError):
        storage_silver.write_airports([{"type": "large_airport"}])


def test_write_airports_empty_returns_zero_without_touching_db(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("con lista vacía no debe conectar a Mongo")

    monkeypatch.setattr(storage_silver, "_get_airport_collection", fail_if_called)
    monkeypatch.setattr(storage_silver, "_get_runway_collection", fail_if_called)

    assert storage_silver.write_airports([]) == 0
    assert storage_silver.write_runways([]) == 0


def test_write_airports_forwards_mongo_uri(monkeypatch):
    captured = {}

    def fake_getter(mongo_uri=None):
        captured["mongo_uri"] = mongo_uri
        return FakeCollection("airports")

    monkeypatch.setattr(storage_silver, "_get_airport_collection", fake_getter)

    storage_silver.write_airports([_airport_doc("LEMD")], mongo_uri="mongodb://other:27017/x")

    assert captured["mongo_uri"] == "mongodb://other:27017/x"


def test_airport_collection_ensures_unique_id_index(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(storage_silver, "_connect", lambda: None)
    monkeypatch.setattr(storage_silver, "_client", client)
    monkeypatch.setattr(storage_silver, "_airports_indexes_ensure", False)

    col = storage_silver._get_airport_collection()
    storage_silver._get_airport_collection()  # segunda llamada: no re-crea índice

    assert col is client.db["airports"]
    assert client.db["airports"].create_index_calls == [(("_id",), {})]


def test_runway_collection_ensures_unique_id_index(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(storage_silver, "_connect", lambda: None)
    monkeypatch.setattr(storage_silver, "_client", client)
    monkeypatch.setattr(storage_silver, "_runways_indexes_ensure", False)

    col = storage_silver._get_runway_collection()

    assert col is client.db["runways"]
    assert client.db["runways"].create_index_calls == [(("_id",), {})]


def test_airport_collection_uses_dedicated_client_when_mongo_uri(monkeypatch):
    created = {}

    def fake_mongo_client(uri, **kwargs):
        client = FakeClient()
        created["uri"] = uri
        created["client"] = client
        return client

    monkeypatch.setattr(storage_silver.pymongo, "MongoClient", fake_mongo_client)
    monkeypatch.setattr(storage_silver, "_airports_indexes_ensure", False)

    col = storage_silver._get_airport_collection("mongodb://other:27017/x")

    assert created["uri"] == "mongodb://other:27017/x"
    assert col is created["client"].db["airports"]
