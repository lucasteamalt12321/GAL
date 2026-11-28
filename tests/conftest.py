"""Общие настройки тестов.

Лендинг намеренно обращается к Supabase за топами, но в тестах реальная сеть
недоступна и каждый запрос ждал бы таймаут. По умолчанию подставляем пустые
данные; тесты, которым нужны строки, переопределяют сервис сами.
"""

import pytest
from postgrest.exceptions import APIError

from app.main import app  # noqa: F401  (гарантирует импорт приложения)


@pytest.fixture(autouse=True)
def _service_client_without_network(monkeypatch):
    """По умолчанию тесты работают с БД без миграции 036.

    RPC отсутствует, таблица реестра тоже — сервис легитимно уходит в
    legacy-путь. Тесты самого RPC переопределяют `new_service_client` локально.
    """
    from app.services import auth as auth_service

    class _RpcResult:
        def execute(self):
            raise APIError(
                {
                    "code": "PGRST202",
                    "message": (
                        "Could not find the function "
                        "public.submit_completion_with_proofs"
                    ),
                }
            )

    class _FingerprintsTable:
        def select(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            raise APIError(
                {
                    "code": "42P01",
                    "message": "relation public.submission_fingerprints does not exist",
                }
            )

    class _IdentitiesTable:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            raise APIError(
                {
                    "code": "42P01",
                    "message": "relation public.external_identities does not exist",
                }
            )

    class _Pre036ServiceClient:
        def rpc(self, name, params=None):
            if name != "submit_completion_with_proofs":
                raise AssertionError(f"unexpected RPC {name}")
            return _RpcResult()

        def table(self, name):
            if name == "submission_fingerprints":
                return _FingerprintsTable()
            if name == "external_identities":
                return _IdentitiesTable()
            raise AssertionError(f"unexpected table {name}")

    monkeypatch.setattr(auth_service, "new_service_client", _Pre036ServiceClient)


@pytest.fixture(autouse=True)
def _landing_without_network(monkeypatch):
    from app import main as main_module
    from app.services import completions as completions_service
    from app.services import rankings as rankings_service

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
    monkeypatch.setattr(rankings_service, "max_ranked_bt_rating", lambda: 1000.0)
    # Публичные списки прошедших читаются через RPC; в тестах без реальной
    # сети по умолчанию пусты. Конкретные тесты переопределяют их сами.
    monkeypatch.setattr(
        completions_service, "list_achievement_completers", lambda *_: []
    )
    monkeypatch.setattr(
        completions_service, "list_user_completed_achievements", lambda *_: []
    )
