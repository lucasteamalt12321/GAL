"""Статические инварианты итоговой SQL-функции после всех миграций.

Тесты не требуют PostgreSQL: они собирают последнее определение функции в
порядке применения миграций. Это ловит регрессию, когда новая миграция
переопределяет функцию устаревшей версией (как 020_reduce_evaluation_threshold).
"""

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _collapse(sql: str) -> str:
    return " ".join(sql.split()).lower()


def _latest_function(name: str) -> str:
    marker = f"create or replace function public.{name}("
    latest = ""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for match in re.finditer(re.escape(marker), sql, flags=re.IGNORECASE):
            end = sql.find("$$;", match.start())
            assert end != -1, f"Unterminated function body in {path.name}"
            latest = sql[match.start() : end + 3]
    assert latest, f"Function {name} is not defined in migrations"
    return _collapse(latest)


def test_migration_numbers_are_unique() -> None:
    numbers = [path.name.split("_", 1)[0] for path in MIGRATIONS.glob("*.sql")]
    assert len(numbers) == len(set(numbers))


def test_recompute_ranks_uses_bt_and_two_phase_bump() -> None:
    body = _latest_function("recompute_ranks_internal")

    assert "security definer" in body
    assert "set rank = rank + 1000000000" in body
    assert (
        "order by bt_strength desc nulls last, average_position asc, "
        "rank_order asc, id asc"
    ) in body


def test_submit_evaluation_keeps_live_evaluation_invariants() -> None:
    body = _latest_function("submit_evaluation")

    assert "security definer" in body
    assert "pg_advisory_xact_lock" in body
    assert "raise exception 'not_found'" in body
    assert "locked = false" in body
    assert "do update set position = excluded.position, locked = false" in body
    assert "if v_count >= 2 and v_rstatus <> 'ranked' then" in body
    assert "perform public.recompute_ranks_internal()" in body
    assert "e.locked = false" not in body


def test_bt_uncertainty_fields_are_guarded_and_stored() -> None:
    guard = _latest_function("guard_ranking_fields")
    for field in (
        "bt_rating_se",
        "bt_rating_ci_low",
        "bt_rating_ci_high",
    ):
        assert f"new.{field} := null" in guard
        assert f"new.{field} := old.{field}" in guard

    apply = _latest_function("apply_bt_ratings")
    for field in (
        "se double precision",
        "ci_low double precision",
        "ci_high double precision",
    ):
        assert field in apply
    for field in (
        "bt_rating_se = r.se",
        "bt_rating_ci_low = r.ci_low",
        "bt_rating_ci_high = r.ci_high",
    ):
        assert field in apply


def test_bt_uncertainty_columns_are_declared() -> None:
    bodies = " ".join(
        _collapse(path.read_text(encoding="utf-8"))
        for path in sorted(MIGRATIONS.glob("*.sql"))
    )
    for column in ("bt_rating_se", "bt_rating_ci_low", "bt_rating_ci_high"):
        assert f"add column if not exists {column} double precision" in bodies


def test_completion_evaluation_uses_two_evaluations() -> None:
    body = _latest_function("apply_completion_evaluation")

    assert "security definer" in body
    assert "if v_count >= 2 and v_rstatus <> 'ranked' then" in body
    assert (
        "insert into public.evaluations (achievement_id, user_id, position, locked) "
        "values (v_aid, v_uid, v_new_pos, false)"
    ) in body
    assert "perform public.recompute_ranks_internal()" in body
    assert "e.locked = false" not in body
