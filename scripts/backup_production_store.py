"""Copia y prueba de restauración de la biblioteca operativa de ClaudIA."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from production_store import store


if __name__ == "__main__":
    print(store.backup())
