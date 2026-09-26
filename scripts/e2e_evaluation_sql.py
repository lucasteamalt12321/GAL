from __future__ import annotations

import re
import sys
import uuid

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

MANAGE_API = "https://api.supabase.com/v1"


class _Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_access_token: str = ""


def _json_claims(sub: str) -> str:
    return '{"sub":"' + sub + '","role":"authenticated"}'


def _sql_create_user_api(env: _Env) -> dict:
    """Management-API channel: create the auth user directly via SQL. The
    on_auth_user_created trigger creates the profiles row synchronously."""
    uid = str(uuid.uuid4())
    email = f"e2e-{uuid.uuid4().hex[:10]}@gal-e2e.test"
    uname = f"e2e{uuid.uuid4().hex[:8]}"
    sql = f"""
insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    created_at, updated_at, confirmation_token, recovery_token,
    email_change_token_new, email_change
) values (
    '00000000-0000-0000-0000-000000000000', '{uid}', 'authenticated', 'authenticated',
    '{email}', 'e2e-dummy-hash', now(),
    '{{"provider":"email","providers":["email"]}}',
    '{{"username":"{uname}"}}',
    now(), now(), '', '', '', ''
);
select 1 from public.profiles where id = '{uid}';
"""
    resp = httpx.post(
        f"{MANAGE_API}/projects/{_project_ref(env)}/database/query",
        headers={"Authorization": f"Bearer {env.supabase_access_token}"},
        json={"query": sql},
        timeout=120,
    )
    if resp.status_code != 201 or resp.json() != [{"?column?": 1}]:
        raise RuntimeError(
            f"create user via SQL failed: {resp.status_code} {resp.text[:300]}"
        )
    return {"user_id": uid, "email": email, "uname": uname}


def _sql_delete_users(env: _Env, user_ids: list[str]) -> None:
    if not user_ids:
        return
    ids = ",".join(f"'{uid}'" for uid in user_ids)
    resp = httpx.post(
        f"{MANAGE_API}/projects/{_project_ref(env)}/database/query",
        headers={"Authorization": f"Bearer {env.supabase_access_token}"},
        json={"query": f"delete from auth.users where id in ({ids});"},
        timeout=120,
    )
    if resp.status_code != 201:
        print(f"cleanup users warning: {resp.status_code} {resp.text[:200]}")


def _run_sql(env: _Env, query: str, timeout: int = 180):
    return httpx.post(
        f"{MANAGE_API}/projects/{_project_ref(env)}/database/query",
        headers={"Authorization": f"Bearer {env.supabase_access_token}"},
        json={"query": query},
        timeout=timeout,
    )


def _project_ref(env: _Env) -> str:
    return re.search(r"//([^.]+)\.supabase\.co", env.supabase_url).group(1)


def build_payload(a: str, b: str, c: str, d: str, mod: str) -> str:
    j_a = _json_claims(a)
    j_d = _json_claims(d)
    return f"""
set local request.jwt.claims = '{{"role":"service_role"}}';

create table if not exists public._gal_e2e_results (
    kind text primary key,
    ok boolean not null,
    note text not null
);
grant select, insert on public._gal_e2e_results to anon, authenticated;
drop policy if exists _gal_e2e_all on public._gal_e2e_results;
create policy _gal_e2e_all on public._gal_e2e_results
    for all using (true) with check (true);
truncate public._gal_e2e_results;

do $$
declare
    v_a uuid := '{a}';
    v_b uuid := '{b}';
    v_c uuid := '{c}';
    v_d uuid := '{d}';
    v_mod uuid := '{mod}';
    v_cat bigint;
    v_ach bigint;
    v_cnt integer;
    v_avg double precision;
    v_st public.ranking_status;
    v_rnk integer;
    v_n bigint;
begin
    insert into public.categories (name, slug)
    values ('E2E-Cat', 'gale2e-cat')
    on conflict (name) do update set name = excluded.name
    returning id into v_cat;

    insert into public.achievements
        (title, description, requirements, category_id, creator_id, status, ranking_status)
    values ('E2E Achievement', 'e2e', 'e2e', v_cat, v_mod, 'published', 'unknown')
    returning id into v_ach;

    insert into public.achievement_completions (achievement_id, user_id, status)
    values (v_ach, v_a, 'approved'),
           (v_ach, v_b, 'approved'),
           (v_ach, v_c, 'approved');

    update public.profiles set role = 'moderator' where id = v_mod;
    perform set_config('request.jwt.claims', '{{"role":"service_role"}}', true);

    insert into public._gal_e2e_results values
        ('mod_role',
         exists (select 1 from public.profiles where id = v_mod and role = 'moderator'),
         'n/a')
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    -- A: первую оценку (якорь = позиция 1) даже при p_harder_count > 0
    perform set_config('request.jwt.claims', '{j_a}', true);
    perform public.submit_evaluation(v_ach, 5);
    select evaluation_count, average_position, ranking_status
      into v_cnt, v_avg, v_st from public.achievements where id = v_ach;
    insert into public._gal_e2e_results values
        ('a_first_anchor', v_cnt = 1 and v_avg = 1.0 and v_st = 'unknown',
         format('cnt=%s avg=%s st=%s', v_cnt, v_avg, v_st))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    select count(*) into v_n
      from public.evaluations
     where achievement_id = v_ach and user_id = v_a and position = 1 and locked = false;
    insert into public._gal_e2e_results values
        ('a_pos1_unlocked', v_n = 1, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    -- A: повторная оценка -> upsert, дублей нет
    perform public.submit_evaluation(v_ach, 3);
    select count(*) into v_n
      from public.evaluations where achievement_id = v_ach and user_id = v_a;
    select evaluation_count into v_cnt from public.achievements where id = v_ach;
    insert into public._gal_e2e_results values
        ('a_upsert_no_dup', v_n = 1 and v_cnt = 1, format('rows=%s cnt=%s', v_n, v_cnt))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    -- B: вторая оценка
    perform set_config('request.jwt.claims', '{{"sub":"'||v_b::text||'","role":"authenticated"}}', true);
    perform public.submit_evaluation(v_ach, 2);
    select evaluation_count, average_position
      into v_cnt, v_avg from public.achievements where id = v_ach;
    insert into public._gal_e2e_results values
        ('b_second', v_cnt = 2 and v_avg = 1.0, format('cnt=%s avg=%s', v_cnt, v_avg))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
end $$;

-- RLS: собственную незаблокированную оценку обновлять можно (policy unlocked)
set local request.jwt.claims = '{j_a}';
set local role authenticated;
do $$
declare v_n bigint;
begin
    update public.evaluations set position = position
     where achievement_id = (select id from public.achievements where title = 'E2E Achievement')
       and user_id = '{a}';
    get diagnostics v_n = row_count;
    insert into public._gal_e2e_results values
        ('rls_unlocked_self_update', v_n = 1, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
end $$;
reset role;

do $$
declare
    v_b uuid := '{b}';
    v_c uuid := '{c}';
    v_d uuid := '{d}';
    v_ach bigint;
    v_cnt integer;
    v_avg double precision;
    v_st public.ranking_status;
    v_rnk integer;
    v_n bigint;
    v_part boolean;
begin
    select id into v_ach from public.achievements where title = 'E2E Achievement';

    -- C: третья оценка -> переход в ranked + lock
    perform set_config('request.jwt.claims', '{{"sub":"'||v_c::text||'","role":"authenticated"}}', true);
    perform public.submit_evaluation(v_ach, 1);
    select evaluation_count, average_position, ranking_status, rank
      into v_cnt, v_avg, v_st, v_rnk
      from public.achievements where id = v_ach;
    insert into public._gal_e2e_results values
        ('c_third_ranked', v_cnt = 3 and v_avg = 1.0 and v_st = 'ranked' and v_rnk is not null,
         format('cnt=%s avg=%s st=%s rank=%s', v_cnt, v_avg, v_st, v_rnk))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    select count(*) into v_n
      from public.evaluations
     where achievement_id = v_ach and locked = false;
    insert into public._gal_e2e_results values
        ('all_locked_on_ranked', v_n = 0, format('unlocked=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    select count(*) into v_n
      from public.evaluations
     where achievement_id = v_ach and position = 1;
    insert into public._gal_e2e_results values
        ('positions_all_1', v_n = 3, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    -- D8: player scores = 1000 / rank по approved completion'ам ранжированных
    select count(*) into v_n
      from public.player_leaderboard()
     where score = 1000.00 and achievement_count = 1;
    insert into public._gal_e2e_results values
        ('player_score_1000', v_n = 3, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    select count(*) into v_n
      from public.achievements
     where ranking_status = 'ranked' and status = 'published' and rank = 1
       and id = v_ach;
    insert into public._gal_e2e_results values
        ('achievement_leaderboard_rank1', v_n = 1, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    select (select count(*) from public.achievements
             where ranking_status = 'ranked' and status = 'published')
         = (select max(rank) from public.achievements
             where ranking_status = 'ranked' and status = 'published')
      into v_part;
    insert into public._gal_e2e_results values
        ('ranks_compact', v_part, format('compact=%s', v_part))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;

    -- C: повторная оценка после ranked -> LOCKED
    begin
        perform public.submit_evaluation(v_ach, 2);
        insert into public._gal_e2e_results values
            ('locked_reject', false, 'no LOCKED raised')
        on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
    exception when others then
        if sqlerrm like '%LOCKED%' then
            insert into public._gal_e2e_results values
                ('locked_reject', true, left(sqlerrm, 60))
            on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
        else
            insert into public._gal_e2e_results values
                ('locked_reject', false, 'wrong error: ' || left(sqlerrm, 60))
            on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
        end if;
    end;

    -- D: без approved completion -> NOT_APPROVED
    perform set_config('request.jwt.claims', '{{"sub":"'||v_d::text||'","role":"authenticated"}}', true);
    begin
        perform public.submit_evaluation(v_ach, 1);
        insert into public._gal_e2e_results values
            ('not_approved_reject', false, 'no NOT_APPROVED raised')
        on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
    exception when others then
        if sqlerrm like '%NOT_APPROVED%' then
            insert into public._gal_e2e_results values
                ('not_approved_reject', true, left(sqlerrm, 60))
            on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
        else
            insert into public._gal_e2e_results values
                ('not_approved_reject', false, 'wrong error: ' || left(sqlerrm, 60))
            on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
        end if;
    end;
end $$;

-- HARDENING: прямой UPDATE evaluations под authenticated запрещён целиком
-- (оценки правятся только через RPC submit_evaluation; 42501)
set local request.jwt.claims = '{j_a}';
set local role authenticated;
do $$
begin
    begin
        update public.evaluations set position = position
         where achievement_id = (select id from public.achievements where title = 'E2E Achievement')
           and user_id = '{a}';
        insert into public._gal_e2e_results values
            ('rls_locked_update_blocked', false, 'update not blocked')
        on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
    exception when others then
        if sqlerrm like '%permission denied%' or sqlerrm like '%42501%' then
            insert into public._gal_e2e_results values
                ('rls_locked_update_blocked', true, left(sqlerrm, 60))
            on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
        else
            insert into public._gal_e2e_results values
                ('rls_locked_update_blocked', false, 'wrong error: ' || left(sqlerrm, 60))
            on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
        end if;
    end;
end $$;
reset role;

-- D8: player_leaderboard() читается даже под ролью anon (grant + security definer)
set local role anon;
set local request.jwt.claims = '{{"role":"anon"}}';
do $$
declare v_n bigint;
begin
    select count(*) into v_n from public.player_leaderboard() where score = 1000.00;
    insert into public._gal_e2e_results values
        ('player_leaderboard_anon', v_n = 3, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
end $$;
reset role;

-- RLS: вставка оценки без approved completion блокируется
set local request.jwt.claims = '{j_d}';
set local role authenticated;
do $$
begin
    begin
        insert into public.evaluations (achievement_id, user_id, position)
        values ((select id from public.achievements where title = 'E2E Achievement'), '{d}', 1);
        insert into public._gal_e2e_results values
            ('rls_insert_without_approval', false, 'insert not blocked')
        on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
    exception when others then
        insert into public._gal_e2e_results values
            ('rls_insert_without_approval', true, left(sqlerrm, 60))
        on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
    end;
end $$;
reset role;

do $$
declare
    v_ach bigint;
begin
    select id into v_ach from public.achievements where title = 'E2E Achievement';
    delete from public.evaluations where achievement_id = v_ach;
    delete from public.proofs where completion_id in
        (select id from public.achievement_completions where achievement_id = v_ach);
    delete from public.achievement_completions where achievement_id = v_ach;
    delete from public.achievements where id = v_ach;
    delete from public.categories where name = 'E2E-Cat';
end $$;

select kind, ok, note from public._gal_e2e_results order by kind;
"""


def main() -> int:
    env = _Env()
    if not env.supabase_access_token:
        print("Set SUPABASE_ACCESS_TOKEN in .env", file=sys.stderr)
        return 1

    created: list[dict] = []
    try:
        for name in ("mod", "a", "b", "c", "d"):
            info = _sql_create_user_api(env)
            info["name"] = name
            created.append(info)
            print(f"+ user {name} ready")

        ids = {info["name"]: info["user_id"] for info in created}
        sql = build_payload(ids["a"], ids["b"], ids["c"], ids["d"], ids["mod"])
        resp = _run_sql(env, sql)
        print(f"--- evaluation RPC live check: HTTP {resp.status_code}")
        print(resp.text)
        if resp.status_code != 201:
            print("--- payload failed; nothing persisted (transaction rolled back)")
            return 1

        rows = resp.json()
        rows = rows if isinstance(rows, list) else []
        total = len(rows)
        passed = sum(1 for r in rows if isinstance(r, dict) and r.get("ok") is True)
        print(f"GAL E2E OK: {passed}/{total} checks -- see table above")
        if passed != total:
            print("--- FAILED: some checks did not pass", file=sys.stderr)
            return 1

        resp = _run_sql(
            env, "drop table if exists public._gal_e2e_results;", timeout=60
        )
        _sql_delete_users(env, [info["user_id"] for info in created])
        return 0
    finally:
        _sql_delete_users(env, [info["user_id"] for info in created])


if __name__ == "__main__":
    sys.exit(main())
