"""Limpia versiones artificiales creadas por importaciones ACJ repetidas."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from library_import import normalize_sii_import_versions


if __name__ == "__main__":
    print(normalize_sii_import_versions())
