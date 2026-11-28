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


def test_submission_rpc_never_writes_null_proof_description() -> None:
    """Регресс 038: proofs.description NOT NULL, а RPC писал в него NULL.

    Пустое описание доказательства (обычный случай — форма не спрашивает
    описание файла) превращалось в NULL и вся отправка падала с SQLSTATE 23502.
    Теперь пустое описание сохраняется пустой строкой.
    """
    body = _latest_function("submit_completion_with_proofs")

    assert (
        "coalesce(nullif(btrim(coalesce(item->>'description', '')), ''), '')" in body
    ), "описание доказательства должно сохраняться пустой строкой, а не NULL"
    # Не должно остаться прежней формы, где описание уходит в NULL.
    assert "nullif(btrim(coalesce(item->>'description', '')), ''), case" not in body


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


def _migration(number: str) -> str:
    paths = list(MIGRATIONS.glob(f"{number}_*.sql"))
    assert len(paths) == 1, f"Expected one migration {number}, got {paths}"
    return _collapse(paths[0].read_text(encoding="utf-8"))


# +------------------------ миграция 036 (D25) ------------------------+


def test_036_registry_separates_submission_types() -> None:
    """Прохождение, топ-5, достижение и жалоба не конфликтуют между собой."""
    sql = _migration("036")
    assert "unique (submission_type, submitter_id, fingerprint)" in sql
    assert "submission_type in ('achievement', 'completion', 'top5', 'report')" in sql
    assert "alter table public.submission_fingerprints enable row level security" in sql

    body = _latest_function("submission_fingerprint")
    assert "'algorithm_version', 1" in body
    assert "'submission_type', p_type" in body
    assert "'submitter_id', p_submitter_id" in body


def test_036_duplicate_registration_is_serialized_and_reported() -> None:
    body = _latest_function("register_submission_fingerprint")
    assert "pg_advisory_xact_lock" in body
    assert "p_type || ':' || p_submitter_id::text || ':' || v_fingerprint" in body
    assert "for update" in body
    assert "errcode = '23505'" in body
    assert "message = 'duplicate_submission'" in body
    # Повтор той же попытки разрешён только при редактировании achievement;
    # completion/top5/report всегда создают новую попытку.
    assert "p_type = 'achievement' or p_attempt_no = v_last_attempt" in body
    assert "p_previous_fingerprint" in body


def test_036_triggers_cover_all_client_writes() -> None:
    sql = _migration("036")
    assert (
        "before insert or update of title, description, requirements, category_id"
        in sql
    )
    assert "before insert on public.top5_applications" in sql
    assert "create trigger zz_top5_applications_fingerprint" in sql
    assert "before insert on public.reports" in sql


def test_036_completion_rpc_is_service_only_and_atomic() -> None:
    body = _latest_function("submit_completion_with_proofs")
    assert "auth.role() <> 'service_role'" in body
    assert "message = 'proof_required'" in body
    assert "pg_advisory_xact_lock" in body
    assert "p_user_id::text || ':' || p_achievement_id::text" in body
    assert "for update" in body
    assert "submission_attempt = v_attempt" in body
    assert "delete from public.proofs p where p.completion_id = v_completion_id" in body
    assert "register_submission_fingerprint(" in body
    assert "return query select v_completion_id, v_attempt" in body


def test_036_proof_hash_trigger_validates_links_and_files() -> None:
    body = _latest_function("set_proof_content_hash")
    assert "^https?://[^[:space:]]+$" in body
    assert "message = 'invalid_proof_url'" in body
    assert "message = 'proof_hash_required'" in body
    assert "message = 'invalid_proof_path'" in body
    assert "'^[0-9a-f]{64}$'" in body


def test_036_hash_trigger_is_created_after_backfill() -> None:
    """Иначе обнуление legacy-дублей уперлось бы в PROOF_HASH_REQUIRED."""
    sql = _migration("036")
    assert sql.index("create trigger proofs_content_hash") > sql.index(
        "update public.proofs p"
    )
    assert sql.index("create trigger proofs_content_hash") > sql.index(
        "set submission_attempt = greatest(c.submission_attempt, 1)"
    )


def test_036_backfill_registers_every_existing_attempt() -> None:
    sql = _migration("036")
    assert "set submission_attempt = greatest(c.submission_attempt, 1)" in sql
    assert "from public.achievements a ) as computed" in sql
    assert "from public.achievement_completions c ) as computed" in sql
    assert "from public.top5_applications a ) as computed" in sql
    assert "from public.reports r ) as computed" in sql
    assert "on conflict (submission_type, submitter_id, fingerprint) do nothing" in sql


def test_036_client_cannot_forge_registry_or_bypass_rpc() -> None:
    sql = _migration("036")
    assert (
        "revoke all on table public.submission_fingerprints from anon, authenticated"
        in sql
    )
    assert (
        "grant select, insert, update on table public.submission_fingerprints "
        "to service_role" in sql
    )
    assert (
        "revoke insert on table public.achievement_completions from anon, authenticated"
        in sql
    )
    assert "revoke insert on table public.proofs from anon, authenticated" in sql
    assert "grant execute on function public.submit_completion_with_proofs" in sql
    # Старые клиентские RPC отзываются по фактическим сигнатурам окружения.
    for name in (
        "reset_rejected_completion",
        "set_completion_comment",
        "remove_completion_proofs",
    ):
        assert f"'{name}'" in sql
    assert "pg_get_function_identity_arguments(p.oid)" in sql
    assert "notify pgrst, 'reload schema'" in sql


def test_036_refresh_helper_is_service_role_only() -> None:
    body = _latest_function("refresh_legacy_submission_fingerprints")
    assert "auth.role() <> 'service_role'" in body
    assert "f.canonical_payload::text like '%legacy-path:%'" in body
    assert "on conflict (submission_type, submitter_id, fingerprint) do nothing" in body


# +------------------------ миграция 039 (D28) ------------------------+


def test_039_accepts_comment_without_proofs() -> None:
    """Заявка живёт, если есть хотя бы файл, ссылка или комментарий."""
    body = _latest_function("submit_completion_with_proofs")

    assert "left(coalesce(p_comment, ''), 2000)" in body
    assert "regexp_replace" in body and "'[[:space:]]'" in body
    assert "v_has_comment := nullif(" in body
    assert "if jsonb_array_length(p_proofs) = 0 and not v_has_comment then" in body
    # Отказ остался прежним маркером: приложение маппит его в понятный текст.
    assert "message = 'proof_required'" in body


def test_039_keeps_submission_rpc_service_only_and_atomic() -> None:
    body = _latest_function("submit_completion_with_proofs")

    assert "auth.role() <> 'service_role'" in body
    assert "pg_advisory_xact_lock" in body
    assert "delete from public.proofs p where p.completion_id = v_completion_id" in body
    assert "register_submission_fingerprint(" in body
    assert "return query select v_completion_id, v_attempt" in body


def test_039_does_not_touch_creator_proof_invariant() -> None:
    """Публикация по-прежнему требует доказательство автора (миграция 013)."""
    sql = _migration("039")

    # 039 переопределяет только RPC отправки: схема, RLS и триггеры не трогаем.
    for forbidden in ("alter table", "drop trigger", "drop policy", "create policy"):
        assert forbidden not in sql, forbidden
    assert (
        "create or replace function public.enforce_creator_proof_on_publish" not in sql
    )
    assert "begin" in sql and "commit" in sql
    assert "revoke execute on function public.submit_completion_with_proofs" in sql
    assert ") to service_role" in sql

    guard = _latest_function("enforce_creator_proof_on_publish")
    assert "public.proofs" in guard


# +------------------------ миграция 040 (D29) ------------------------+


def test_040_player_search_is_literal_case_insensitive_and_public() -> None:
    sql = _migration("040")

    assert "security definer" in sql
    assert "set search_path = public" in sql
    assert "strpos(lower(r.username), lower(btrim(p_query)))" in sql
    assert "strpos(lower(coalesce(r.display_name, '')), lower(btrim(p_query)))" in sql
    assert "order by f.score desc, f.username asc" in sql
    assert "limit least(greatest(p_limit, 1), 100)" in sql
    assert "count(*) over () as total_count" in sql
    assert "from public, anon, authenticated" in sql
    assert "to anon, authenticated" in sql
    assert "notify pgrst, 'reload schema'" in sql


def test_040_only_adds_search_rpc_for_pre_migration_fallback() -> None:
    sql = _migration("040")

    assert "create or replace function public.player_leaderboard_search_page(" in sql
    assert "create or replace function public.player_leaderboard()" not in sql
    assert "c.status = 'approved'" in sql
    assert "a.ranking_status = 'ranked'" in sql
    assert "round((1000.0 / a.rank)::numeric, 2)" in sql


# +------------------------ миграция 041 (D30) ------------------------+


def test_041_player_scores_use_global_bt_rating_scale() -> None:
    body = _latest_function("player_leaderboard_scores")

    assert "security definer" in body
    assert "set search_path = public" in body
    assert "max(a.bt_rating) over () as max_bt_rating" in body
    assert "a.status = 'published'" in body
    assert "a.ranking_status = 'ranked'" in body
    assert "a.rank is not null" in body
    assert "a.bt_rating is not null" in body
    assert "c.status = 'approved'" in body
    assert "1000.0 * power(10.0, (e.bt_rating - e.max_bt_rating) / 400.0)" in body
    assert "round(sum(s.points), 2)" in body
    assert "count(*)::integer" in body


def test_041_leaderboard_rpcs_share_scores_and_keep_contracts() -> None:
    legacy = _latest_function("player_leaderboard")
    page = _latest_function("player_leaderboard_page")
    search = _latest_function("player_leaderboard_search_page")

    for body in (legacy, page, search):
        assert "from public.player_leaderboard_scores() s" in body
        assert "security definer" in body
        assert "set search_path = public" in body

    assert "total_count bigint" in page
    assert "count(*) over () as total_count" in page
    assert "total_count bigint" in search
    assert "count(*) over () as total_count" in search
    assert "limit least(greatest(p_limit, 1), 100)" in search
    assert "strpos(lower(s.username), lower(btrim(p_query)))" in search
    assert (
        "strpos(lower(coalesce(s.display_name, '')), lower(btrim(p_query)))" in search
    )


def test_041_internal_score_helper_is_not_publicly_executable() -> None:
    sql = _migration("041")

    assert "revoke all on function public.player_leaderboard_scores()" in sql
    assert "from public, anon, authenticated, service_role" in sql
    assert "grant execute on function public.player_leaderboard_scores()" not in sql
    assert "notify pgrst, 'reload schema'" in sql
