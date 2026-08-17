"""
Mitigación de "Lost in the Middle" (LitM).

Los LLMs tienden a ignorar la información que cae en la parte media de su
ventana de contexto (curva de rendimiento en U: favorecen el inicio por
sesgo de primacía y el final por sesgo de recencia, y descuidan el medio).

Este módulo provee utilidades de reordenamiento para que, dado un contenido
ya rankeado por relevancia (de mayor a menor), lo más relevante quede en los
extremos del prompt y lo menos relevante en el centro.
"""

from __future__ import annotations

from typing import TypeVar

import config

T = TypeVar("T")


def reorder_lost_in_middle(items: list[T]) -> list[T]:
    """Reordena una lista rankeada (desc) en forma de U.

    El primer elemento (más relevante) queda al inicio, el segundo al final,
    el tercero en la segunda posición, y así sucesivamente. La relevancia
    decrece hacia el centro del contexto, que es donde el modelo menos atiende.
    """
    n = len(items)
    if n <= 2:
        return list(items)

    front: list[T] = []
    back: list[T] = []
    for i, item in enumerate(items):
        if i % 2 == 0:
            front.append(item)
        else:
            back.append(item)
    back.reverse()
    return front + back


def maybe_reorder(items: list[T]) -> list[T]:
    """Aplica el reordenamiento U solo si está habilitado (config.LITM_REORDER)."""
    if not getattr(config, "LITM_REORDER", True):
        return items
    return reorder_lost_in_middle(items)
