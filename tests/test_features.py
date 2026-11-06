"""Тесты новых сценариев: ошибки, редактирование, настройки, модерация."""

from fastapi.testclient import TestClient

from app.main import app
from app.services import achievements as achievements_service
from app.services import auth as auth_service
from app.services import completions as completions_service
from app.services import evaluation as evaluation_service
from app.services import moderation as moderation_service
from app.services import profiles as profiles_service

client = TestClient(app)

MODERATOR = auth_service.CurrentUser(
    id="11111111-1111-1111-1111-111111111111",
    email="mod@gal.test",
    username="moder",
    display_name="Moder",
    avatar_url=None,
    role="moderator",
)
USER = auth_service.CurrentUser(
    id="22222222-2222-2222-2222-222222222222",
    email="user@gal.test",
    username="tester",
    display_name="Tester",
    avatar_url=None,
    role="user",
)


def _login(monkeypatch, user=USER) -> None:
    monkeypatch.setattr(auth_service, "resolve_user", lambda _request: user)


def _pending(creator_id: str = USER.id) -> dict:
    return {
        "id": 5,
        "title": "Pending",
        "description": "d",
        "requirements": "r",
        "status": "pending",
        "rank": None,
        "average_position": None,
        "evaluation_count": 0,
        "creator_id": creator_id,
        "category": None,
        "creator": None,
        "created_at": "2026-11-05T10:00:00+00:00",
    }


def test_404_page_is_html(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: None)
    resp = client.get("/achievements/999", headers={"accept": "text/html"})
    assert resp.status_code == 404
    assert "Страница не найдена" in resp.text
    assert "Вернуться на главную" not in resp.text


def test_403_page_is_html(monkeypatch) -> None:
    _login(monkeypatch, USER)
    resp = client.get("/reports", headers={"accept": "text/html"})
    assert resp.status_code == 403
    assert "Доступ запрещён" in resp.text


def test_nav_hides_moderator_links(monkeypatch) -> None:
    _login(monkeypatch, USER)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Жалобы" not in resp.text
    assert "Модерация" not in resp.text
    assert "Настройки" in resp.text


def test_nav_shows_moderator_links(monkeypatch) -> None:
    _login(monkeypatch, MODERATOR)
    resp = client.get("/")
    assert "Жалобы" in resp.text
    assert "Модерация" in resp.text


def _detail_stubs(monkeypatch) -> None:
    monkeypatch.setattr(
        completions_service, "get_user_completion", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        evaluation_service, "evaluate_context", lambda *_a, **_k: (False, None, None)
    )


def test_report_submitted_message(monkeypatch) -> None:
    _login(monkeypatch, USER)
    item = _pending()
    item["status"] = "published"
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    _detail_stubs(monkeypatch)
    resp = client.get("/achievements/5?info=report-submitted")
    assert resp.status_code == 200
    assert "Жалоба отправлена" in resp.text


def test_evaluation_reason_is_visible(monkeypatch) -> None:
    _login(monkeypatch, USER)
    item = _pending()
    item["status"] = "published"
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    monkeypatch.setattr(
        completions_service, "get_user_completion", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        evaluation_service,
        "evaluate_context",
        lambda *_a, **_k: (
            False,
            "Оценивать можно только подтверждённо выполненные достижения.",
            None,
        ),
    )
    resp = client.get("/achievements/5")
    assert "Оценивать можно только" in resp.text


def test_edit_page_requires_owner(monkeypatch) -> None:
    _login(monkeypatch, USER)
    other = _pending(creator_id="33333333-3333-3333-3333-333333333333")
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: other)
    resp = client.get("/achievements/5/edit")
    assert resp.status_code == 403


def test_edit_page_renders_for_creator(monkeypatch) -> None:
    _login(monkeypatch, USER)
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: _pending())
    monkeypatch.setattr(achievements_service, "list_categories", list)
    resp = client.get("/achievements/5/edit")
    assert resp.status_code == 200
    assert "Редактирование достижения" in resp.text


def test_edit_post_calls_service(monkeypatch) -> None:
    _login(monkeypatch, USER)
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: _pending())
    captured: list[dict] = []

    def _update(request, achievement_id, **kwargs):
        captured.append({"id": achievement_id, **kwargs})
        return {}

    monkeypatch.setattr(achievements_service, "update_achievement", _update)
    resp = client.post(
        "/achievements/5/edit",
        data={"title": "New title", "description": "d", "requirements": "r"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/5?info=updated"
    assert captured[0]["title"] == "New title"


def test_delete_own_pending_redirects(monkeypatch) -> None:
    _login(monkeypatch, USER)
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: _pending())
    called: list[int] = []
    monkeypatch.setattr(
        achievements_service,
        "soft_delete_own_pending",
        lambda _r, aid: called.append(aid),
    )
    resp = client.post("/achievements/5/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/users/tester?info=deleted"
    assert called == [5]


def test_settings_requires_auth() -> None:
    resp = client.get("/settings/profile", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login"


def test_settings_renders(monkeypatch) -> None:
    _login(monkeypatch, USER)
    resp = client.get("/settings/profile")
    assert resp.status_code == 200
    assert "Смена пароля" in resp.text
    assert 'value="tester"' in resp.text


def test_settings_profile_update(monkeypatch) -> None:
    _login(monkeypatch, USER)
    captured: list[dict] = []

    def _update(request, **kwargs):
        captured.append(kwargs)
        return {}

    monkeypatch.setattr(profiles_service, "update_profile", _update)
    resp = client.post(
        "/settings/profile",
        data={"username": "newname", "display_name": "New", "avatar_url": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/users/newname?info=profile-updated"
    assert captured[0]["username"] == "newname"


def test_password_mismatch_shows_error(monkeypatch) -> None:
    _login(monkeypatch, USER)
    resp = client.post(
        "/settings/password",
        data={
            "current_password": "oldpass",
            "new_password": "newpass1",
            "new_password_confirm": "newpass2",
        },
    )
    assert resp.status_code == 200
    assert "Пароли не совпадают" in resp.text


def test_update_password_page_renders() -> None:
    resp = client.get("/auth/update-password")
    assert resp.status_code == 200
    assert "Новый пароль" in resp.text
    assert "auth/v1/user" in resp.text


def test_moderation_hide_calls_service(monkeypatch) -> None:
    _login(monkeypatch, MODERATOR)
    captured: list[tuple] = []
    monkeypatch.setattr(
        moderation_service,
        "hide_achievement",
        lambda _r, aid, reason: captured.append((aid, reason)),
    )
    resp = client.post(
        "/moderation/achievements/5/hide",
        data={"reason": "spam"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/achievements/5?info=hidden"
    assert captured == [(5, "spam")]


def test_moderation_history_renders(monkeypatch) -> None:
    _login(monkeypatch, MODERATOR)
    monkeypatch.setattr(
        moderation_service,
        "list_history",
        lambda _r: [
            {
                "id": 1,
                "completion_id": None,
                "achievement_id": 5,
                "moderator_id": MODERATOR.id,
                "decision": "approved",
                "reason": "ok",
                "created_at": "2026-11-05T10:00:00+00:00",
                "moderator": {"username": "moder", "display_name": "Moder"},
                "target_title": "Sample",
                "target_kind": "achievement",
            }
        ],
    )
    resp = client.get("/moderation/history")
    assert resp.status_code == 200
    assert "История модерации" in resp.text
    assert "Sample" in resp.text
    assert "Одобрено" in resp.text


def test_list_page_redirects_to_last_page(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(achievements_service, "list_achievements", lambda **_: ([], 30))
    resp = client.get("/achievements?page=99", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/achievements?page=2")


def test_empty_catalog_out_of_range_redirects(monkeypatch) -> None:
    monkeypatch.setattr(achievements_service, "list_categories", list)
    monkeypatch.setattr(achievements_service, "list_achievements", lambda **_: ([], 0))
    resp = client.get("/achievements?page=2", follow_redirects=False)
    assert resp.status_code == 303
    assert "page=1" in resp.headers["location"]


def test_status_labels_are_human_readable(monkeypatch) -> None:
    _login(monkeypatch, USER)
    item = _pending()
    monkeypatch.setattr(achievements_service, "get_achievement", lambda *_: item)
    _detail_stubs(monkeypatch)
    resp = client.get("/achievements/5")
    assert "На модерации" in resp.text
    assert ">pending<" not in resp.text


def test_landing_shows_top_data(monkeypatch) -> None:
    from app import main as main_module

    monkeypatch.setattr(
        main_module.rankings_service,
        "leaderboard_achievements",
        lambda **_: (
            [
                {
                    "id": 1,
                    "rank": 1,
                    "title": "Top one",
                    "category": {"name": "Chess"},
                }
            ],
            1,
        ),
    )
    monkeypatch.setattr(
        main_module.rankings_service,
        "player_leaderboard",
        lambda **_: (
            [
                {
                    "username": "alice",
                    "display_name": "Alice",
                    "score": 1000,
                }
            ],
            1,
        ),
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Top one" in resp.text
    assert "Alice" in resp.text


def test_malformed_session_cookie_is_deleted(monkeypatch) -> None:
    def _resolve(request):
        auth_service._drop_auth_cookies(request)

    monkeypatch.setattr(auth_service, "resolve_user", _resolve)
    resp = client.get("/", cookies={auth_service.SESSION_COOKIE: "not-a-jwt"})
    cookies = resp.headers.get_list("set-cookie")
    assert any(
        c.startswith(f"{auth_service.SESSION_COOKIE}=") and "Max-Age=0" in c
        for c in cookies
    )


def test_supabase_not_configured_returns_503(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise auth_service.SupabaseNotConfiguredError("not configured")

    monkeypatch.setattr(achievements_service, "list_categories", _boom)
    resp = client.get("/achievements")
    assert resp.status_code == 503
    assert "временно недоступен" in resp.text
