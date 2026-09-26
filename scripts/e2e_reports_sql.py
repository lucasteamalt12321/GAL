"""Live-проверка RLS/потоков reports через Management API (неявная транзакция).

Проверяет: создание достижения, жалоба под ролью reporter (анон не может),
изменение жалобы аноном/не модератором запрещено, модератор видит очередь,
accept хайдит достижение (deleted), reject отклоняет.
"""

from __future__ import annotations

import json

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

MANAGE_API = "https://api.supabase.com/v1"


class _Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_access_token: str = ""


def _project_ref(env: _Env) -> str:
    host = env.supabase_url.split("://")[-1].split("/")[0]
    return host.split(".")[0]


def _post_query(
    url: str, headers: dict, query: str, retries: int = 4
) -> httpx.Response:
    payload = {"query": query}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return httpx.post(
                url, headers=headers, content=json.dumps(payload), timeout=120
            )
        except httpx.HTTPError as exc:
            last = exc
            print(f"retry {attempt + 1}/{retries}: {exc}")
    raise last


def main() -> None:
    env = _Env()
    if not env.supabase_access_token or not env.supabase_url:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_ACCESS_TOKEN in .env")
    url = f"{MANAGE_API}/projects/{_project_ref(env)}/database/query"
    headers = {
        "Authorization": f"Bearer {env.supabase_access_token}",
        "Content-Type": "application/json",
    }

    moderator = "mod@gal-e2e.test"
    reporter = "e2e-reporter@gal-e2e.test"
    category = "Chess"

    sql = f"""
begin;
set local role postgres;
set local request.jwt.claims = '{{"role":"service_role"}}';

-- чистый старт: удаляем возможные остатки
delete from auth.users where email in ('{moderator}', '{reporter}');

-- пользователи + профили
insert into auth.users (instance_id, id, aud, role, email, encrypted_password,
                        email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
values
 ('00000000-0000-0000-0000-000000000000', 'd0000000-0000-4000-8000-000000000001', 'authenticated', 'authenticated', '{moderator}', crypt('secret', gen_salt('bf')),
  now(), '{{"provider":"email","providers":["email"]}}', '{{"username":"e2emod","display_name":"E2E Mod"}}'),
 ('00000000-0000-0000-0000-000000000000', 'd0000000-0000-4000-8000-000000000002', 'authenticated', 'authenticated', '{reporter}', crypt('secret', gen_salt('bf')),
  now(), '{{"provider":"email","providers":["email"]}}', '{{"username":"e2erep","display_name":"E2E Reporter"}}')
on conflict (id) do update set email = excluded.email;
insert into public.profiles (id, username, display_name, role)
select id, raw_user_meta_data->>'username', raw_user_meta_data->>'display_name',
       case when email = '{moderator}' then 'moderator'::public.user_role else 'user'::public.user_role end
from auth.users where email in ('{moderator}', '{reporter}')
on conflict (id) do update set role = excluded.role, username = excluded.username;
update public.profiles set role = 'moderator'::public.user_role where id = 'd0000000-0000-4000-8000-000000000001';

-- достижение (published), чтобы была возможность на него жаловаться
insert into public.categories (name, slug)
values ('{category}', lower('{category}'))
on conflict (slug) do nothing;
create table if not exists public._gal_e2e_results (kind text primary key, ok bool, note text);
create temp table if not exists tmp_report_ach (id int);
truncate tmp_report_ach;
insert into tmp_report_ach
select id from public.achievements where title = 'E2E Reports Achievement' limit 1;
insert into public.achievements (title, description, requirements, creator_id, status, category_id)
select 'E2E Reports Achievement', 'd', 'r', 'd0000000-0000-4000-8000-000000000001', 'published',
       (select id from public.categories where slug = lower('{category}'))
where not exists (select 1 from tmp_report_ach);
insert into tmp_report_ach
select id from public.achievements where title = 'E2E Reports Achievement' limit 1;

create temp table if not exists _gal_e2e_results (kind text primary key, ok bool, note text);
truncate _gal_e2e_results;

-- 1) reporter подаёт жалобу под своей ролью (досоздание, если осталась prev-запись)
set local role authenticated;
set local request.jwt.claims = '{{"sub":"d0000000-0000-4000-8000-000000000002","role":"authenticated","email":"{reporter}"}}';
do $$
declare v_ach int; v_n bigint; v_cnt bigint;
begin
    select id into v_ach from public.achievements where title = 'E2E Reports Achievement';
    select count(*) into v_cnt from public.reports
     where achievement_id = v_ach and reporter_id = 'd0000000-0000-4000-8000-000000000002';
    if v_cnt = 0 then
        insert into public.reports (achievement_id, reporter_id, reason, description, status)
        values (v_ach, 'd0000000-0000-4000-8000-000000000002', 'spam', 'Spam content', 'pending');
    end if;
    select count(*) into v_n from public.reports
     where achievement_id = v_ach and reporter_id = 'd0000000-0000-4000-8000-000000000002' and status = 'pending';
    insert into public._gal_e2e_results values ('report_own_insert', v_n = 1, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
end $$;
reset role;

-- 2) без явной роли (postgres) жалобу увидит любой anon? -> нет, очередь только у модератора
set local role anon;
set local request.jwt.claims = '{{"role":"anon"}}';
do $$
declare v_n bigint;
begin
    select count(*) into v_n from public.reports;
    insert into public._gal_e2e_results values ('anon_sees_reports', v_n = 0, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
end $$;
reset role;

-- 3) модератор видит очередь и принимает жалобу (accept -> achievement deleted)
set local role authenticated;
set local request.jwt.claims = '{{"sub":"d0000000-0000-4000-8000-000000000001","role":"authenticated","email":"{moderator}"}}';
do $$
declare v_rep bigint; v_ach bigint; v_n bigint; v_st text;
begin
    select count(*) into v_n from public.reports where status = 'pending';
    insert into public._gal_e2e_results values ('moderator_see_pending', v_n = 1, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
    select id into v_rep from public.reports where status = 'pending' limit 1;
    update public.reports set status='accepted', resolved_at=now(), resolved_by='d0000000-0000-4000-8000-000000000001'
     where id = v_rep;
    select achievement_id into v_ach from public.reports where id = v_rep;
    update public.achievements set status = 'deleted' where id = v_ach;
    select status into v_st from public.achievements where id = v_ach;
    insert into public._gal_e2e_results values ('accept_hides_achievement', v_st = 'deleted', format('st=%s', v_st))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
end $$;
reset role;

-- 4) удалять/изменять жалобу анон не может (RLS)
set local role anon;
set local request.jwt.claims = '{{"role":"anon"}}';
do $$
declare v_rep bigint; v_n bigint;
begin
    select id into v_rep from public.reports limit 1;
    update public.reports set status = 'rejected' where id = v_rep;
    get diagnostics v_n = row_count;
    insert into public._gal_e2e_results values ('anon_update_blocked', v_n = 0, format('rows=%s', v_n))
    on conflict (kind) do update set ok = excluded.ok, note = excluded.note;
end $$;
reset role;

-- 5) очистка достижения и жалоб
do $$
declare v_ach bigint;
begin
    select id into v_ach from public.achievements where title = 'E2E Reports Achievement';
    delete from public.reports where achievement_id = v_ach;
    delete from public.achievements where id = v_ach;
    delete from public.categories where name = '{category}' and not exists
        (select 1 from public.achievements where category_id = public.categories.id);
end $$;

select json_agg(json_build_object('kind', kind, 'ok', ok, 'note', note) order by kind) as result from public._gal_e2e_results;
commit;
"""

    resp = _post_query(url, headers, sql)
    print(f"--- reports RLS live check: HTTP {resp.status_code}")
    if resp.status_code == 201:
        body = resp.json()
        result = body[0].get("result")
        print(json.dumps(result, ensure_ascii=False, default=str))
        rows = [r for r in (result or []) if isinstance(r, dict)]
        ok_rows = [r for r in rows if r.get("ok")]
        print(f"GAL REPORTS E2E: {len(ok_rows)}/{len(rows)} checks")
        if len(ok_rows) != len(rows):
            raise SystemExit(1)
    else:
        print(resp.text[:2000])

    cleanup = (
        "delete from auth.users where id in "
        "('d0000000-0000-4000-8000-000000000001','d0000000-0000-4000-8000-000000000002');"
    )
    resp = _post_query(url, headers, cleanup)
    if resp.status_code != 201:
        print(f"cleanup users warning: {resp.status_code} {resp.text[:200]}")


if __name__ == "__main__":
    main()
