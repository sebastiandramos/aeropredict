"""Tests for the configuration module."""

from __future__ import annotations

from aeropredict.opensky.config import (
    BBOX_ESPANA,
    BBOX_EUROPA_OESTE,
    BoundingBox,
    get_delta_root,
    get_mongo_uri,
    get_postgres_uri,
)


class TestBoundingBox:
    """Bounding box configuration."""

    def test_bbox_espana_defaults(self) -> None:
        """Bounding box for Spain has expected lat/lon ranges."""
        assert isinstance(BBOX_ESPANA, BoundingBox)
        assert BBOX_ESPANA.lamin == 36.0
        assert BBOX_ESPANA.lamax == 43.8
        assert BBOX_ESPANA.lomin == -9.3
        assert BBOX_ESPANA.lomax == 4.3

    def test_bbox_europa_dimensions(self) -> None:
        """The wider European bounding box contains Spain's box."""
        assert BBOX_EUROPA_OESTE.lamin < BBOX_ESPANA.lamin
        assert BBOX_EUROPA_OESTE.lamax > BBOX_ESPANA.lamax
        assert BBOX_EUROPA_OESTE.lomin < BBOX_ESPANA.lomin
        assert BBOX_EUROPA_OESTE.lomax > BBOX_ESPANA.lomax


class TestConnectionURIs:
    """Default connection URIs."""

    def test_get_mongo_uri_default(self) -> None:
        """Default MongoDB URI points to local Docker."""
        uri = get_mongo_uri()
        assert uri == "mongodb://localhost:27017/aeropredict"

    def test_get_postgres_uri_default(self) -> None:
        """Default PostgreSQL URI points to local Docker."""
        uri = get_postgres_uri()
        assert uri == "postgresql://aeropredict:aeropredict@localhost:5432/aeropredict"

    def test_get_delta_root_default(self) -> None:
        """Default Delta root is data/raw."""
        root = get_delta_root()
        assert root == "data/raw"
