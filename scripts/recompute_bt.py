"""Пересчитать Bradley–Terry рейтинги по текущим данным БД.

Запуск (нужны SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY):
    python -m scripts.recompute_bt
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import bt_recompute


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = bt_recompute.recompute_global_ratings()
    if summary is None:
        print("Skip: service role is not configured")
        return 1
    print(f"OK: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
