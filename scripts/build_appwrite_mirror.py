"""Собирает каталог с кодом для деплоя Appwrite Function-зеркала.

Исходный код приложения не дублируется: в staging-каталог копируется
текущий пакет `app/` (вместе с шаблонами и статикой), адаптер `main.py`
и отдельный requirements.txt без uvicorn (он не нужен внутри функции).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FUNCTION = ROOT / "appwrite" / "functions" / "gal-mirror"
DEFAULT_OUTPUT = ROOT / "appwrite" / "build" / "gal-mirror"

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".pytest_cache")


def build(destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    shutil.copytree(ROOT / "app", destination / "app", ignore=_IGNORE)
    shutil.copy2(FUNCTION / "main.py", destination / "main.py")
    shutil.copy2(FUNCTION / "requirements.txt", destination / "requirements.txt")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(build(args.out))


if __name__ == "__main__":
    main()
