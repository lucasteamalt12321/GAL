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
    """Последнее определение функции в порядке применения миграций.

    Учитываются оба варианта: `create or replace function` и `create function`
    после `drop function` (так пересозданы публичные RPC в 026/035).
    """
    markers = (
        f"create or replace function public.{name}(",
        f"create function public.{name}(",
    )
    latest = ""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for marker in markers:
            for match in re.finditer(re.escape(marker), sql, flags=re.IGNORECASE):
                end = sql.find("$$;", match.start())
                assert end != -1, f"Unterminated function body in {path.name}"
                latest = sql[match.start() : end + 3]
    assert latest, f"Function {name} is not defined in migrations"
    return _collapse(latest)


def test_migration_numbers_are_unique() -> None:
    numbers = [path.name.split("_", 1)[0] for path in MIGRATIONS.glob("*.sql")]
    assert len(numbers) == len(set(numbers))


def test_public_proofs_respect_completion_visibility() -> None:
    for name in ("achievement_completers", "user_completed_achievements"):
        body = _latest_function(name)

        assert "c.proofs_visible" in body
        assert "c.user_id = auth.uid()" in body
        assert "public.is_moderator()" in body


def test_public_completion_comment_follows_proof_visibility() -> None:
    """Комментарий к прохождению виден ровно там же, где доказательства."""
    for name in ("achievement_completers", "user_completed_achievements"):
        body = _latest_function(name)

        assert "comment text" in body
        assert "then c.comment" in body
        # Колонка в select-list (CASE) и в group by.
        assert body.count("c.comment") >= 2


def test_completion_comment_rpcs_are_owner_scoped() -> None:
    """Комментарий и откат вложений пишет только владелец pending-заявки."""
    comment = _latest_function("set_completion_comment")
    assert "security definer" in comment
    assert "auth.uid()" in comment
    assert "c.user_id = v_uid" in comment
    assert "c.status = 'pending'" in comment
    assert "raise exception 'auth_required'" in comment
    assert "revoke execute on function public.set_completion_comment" in _collapse(
        (MIGRATIONS / "035_completion_comment.sql").read_text(encoding="utf-8")
    )

    rollback = _latest_function("remove_completion_proofs")
    assert "security definer" in rollback
    assert "c.user_id = v_uid" in rollback
    assert "c.status = 'pending'" in rollback
    assert "pr.id = any(p_proof_ids)" in rollback


def test_creator_proof_visibility_cannot_be_hidden() -> None:
    migration = _collapse(
        (MIGRATIONS / "028_proof_visibility.sql").read_text(encoding="utf-8")
    )
    assert (
        "add column if not exists proofs_visible boolean not null default true"
        in migration
    )
    assert "create trigger achievement_completions_creator_visibility" in migration

    guard = _latest_function("force_creator_proof_visibility")
    assert "new.user_id = v_creator" in guard
    assert "new.proofs_visible := true" in guard


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


def test_submit_evaluation_clamps_position_to_other_count() -> None:
    body = _latest_function("submit_evaluation")
    # Кламп по числу других оценок, а не по максимуму до сжатия шкалы:
    # иначе редактирование не последней оценки оставляло дыру в позиции.
    assert "v_max_pos" not in body
    assert "v_other_count" in body
    assert "select count(*)" in body


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


def test_top5_application_guard_enforces_independence() -> None:
    guard = _latest_function("top5_application_guard")

    assert "raise exception 'self_decision'" in guard
    assert "raise exception 'reason_required'" in guard
    assert "raise exception 'already_permanent'" in guard
    assert "raise exception 'already_in_top5'" in guard
    assert "raise exception 'author_cannot_decide'" in guard
    assert "raise exception 'terminal_application'" in guard
    assert "new.status := 'pending'" in guard


def test_top5_evaluation_guard_rejects_author_and_uninvited() -> None:
    guard = _latest_function("top5_evaluation_guard")

    assert "new.evaluator_id in (v_applicant, v_creator)" in guard
    assert "raise exception 'not_independent'" in guard
    assert "raise exception 'not_invited'" in guard
    assert "raise exception 'not_moderator'" in guard
    assert "raise exception 'evaluation_immutable'" in guard
    assert "set status = 'submitted'" in guard

    bodies = " ".join(
        _collapse(path.read_text(encoding="utf-8"))
        for path in sorted(MIGRATIONS.glob("*.sql"))
    )
    assert "unique (application_id, evaluator_id)" in bodies
    assert "unique (application_id, expert_id)" in bodies


def test_top5_converts_on_third_ordinary_evaluation() -> None:
    body = _latest_function("top5_convert_after_third_evaluation")

    assert "if v_count < 3 then" in body
    assert "status = 'converted'" in body
    assert "delete from public.achievement_temporary_ratings" in body
    assert "perform public.recompute_ranks_internal()" in body
    assert "converted_at = now()" in body


def test_top5_temporary_only_before_third_evaluation() -> None:
    bodies = " ".join(
        _collapse(path.read_text(encoding="utf-8"))
        for path in sorted(MIGRATIONS.glob("*.sql"))
    )

    assert "if v_count >= 3 then" in bodies
    assert "raise exception 'already_permanent'" in bodies


def test_public_achievements_view_has_shared_top_markers() -> None:
    sql = _collapse(
        (MIGRATIONS / "027_top5_temporary_rating.sql").read_text(encoding="utf-8")
    )

    assert "create view public.public_achievements as" in sql
    assert "a.rank as permanent_rank" in sql
    assert "n.display_rank as rank" in sql
    assert "as is_temporary" in sql
    assert "as in_leaderboard" in sql
    assert (
        "coalesce( case when b.evaluation_count < 3 then t.strength end, "
        "b.bt_strength ) as sort_strength"
    ) in sql
    assert "row_number() over" in sql
    assert "coalesce(n.temporary_rating, n.bt_rating) as bt_rating" in sql


def test_apply_temporary_ratings_is_service_role_only() -> None:
    body = _latest_function("apply_temporary_ratings")

    assert "security definer" in body
    assert "auth.role()" in body
    assert "service_role" in body
    assert "pg_advisory_xact_lock" in body
    assert "delete from public.achievement_temporary_ratings" in body
    assert "app.status = 'approved'" in body
    assert "a.evaluation_count < 3" in body


def test_apply_temporary_ratings_merges_instead_of_wiping() -> None:
    # 030: stale payload не должен стирать свежие строки одобренных заявок.
    body = _latest_function("apply_temporary_ratings")

    assert "on conflict (achievement_id) do update" in body
    assert "delete from public.achievement_temporary_ratings tr" in body
    assert "where not exists" in body
    assert "(a.rank is null or a.rank > 5)" in body
    # Полная очистка кэша (delete-all) заменена адресным удалением.
    assert "delete from public.achievement_temporary_ratings;" not in body


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


def test_top5_completion_claim_column_and_index() -> None:
    # 031: заявка связывается с выполнением через completion_id.
    migration = _collapse(
        (MIGRATIONS / "031_top5_completion_claims.sql").read_text(encoding="utf-8")
    )

    assert (
        "add column if not exists completion_id bigint "
        "references public.achievement_completions (id) on delete set null"
    ) in migration
    assert (
        "create index if not exists top5_applications_completion_idx "
        "on public.top5_applications (completion_id) where completion_id is not null"
    ) in migration


def test_top5_evidence_url_is_optional() -> None:
    # 031: доказательство заявки — proof выполнения, evidence_url необязателен.
    migration = _collapse(
        (MIGRATIONS / "031_top5_completion_claims.sql").read_text(encoding="utf-8")
    )

    assert "alter column evidence_url drop not null" in migration
    assert "drop constraint if exists top5_applications_evidence_scheme" in migration
    assert (
        "check (evidence_url is null or evidence_url = '' "
        "or evidence_url ~* '^https?://[^[:space:]]+$')"
    ) in migration


def test_top5_application_guard_allows_completion_owner() -> None:
    guard = _latest_function("top5_application_guard")

    # INSERT: автор достижения ИЛИ владелец выполнения из completion_id.
    assert "public.achievement_completions" in guard
    assert "c.id = new.completion_id" in guard
    assert "c.user_id = v_actor" in guard
    assert "c.achievement_id = new.achievement_id" in guard
    assert "raise exception 'not_author'" in guard
    assert "raise exception 'already_permanent'" in guard
    assert "raise exception 'already_in_top5'" in guard
    assert "raise exception 'self_decision'" in guard
    assert "raise exception 'reason_required'" in guard
    # UPDATE: completion_id неизменяем, как achievement_id/applicant_id.
    assert "new.completion_id is distinct from old.completion_id" in guard


def test_top5_insert_policy_allows_completion_owner() -> None:
    sql = _collapse(
        (MIGRATIONS / "031_top5_completion_claims.sql").read_text(encoding="utf-8")
    )

    start = sql.find("create policy top5_applications_insert")
    assert start != -1
    policy = sql[start:]
    policy = policy[: policy.find(";")]
    assert "drop policy if exists top5_applications_insert" in sql
    assert "applicant_id = auth.uid()" in policy
    assert "a.creator_id = auth.uid()" in policy
    assert "achievement_completions" in policy
    assert "c.id = completion_id" in policy
    assert "c.user_id = auth.uid()" in policy
    assert "c.achievement_id = a.id" in policy


def test_top5_legacy_claims_backfilled() -> None:
    migration = _collapse(
        (MIGRATIONS / "031_top5_completion_claims.sql").read_text(encoding="utf-8")
    )

    assert (
        "update public.top5_applications a set completion_id = c.id "
        "from public.achievement_completions c"
    ) in migration
    assert "a.completion_id is null" in migration
    assert "a.status in ('pending', 'expert_review')" in migration
    assert "c.achievement_id = a.achievement_id" in migration
    assert "c.user_id = a.applicant_id" in migration
    assert "c.status = 'approved'" in migration
