"""Configuración global del módulo opensky.

Las variables de entorno se cargan desde .env si existe.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Bounding boxes predefinidos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundingBox:
    """Rectángulo geográfico WGS84."""

    lamin: float
    lamax: float
    lomin: float
    lomax: float


# España peninsular + Baleares
BBOX_ESPANA = BoundingBox(lamin=36.0, lamax=43.8, lomin=-9.3, lomax=4.3)

# Europa occidental (área grande, consume más créditos)
BBOX_EUROPA_OESTE = BoundingBox(lamin=35.0, lamax=60.0, lomin=-10.0, lomax=25.0)

# ---------------------------------------------------------------------------
# Lista de aeropuertos europeos
# ---------------------------------------------------------------------------

# (código ICAO, nombre, ciudad, país)
AEROPUERTOS: list[tuple[str, str, str, str]] = [
    # --- España ---
    ("LEMD", "Adolfo Suárez Madrid-Barajas", "Madrid", "España"),
    ("LEBL", "Barcelona-El Prat", "Barcelona", "España"),
    ("LEAL", "Alicante-Elche", "Alicante", "España"),
    ("LEMG", "Málaga-Costa del Sol", "Málaga", "España"),
    ("LEVC", "Valencia-Manises", "Valencia", "España"),
    ("LEIB", "Ibiza", "Ibiza", "España"),
    ("LEPA", "Palma de Mallorca", "Palma", "España"),
    ("LEZG", "Zaragoza", "Zaragoza", "España"),
    ("LEAS", "Asturias", "Asturias", "España"),
    ("LEVX", "Vigo-Peinador", "Vigo", "España"),
    ("LESO", "San Sebastián", "San Sebastián", "España"),
    ("LEBB", "Bilbao", "Bilbao", "España"),
    ("LEXJ", "Santander-Seve Ballesteros", "Santander", "España"),
    ("LECO", "A Coruña", "A Coruña", "España"),
    ("LEGE", "Girona-Costa Brava", "Girona", "España"),
    ("LELL", "Sabadell", "Sabadell", "España"),
    ("LELN", "León", "León", "España"),
    ("LEGR", "Granada-José María Cordero", "Granada", "España"),
    ("LEJR", "Jerez", "Jerez", "España"),
    ("LEZL", "Sevilla-San Pablo", "Sevilla", "España"),
    ("LEBT", "Córdoba", "Córdoba", "España"),
    ("LEAB", "Albacete", "Albacete", "España"),
    ("LEMO", "Morón", "Morón", "España"),
    ("GCFV", "Fuerteventura", "Fuerteventura", "España"),
    ("GCLP", "Gran Canaria", "Gran Canaria", "España"),
    ("GCXO", "Tenerife Norte-Ciudad de La Laguna", "Tenerife", "España"),
    ("GCTS", "Tenerife Sur", "Tenerife", "España"),
    ("GCLA", "La Palma", "La Palma", "España"),
    ("GCGM", "La Gomera", "La Gomera", "España"),
    ("GCHI", "El Hierro", "El Hierro", "España"),
    ("GCJA", "Jandía", "Jandía", "España"),
    ("LEMH", "Menorca", "Menorca", "España"),
    ("LPPD", "Ponta Delgada-João Paulo II", "Azores", "Portugal"),
    ("LPPT", "Lisboa-Humberto Delgado", "Lisboa", "Portugal"),
    ("LPPR", "Porto-Francisco Sá Carneiro", "Porto", "Portugal"),
    ("LPFR", "Faro-Gago Coutinho", "Faro", "Portugal"),
    # --- Principales hubs europeos ---
    ("EGLL", "London Heathrow", "Londres", "Reino Unido"),
    ("EGKK", "London Gatwick", "Londres", "Reino Unido"),
    ("LFPG", "Paris Charles de Gaulle", "París", "Francia"),
    ("LFPO", "Paris Orly", "París", "Francia"),
    ("EDDF", "Frankfurt am Main", "Fráncfort", "Alemania"),
    ("EDDM", "Munich", "Múnich", "Alemania"),
    ("EDDB", "Berlin Brandenburg", "Berlín", "Alemania"),
    ("EHAM", "Amsterdam Schiphol", "Ámsterdam", "Países Bajos"),
    ("LIRF", "Roma Fiumicino", "Roma", "Italia"),
    ("LIML", "Milán Linate", "Milán", "Italia"),
    ("LSZH", "Zürich", "Zúrich", "Suiza"),
    ("LSGG", "Genève", "Ginebra", "Suiza"),
    ("LOWW", "Vienna International", "Viena", "Austria"),
    ("EKCH", "Copenhague Kastrup", "Copenhague", "Dinamarca"),
    ("ESSA", "Stockholm Arlanda", "Estocolmo", "Suecia"),
    ("ENGM", "Oslo Gardermoen", "Oslo", "Noruega"),
    ("EFHK", "Helsinki-Vantaa", "Helsinki", "Finlandia"),
    ("EPWA", "Varsovia Chopin", "Varsovia", "Polonia"),
    ("LKPR", "Václav Havel Prague", "Praga", "República Checa"),
    ("LHBP", "Budapest Liszt Ferenc", "Budapest", "Hungría"),
    ("LBSF", "Sofía", "Sofía", "Bulgaria"),
    ("LRBB", "Henri Coandă Bucarest", "Bucarest", "Rumanía"),
    ("LGAV", "Atenas Eleftherios Venizelos", "Atenas", "Grecia"),
    ("LTFM", "Estambul", "Estambul", "Turquía"),
    ("UUDD", "Moscú Domodedovo", "Moscú", "Rusia"),
    ("ULLI", "San Petersburgo Pulkovo", "San Petersburgo", "Rusia"),
]

# ---------------------------------------------------------------------------
# Configuración desde variables de entorno
# ---------------------------------------------------------------------------


def get_client_id() -> str:
    return os.environ.get("OPENSKY_CLIENT_ID", "")


def get_client_secret() -> str:
    return os.environ.get("OPENSKY_CLIENT_SECRET", "")


def get_delta_root() -> str:
    """Ruta base para tablas Delta. Por defecto data/raw/."""
    return os.environ.get("OPENSKY_DELTA_ROOT", "data/raw")


def get_bbox() -> BoundingBox:
    """Bounding box desde variables de entorno o el de España por defecto."""
    lamin = os.environ.get("OPENSKY_BBOX_LAMIN")
    lamax = os.environ.get("OPENSKY_BBOX_LAMAX")
    lomin = os.environ.get("OPENSKY_BBOX_LOMIN")
    lomax = os.environ.get("OPENSKY_BBOX_LOMAX")
    if all(v is not None for v in (lamin, lamax, lomin, lomax)):
        return BoundingBox(
            lamin=float(lamin),
            lamax=float(lamax),
            lomin=float(lomin),
            lomax=float(lomax),
        )
    return BBOX_ESPANA


def get_airport_icao_codes() -> list[str]:
    """Devuelve lista de códigos ICAO de aeropuertos configurados."""
    return [code for code, *_ in AEROPUERTOS]
