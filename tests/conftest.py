"""Общие настройки тестов.

Лендинг намеренно обращается к Supabase за топами, но в тестах реальная сеть
недоступна и каждый запрос ждал бы таймаут. По умолчанию подставляем пустые
данные; тесты, которым нужны строки, переопределяют сервис сами.
"""

import pytest

from app.main import app  # noqa: F401  (гарантирует импорт приложения)


@pytest.fixture(autouse=True)
def _landing_without_network(monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(
        main_module.rankings_service,
        "leaderboard_achievements",
        lambda **_: ([], 0),
    )
    monkeypatch.setattr(
        main_module.rankings_service,
        "player_leaderboard",
        lambda **_: ([], 0),
    )
