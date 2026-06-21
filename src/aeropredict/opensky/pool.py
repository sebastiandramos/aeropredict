"""Pool genérico con rotación automática entre items de respaldo.

Cuando un item falla con una excepción que cumple la condición
``should_rotate``, el pool rota automáticamente al siguiente item.

Uso::

    # Pool de clientes HTTP que rotan en 429
    pool = Pool(
        items=[Client(token) for token in tokens],
        should_rotate=lambda e: isinstance(e, HTTPError) and e.response.status_code == 429,
        labels=tokens,
    )
    data = pool.execute(lambda c: c.get("/flights/arrival", params={...}))
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class Pool[T]:
    """Administra N items rotando cuando uno falla bajo cierta condición.

    Args:
        items: Lista de items a rotar.
        should_rotate: Callable que recibe la excepción y devuelve ``True``
            si se debe rotar al siguiente item.
        labels: Etiquetas opcionales para cada item (usadas en logs).
            Por defecto: índices numéricos.
    """

    def __init__(
        self,
        items: list[T],
        should_rotate: Callable[[Exception], bool],
        labels: list[str] | None = None,
    ) -> None:
        if not items:
            raise ValueError("Se necesita al menos un item en el Pool")

        self._items = items
        self._should_rotate = should_rotate
        self._labels = labels or [str(i) for i in range(len(items))]
        self._current: int = 0

    # ------------------------------------------------------------------
    # Propiedades públicas
    # ------------------------------------------------------------------

    @property
    def items(self) -> list[T]:
        """Lista completa de items."""
        return self._items

    @property
    def labels(self) -> list[str]:
        """Etiquetas de cada item."""
        return self._labels

    @property
    def current_index(self) -> int:
        """Índice del item activo actualmente."""
        return self._current

    @current_index.setter
    def current_index(self, idx: int) -> None:
        """Establece el item activo."""
        self._current = idx % len(self._items)

    @property
    def current_label(self) -> str:
        """Etiqueta del item activo actualmente."""
        return self._labels[self._current]

    @property
    def item_count(self) -> int:
        """Número total de items."""
        return len(self._items)

    # ------------------------------------------------------------------
    # Método público principal
    # ------------------------------------------------------------------

    def execute(
        self,
        fn: Callable[[T], Any],
        context: str = "",
    ) -> Any:
        """Ejecuta ``fn`` con rotación automática en caso de fallo.

        Intenta con el item activo. Si falla con una excepción que cumple
        ``should_rotate``, rota al siguiente item. Si todos fallan, lanza
        RuntimeError.

        Args:
            fn: Función a ejecutar con cada item.
            context: Descripción opcional del contexto (para logs).

        Returns:
            Resultado de ``fn`` ejecutado sobre el item que funcionó.

        Raises:
            RuntimeError: Si todos los items fallaron con excepciones
                rotables.
            Exception: Cualquier excepción no rotable se propaga
                inmediatamente.
        """
        start_idx = self._current
        n = self.item_count
        ctx = f" ({context})" if context else ""

        for attempt in range(n):
            idx = (start_idx + attempt) % n
            item = self._items[idx]
            label = self._labels[idx]

            try:
                result = fn(item)
                # Actualizar puntero al item que funcionó
                if idx != self._current:
                    logger.info("Pool rotado a %s como activo%s", label, ctx)
                    self._current = idx
                return result

            except Exception as e:
                if self._should_rotate(e):
                    logger.warning(
                        "Pool: %s falló (%s: %s), rotando%s...",
                        label,
                        type(e).__name__,
                        e,
                        ctx,
                    )
                    continue
                # Excepción no rotable → propagar
                raise

        # Todos los items agotados
        labels_used = ", ".join(self._labels)
        msg = (
            f"Todos los items del Pool fallaron{ctx}. "
            f"Items: [{labels_used}]"
        )
        logger.error(msg)
        raise RuntimeError(msg)
