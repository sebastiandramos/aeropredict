"""Configuración global del módulo opensky.

Las variables de entorno se cargan desde:
1. Doppler (vía `doppler run`) — prioridad máxima
2. .env si existe — fallback local
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv  # type: ignore[import]

# No sobrescribe vars ya definidas (Doppler inyecta antes, .env es solo fallback)
load_dotenv(override=False)

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


def get_all_credentials() -> list[dict[str, str]]:
    """Descubre todas las cuentas OpenSky configuradas en el entorno.

    Busca variables con el patrón::

        OPENSKY_CLIENT_ID_{NOMBRE}
        OPENSKY_CLIENT_SECRET_{NOMBRE}

    También reconoce ``OPENSKY_CLIENT_ID`` / ``OPENSKY_CLIENT_SECRET``
    (sin sufijo) como cuenta ``default``.

    Returns:
        Lista de dicts con clave ``name``, ``id``, ``secret``.
    """
    accounts: list[dict[str, str]] = []

    # Cuenta primaria sin sufijo (compatibilidad hacia atrás)
    cid = os.environ.get("OPENSKY_CLIENT_ID")
    secret = os.environ.get("OPENSKY_CLIENT_SECRET")
    if cid and secret:
        accounts.append({"name": "default", "id": cid, "secret": secret})

    # Cuentas con nombre: OPENSKY_CLIENT_ID_{NAME}
    prefix = "OPENSKY_CLIENT_ID_"
    for key, value in os.environ.items():
        if key.startswith(prefix):
            name = key.removeprefix(prefix)
            secret_key = f"OPENSKY_CLIENT_SECRET_{name}"
            s = os.environ.get(secret_key)
            if s and not _already_registered(accounts, value, s):
                accounts.append({"name": name, "id": value, "secret": s})

    return accounts


def _already_registered(
    accounts: list[dict[str, str]], cid: str, secret: str
) -> bool:
    """Evita duplicados cuando la cuenta sin sufijo coincide con una nombrada."""
    return any(a["id"] == cid and a["secret"] == secret for a in accounts)


def get_storage_options() -> dict[str, str] | None:
    """Configuración para almacenamiento remoto Delta Lake.

    Soporta dos backends:

    **Azure Blob Storage / ADLS Gen2** (prioridad máxima)
    Variables requeridas:
      - ``AZURE_STORAGE_ACCOUNT_NAME``
      - ``AZURE_STORAGE_ACCESS_KEY`` (o ``AZURE_STORAGE_SAS_TOKEN``)
    URI esperada en ``OPENSKY_DELTA_ROOT``:
      ``abfss://<container>@<account>.dfs.core.windows.net``

    **S3-compatible** (MinIO, Scaleway, Backblaze B2…)
    Variables requeridas:
      - ``S3_ENDPOINT_URL``
      - ``S3_ACCESS_KEY_ID`` / ``S3_SECRET_ACCESS_KEY``

    **Cloudflare R2** (prioridad 3)
    Variables requeridas:
      - ``R2_ENDPOINT_URL``
      - ``R2_ACCESS_KEY_ID`` / ``R2_SECRET_ACCESS_KEY``

    Si no hay ninguna configurada, devuelve ``None`` (modo almacenamiento local).
    """
    # --- Prioridad 1: Azure ---
    account = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
    access_key = os.environ.get("AZURE_STORAGE_ACCESS_KEY")
    sas_token = os.environ.get("AZURE_STORAGE_SAS_TOKEN")
    if account and (access_key or sas_token):
        opts: dict[str, str] = {"AZURE_STORAGE_ACCOUNT_NAME": account}
        if access_key:
            opts["AZURE_STORAGE_ACCESS_KEY"] = access_key
        if sas_token:
            opts["AZURE_STORAGE_SAS_TOKEN"] = sas_token
        return opts

    # --- Prioridad 2: S3 (MinIO, Scaleway, Backblaze B2…) ---
    endpoint = os.environ.get("S3_ENDPOINT_URL")
    if endpoint:
        return {
            "AWS_ENDPOINT_URL": endpoint,
            "AWS_ACCESS_KEY_ID": os.environ.get("S3_ACCESS_KEY_ID", ""),
            "AWS_SECRET_ACCESS_KEY": os.environ.get("S3_SECRET_ACCESS_KEY", ""),
            "AWS_REGION": "auto",
            "aws_conditional_put": "etag",
        }

    # --- Prioridad 3: Cloudflare R2 (S3-compatible) ---
    r2_endpoint = os.environ.get("R2_ENDPOINT_URL")
    if r2_endpoint:
        return {
            "AWS_ENDPOINT_URL": r2_endpoint,
            "AWS_ACCESS_KEY_ID": os.environ.get("R2_ACCESS_KEY_ID", ""),
            "AWS_SECRET_ACCESS_KEY": os.environ.get("R2_SECRET_ACCESS_KEY", ""),
            "AWS_REGION": "auto",
            "aws_conditional_put": "etag",
        }

    return None


def get_mongo_uri() -> str:
    """URI de conexión a MongoDB para la capa silver.

    Por defecto apunta al contenedor Docker local.
    """
    return os.environ.get("MONGODB_URI", "mongodb://localhost:27017/aeropredict")


def get_postgres_uri() -> str:
    """URI de conexión a PostgreSQL para la capa gold.

    Por defecto apunta al contenedor Docker local.
    """
    return os.environ.get(
        "POSTGRES_URI",
        "postgresql://aeropredict:aeropredict@localhost:5432/aeropredict",
    )


# ---------------------------------------------------------------------------
# Configuración de fuentes externas (schedules, weather, aircraft)
# ---------------------------------------------------------------------------


def get_aviationstack_api_key() -> str:
    """API key de AviationStack (free tier: 100 req/mes)."""
    return os.environ.get("AVIATIONSTACK_API_KEY", "")


def get_aviationstack_keys() -> list[str]:
    """Descubre todas las AviationStack API keys con patrón de sufijo.

    Busca ``AVIATIONSTACK_API_KEY_{NAME}`` además de la variable base.
    Útil para Pool rotation entre múltiples cuentas.
    """
    keys: list[str] = []
    base = os.environ.get("AVIATIONSTACK_API_KEY")
    if base:
        keys.append(base)
    prefix = "AVIATIONSTACK_API_KEY_"
    for key, value in os.environ.items():
        if key.startswith(prefix) and value and value not in keys:
            keys.append(value)
    return keys


def get_aerodatabox_key() -> str:
    """API key de AeroDataBox (via RapidAPI / API.Market).

    Busca AERODATABOX_API_KEY primero, luego AERODATABOX_TOKEN_PABLO,
    y finalmente cualquier AERODATABOX_TOKEN_* como fallback.
    """
    key = os.environ.get("AERODATABOX_API_KEY", "")
    if key:
        return key
    # Fallback: suffixed token (e.g. AERODATABOX_TOKEN_PABLO)
    for env_name, val in os.environ.items():
        if env_name.startswith("AERODATABOX_TOKEN_") and val.strip():
            return val.strip()
    return ""


def get_aerodatabox_keys() -> list[str]:
    """Todas las API keys de AeroDataBox disponibles (para Pool rotation)."""
    keys = []
    seen = set()
    primary = get_aerodatabox_key()
    if primary:
        keys.append(primary)
        seen.add(primary)
    for env_name, val in os.environ.items():
        if env_name.startswith("AERODATABOX_TOKEN_") and val.strip() and val.strip() not in seen:
            keys.append(val.strip())
            seen.add(val.strip())
    return keys


def get_opensky_aircraft_db_path() -> str:
    """Ruta al archivo CSV de la base de datos de aeronaves de OpenSky."""
    return os.environ.get("OPENSKY_AIRCRAFT_DB_PATH", "data/aircraft_db.csv")


def get_delta_root() -> str:
    """Ruta base para tablas Delta. Por defecto data/raw/."""
    return os.environ.get("OPENSKY_DELTA_ROOT", "data/raw")


def get_bbox() -> BoundingBox:
    """Bounding box desde variables de entorno o el de España por defecto."""
    lamin: str | None = os.environ.get("OPENSKY_BBOX_LAMIN")
    lamax: str | None = os.environ.get("OPENSKY_BBOX_LAMAX")
    lomin: str | None = os.environ.get("OPENSKY_BBOX_LOMIN")
    lomax: str | None = os.environ.get("OPENSKY_BBOX_LOMAX")
    if lamin is not None and lamax is not None and lomin is not None and lomax is not None:
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
