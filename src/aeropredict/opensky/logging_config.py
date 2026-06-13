"""Configuración de logging para el script de extracción diaria.

Proporciona un logger preconfigurado con salida a archivo rotado
y a stderr, con formato estructurado y nivel configurable por
variable de entorno.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_DIR = "data/logs"
DEFAULT_LOG_FILE = "daily_extract.log"
LOG_FORMAT = "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S UTC"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5


def setup_daily_logger(
    name: str = "daily_extract",
    log_dir: str | None = None,
    log_file: str | None = None,
) -> logging.Logger:
    """Configura y devuelve un logger para el script diario.

    1. Configura el **root logger** con un StreamHandler a stderr, para que
       todos los módulos del paquete (client, client_pool, etc.) hereden el
       nivel y el formato correctamente.
    2. Añade un RotatingFileHandler específico para el logger ``name``.

    El nivel de logging se puede controlar con la variable de entorno
    OPENSKY_LOG_LEVEL (DEBUG, INFO, WARNING, etc.).  Por defecto INFO.

    Args:
        name: Nombre del logger.
        log_dir: Directorio de logs (default: data/logs/).
        log_file: Nombre del archivo de log (default: daily_extract.log).

    Returns:
        Logger configurado.
    """
    log_level_name = os.environ.get("OPENSKY_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level_name, logging.INFO)

    # ------------------------------------------------------------------
    # Root logger — todos los módulos (client, client_pool, daily_extract)
    # heredan de él.  Esto evita que los INFO de otros módulos se traguen.
    # ------------------------------------------------------------------
    root = logging.getLogger()
    root.setLevel(level)

    # Solo añadimos el handler de consola si el root aún no tiene ninguno
    if not root.handlers:
        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # ------------------------------------------------------------------
    # Logger específico del script de extracción (daily_extract)
    # Añade RotatingFileHandler.  Los mensajes ya se ven en consola
    # gracias al root logger → no necesita otro StreamHandler.
    # ------------------------------------------------------------------
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(level)

        log_path = Path(log_dir or DEFAULT_LOG_DIR)
        log_path.mkdir(parents=True, exist_ok=True)

        log_file_path = log_path / (log_file or DEFAULT_LOG_FILE)

        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

        file_handler = RotatingFileHandler(
            str(log_file_path),
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
