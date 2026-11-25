-- GAL — 036: технический запрет идентичных заявок (D25)
--
-- Отпечаток считает PostgreSQL по нормализованному содержимому. Тип заявки
-- входит и в payload, и в ключ реестра, поэтому прохождение и заявка в топ-5
-- из одной формы не конфликтуют. Реестр сохраняет историю: отклонение, отмена
-- или удаление proof не делает повторную идентичную отправку возможной.
--
-- Прохождение отправляется через service-role RPC: файлы загружаются в Storage
-- заранее, а байтовый SHA-256 вычисляет backend. В одной транзакции RPC меняет
-- заявку, заменяет proofs и регистрирует попытку. При дубликате откат возвращает
-- старые proofs, поэтому проверка выполняется до их фактического удаления.

begin;

-- +--------------------------------------------------+
-- | 1. Реестр отпечатков и поля attempts/proofs     |
-- +--------------------------------------------------+

alter table public.achievement_completions
    add column if not exists submission_attempt integer not null default 0;

alter table public.achievement_completions
    drop constraint if exists achievement_completions_submission_attempt_non_negative;
alter table public.achievement_completions
    add constraint achievement_completions_submission_attempt_non_negative
    check (submission_attempt >= 0);

alter table public.proofs
    add column if not exists content_hash text;

alter table public.proofs
    drop constraint if exists proofs_content_hash_format;
alter table public.proofs
    add constraint proofs_content_hash_format
    check (content_hash is null or content_hash ~ '^[0-9a-f]{64}$');

create table if not exists public.submission_fingerprints (
    id bigint generated always as identity primary key,
    submission_type text not null,
    submitter_id uuid not null,
    fingerprint text not null,
    submission_id bigint not null,
    first_attempt_no integer not null default 1,
    last_attempt_no integer not null default 1,
    canonical_payload jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint submission_fingerprints_type_check
        check (submission_type in ('achievement', 'completion', 'top5', 'report')),
    constraint submission_fingerprints_hash_check
        check (fingerprint ~ '^[0-9a-f]{64}$'),
    constraint submission_fingerprints_attempt_check
        check (first_attempt_no >= 1 and last_attempt_no >= first_attempt_no)
);

alter table public.submission_fingerprints
    drop constraint if exists submission_fingerprints_identity;
alter table public.submission_fingerprints
    add constraint submission_fingerprints_identity
    unique (submission_type, submitter_id, fingerprint);

create index if not exists submission_fingerprints_submission_idx
    on public.submission_fingerprints (submission_type, submission_id);

alter table public.submission_fingerprints enable row level security;

-- +--------------------------------------------------+
-- | 2. Канонизация содержимого                      |
-- +--------------------------------------------------+

create or replace function public.canonical_submission_text(p_text text)
returns text
language sql
immutable
strict
parallel safe
as $$
    select lower(regexp_replace(normalize(btrim(p_text), nfc), '\s+', ' ', 'g'));
$$;

create or replace function public.canonical_submission_url(p_url text)
returns text
language plpgsql
immutable
strict
parallel safe
as $$
declare
    v_parts text[];
    v_scheme text;
    v_authority text;
    v_path text;
    v_query text;
begin
    v_parts := regexp_match(
        btrim(p_url),
        '^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)([^?#]*)(\?[^#]*)?'
    );
    if v_parts is null then
        return public.canonical_submission_text(p_url);
    end if;

    v_scheme := lower(v_parts[1]);
    if v_scheme not in ('http', 'https') then
        return public.canonical_submission_text(p_url);
    end if;

    v_authority := lower(v_parts[2]);
    if (v_scheme = 'http' and v_authority like '%:80')
        or (v_scheme = 'https' and v_authority like '%:443') then
        v_authority := regexp_replace(v_authority, ':(80|443)$', '');
    end if;

    v_path := coalesce(v_parts[3], '');
    if v_path = '' then
        v_path := '/';
    end if;
    v_query := coalesce(v_parts[4], '');

    return v_scheme || '://' || v_authority || v_path || v_query;
end;
$$;

create or replace function public.submission_proof_set(p_completion_id bigint)
returns jsonb
language sql
stable
parallel safe
as $$
    with items as (
        select
            p.proof_type::text as proof_type,
            case
                when p.proof_type = 'link' then encode(
                    sha256(convert_to(public.canonical_submission_url(p.storage_path), 'UTF8')),
                    'hex'
                )
                else coalesce(
                    p.content_hash,
                    'legacy-path:' || encode(
                        sha256(convert_to(p.storage_path, 'UTF8')),
                        'hex'
                    )
                )
            end as content_hash,
            public.canonical_submission_text(coalesce(p.description, '')) as description
        from public.proofs p
        where p.completion_id = p_completion_id
    )
    select coalesce(
        jsonb_agg(
            jsonb_build_object(
                'proof_type', proof_type,
                'content_hash', content_hash,
                'description', description
            )
            order by content_hash, proof_type, description
        ),
        '[]'::jsonb
    )
    from items;
$$;

create or replace function public.achievement_submission_payload(
    p_row public.achievements
)
returns jsonb
language sql
immutable
parallel safe
as $$
    select jsonb_build_object(
        'title', public.canonical_submission_text(p_row.title),
        'description', public.canonical_submission_text(coalesce(p_row.description, '')),
        'requirements', public.canonical_submission_text(coalesce(p_row.requirements, '')),
        'category_id', p_row.category_id
    );
$$;

create or replace function public.completion_submission_payload(
    p_row public.achievement_completions
)
returns jsonb
language sql
stable
parallel safe
as $$
    select jsonb_build_object(
        'achievement_id', p_row.achievement_id,
        'requested_harder_count', p_row.requested_harder_count,
        'proofs_visible', p_row.proofs_visible,
        'comment', public.canonical_submission_text(coalesce(p_row.comment, '')),
        'proofs', public.submission_proof_set(p_row.id)
    );
$$;

create or replace function public.top5_submission_payload(
    p_row public.top5_applications
)
returns jsonb
language sql
stable
parallel safe
as $$
    select jsonb_build_object(
        'achievement_id', p_row.achievement_id,
        'proposed_position', p_row.proposed_position,
        'justification', public.canonical_submission_text(coalesce(p_row.justification, '')),
        'evidence_url', case
            when nullif(btrim(coalesce(p_row.evidence_url, '')), '') is null then null
            else public.canonical_submission_url(p_row.evidence_url)
        end,
        'evidence_note', public.canonical_submission_text(coalesce(p_row.evidence_note, '')),
        'proofs', public.submission_proof_set(p_row.completion_id)
    );
$$;

create or replace function public.report_submission_payload(p_row public.reports)
returns jsonb
language sql
immutable
parallel safe
as $$
    select jsonb_build_object(
        'achievement_id', p_row.achievement_id,
        'reason', public.canonical_submission_text(coalesce(p_row.reason, '')),
        'description', public.canonical_submission_text(coalesce(p_row.description, ''))
    );
$$;

create or replace function public.submission_fingerprint(
    p_type text,
    p_submitter_id uuid,
    p_payload jsonb
)
returns text
language sql
immutable
strict
parallel safe
as $$
    select encode(
        sha256(convert_to(
            jsonb_build_object(
                'algorithm_version', 1,
                'submission_type', p_type,
                'submitter_id', p_submitter_id,
                'payload', p_payload
            )::text,
            'UTF8'
        )),
        'hex'
    );
$$;

-- +--------------------------------------------------+
-- | 3. Регистрация и триггеры                       |
-- +--------------------------------------------------+

create or replace function public.register_submission_fingerprint(
    p_type text,
    p_submitter_id uuid,
    p_submission_id bigint,
    p_attempt_no integer,
    p_payload jsonb,
    p_previous_fingerprint text default null
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_fingerprint text;
    v_submission_id bigint;
    v_last_attempt integer;
begin
    v_fingerprint := public.submission_fingerprint(
        p_type,
        p_submitter_id,
        p_payload
    );

    -- Уникальный ключ — окончательный арбитр, а advisory lock сериализует
    -- одновременные одинаковые INSERT до проверки реестра.
    perform pg_advisory_xact_lock(
        hashtextextended(p_type || ':' || p_submitter_id::text || ':' || v_fingerprint, 0)
    );

    select f.submission_id, f.last_attempt_no
    into v_submission_id, v_last_attempt
    from public.submission_fingerprints f
    where f.submission_type = p_type
      and f.submitter_id = p_submitter_id
      and f.fingerprint = v_fingerprint
    for update;

    if found then
        -- Редактирование собственного pending-достижения может вернуть прежний
        -- текст. Для completion/top5/report допустим только тот же attempt:
        -- новая попытка с тем же содержимым всегда считается дубликатом.
        if v_submission_id = p_submission_id
            and (p_type = 'achievement' or p_attempt_no = v_last_attempt) then
            update public.submission_fingerprints
            set last_attempt_no = greatest(last_attempt_no, p_attempt_no),
                updated_at = now()
            where id = (
                select f.id
                from public.submission_fingerprints f
                where f.submission_type = p_type
                  and f.submitter_id = p_submitter_id
                  and f.fingerprint = v_fingerprint
            );
            return;
        end if;

        -- Унаследованные дубли #52–55 уже зарегистрированы на другую строку.
        -- Если содержимое не изменилось, правка таких achievement должна
        -- работать; новое совпадение по-прежнему блокируется ниже.
        if p_previous_fingerprint is not null
            and (
                p_previous_fingerprint = v_fingerprint
                or exists (
                    select 1
                    from public.submission_fingerprints f
                    where f.submission_type = p_type
                      and f.submitter_id = p_submitter_id
                      and f.fingerprint = p_previous_fingerprint
                      and f.submission_id = p_submission_id
                )
            ) then
            return;
        end if;

        raise exception using errcode = '23505', message = 'DUPLICATE_SUBMISSION';
    end if;

    insert into public.submission_fingerprints (
        submission_type,
        submitter_id,
        fingerprint,
        submission_id,
        first_attempt_no,
        last_attempt_no,
        canonical_payload
    )
    values (
        p_type,
        p_submitter_id,
        v_fingerprint,
        p_submission_id,
        p_attempt_no,
        p_attempt_no,
        p_payload
    );
end;
$$;

create or replace function public.register_submission_trigger()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_previous_fingerprint text;
begin
    if tg_table_name = 'achievements' then
        if tg_op = 'UPDATE' then
            v_previous_fingerprint := public.submission_fingerprint(
                'achievement', old.creator_id,
                public.achievement_submission_payload(old)
            );
        end if;
        perform public.register_submission_fingerprint(
            'achievement', new.creator_id, new.id, 1,
            public.achievement_submission_payload(new),
            v_previous_fingerprint
        );
    elsif tg_table_name = 'top5_applications' then
        perform public.register_submission_fingerprint(
            'top5', new.applicant_id, new.id, 1,
            public.top5_submission_payload(new)
        );
    elsif tg_table_name = 'reports' then
        perform public.register_submission_fingerprint(
            'report', new.reporter_id, new.id, 1,
            public.report_submission_payload(new)
        );
    end if;
    return new;
end;
$$;

drop trigger if exists achievements_submission_fingerprint on public.achievements;
create trigger achievements_submission_fingerprint
before insert or update of title, description, requirements, category_id
on public.achievements
for each row execute function public.register_submission_trigger();

-- Имя с zz гарантирует запуск после top5_applications_guard: PostgreSQL
-- выполняет BEFORE-триггеры одного события в алфавитном порядке имён.
drop trigger if exists zz_top5_applications_fingerprint on public.top5_applications;
create trigger zz_top5_applications_fingerprint
before insert on public.top5_applications
for each row execute function public.register_submission_trigger();

drop trigger if exists reports_submission_fingerprint on public.reports;
create trigger reports_submission_fingerprint
before insert on public.reports
for each row execute function public.register_submission_trigger();

-- +--------------------------------------------------+
-- | 4. Атомарная отправка прохождения               |
-- +--------------------------------------------------+

create or replace function public.submit_completion_with_proofs(
    p_user_id uuid,
    p_achievement_id bigint,
    p_harder_count integer default null,
    p_proofs_visible boolean default true,
    p_comment text default '',
    p_proofs jsonb default '[]'::jsonb
)
returns table (
    completion_id bigint,
    attempt_no integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_completion_id bigint;
    v_status public.completion_status;
    v_attempt integer;
    v_creator uuid;
    v_achievement_status public.achievement_status;
    v_completion public.achievement_completions%rowtype;
begin
    if auth.role() <> 'service_role' then
        raise exception using errcode = '42501', message = 'SYSTEM_ONLY';
    end if;
    if p_user_id is null or p_achievement_id is null then
        raise exception using errcode = '22023', message = 'SUBMISSION_FIELDS_REQUIRED';
    end if;
    if p_proofs is null or jsonb_typeof(p_proofs) <> 'array'
        or jsonb_array_length(p_proofs) = 0 then
        raise exception using errcode = '22023', message = 'PROOF_REQUIRED';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended(p_user_id::text || ':' || p_achievement_id::text, 0)
    );

    select a.creator_id, a.status
    into v_creator, v_achievement_status
    from public.achievements a
    where a.id = p_achievement_id;
    if not found then
        raise exception using errcode = '23503', message = 'ACHIEVEMENT_NOT_FOUND';
    end if;
    if v_achievement_status <> 'published'
        and not (
            v_achievement_status = 'pending'
            and v_creator = p_user_id
        ) then
        raise exception using errcode = '42501', message = 'NOT_ALLOWED';
    end if;

    select c.id, c.status
    into v_completion_id, v_status
    from public.achievement_completions c
    where c.achievement_id = p_achievement_id
      and c.user_id = p_user_id
    for update;

    if found then
        if v_status = 'rejected' then
            null;
        elsif v_status = 'pending' and not exists (
            select 1 from public.proofs p where p.completion_id = v_completion_id
        ) then
            null;
        else
            raise exception using errcode = '23505', message = 'ALREADY_COMPLETED';
        end if;
    else
        insert into public.achievement_completions (
            achievement_id,
            user_id,
            status,
            requested_harder_count,
            proofs_visible,
            comment,
            submission_attempt
        )
        values (
            p_achievement_id,
            p_user_id,
            'pending',
            p_harder_count,
            p_proofs_visible,
            nullif(left(coalesce(p_comment, ''), 2000), ''),
            0
        )
        returning id into v_completion_id;
    end if;

    v_attempt := coalesce((
        select c.submission_attempt
        from public.achievement_completions c
        where c.id = v_completion_id
    ), 0) + 1;

    update public.achievement_completions c
    set status = 'pending',
        approved_at = null,
        requested_harder_count = case
            when p_harder_count is null then c.requested_harder_count
            else p_harder_count
        end,
        proofs_visible = p_proofs_visible,
        comment = nullif(left(coalesce(p_comment, ''), 2000), ''),
        submission_attempt = v_attempt
    where c.id = v_completion_id;

    delete from public.proofs p where p.completion_id = v_completion_id;

    insert into public.proofs (
        completion_id,
        storage_path,
        proof_type,
        description,
        content_hash
    )
    select
        v_completion_id,
        btrim(item->>'storage_path'),
        coalesce(item->>'proof_type', 'other')::public.proof_type,
        nullif(btrim(coalesce(item->>'description', '')), ''),
        case
            when coalesce(item->>'proof_type', 'other') = 'link' then null
            else nullif(item->>'content_hash', '')
        end
    from jsonb_array_elements(p_proofs) as item
    order by item->>'storage_path';

    select c.* into v_completion
    from public.achievement_completions c
    where c.id = v_completion_id;

    perform public.register_submission_fingerprint(
        'completion',
        p_user_id,
        v_completion_id,
        v_attempt,
        public.completion_submission_payload(v_completion)
    );

    return query select v_completion_id, v_attempt;
end;
$$;

-- +--------------------------------------------------+
-- | 5. Backfill без удаления исторических дублей     |
-- +--------------------------------------------------+

-- Ссылки можно захешировать в SQL. Байты старых файлов Storage недоступны БД;
-- для них payload использует стабильный legacy-path отпечаток, пока серверный
-- скрипт не заполнит content_hash фактическими SHA-256 файлов.
update public.proofs p
set content_hash = encode(
    sha256(convert_to(public.canonical_submission_url(p.storage_path), 'UTF8')),
    'hex'
)
where p.proof_type = 'link'
  and p.content_hash is null
  and not exists (
      select 1
      from public.proofs first_proof
      where first_proof.completion_id = p.completion_id
        and first_proof.id < p.id
        and first_proof.content_hash = encode(
            sha256(convert_to(public.canonical_submission_url(p.storage_path), 'UTF8')),
            'hex'
        )
  );

-- Если в legacy-заявке уже были две одинаковые ссылки, оставляем обе строки,
-- но снимаем hash со всех кроме первой, чтобы новый unique index не удалял и
-- не блокировал исторические данные.
with duplicates as (
    select
        p.id,
        row_number() over (
            partition by p.completion_id, p.content_hash
            order by p.id
        ) as rn
    from public.proofs p
    where p.content_hash is not null
)
update public.proofs p
set content_hash = null
from duplicates d
where p.id = d.id
  and d.rn > 1;

create unique index if not exists proofs_completion_content_unique
    on public.proofs (completion_id, content_hash)
    where content_hash is not null;

-- Каждая существующая заявка — это уже состоявшаяся попытка, даже если её
-- proof были удалены старым reset_rejected_completion.
update public.achievement_completions c
set submission_attempt = greatest(c.submission_attempt, 1);

insert into public.submission_fingerprints (
    submission_type,
    submitter_id,
    fingerprint,
    submission_id,
    first_attempt_no,
    last_attempt_no,
    canonical_payload
)
select distinct on (computed.submitter_id, computed.fingerprint)
    'achievement',
    computed.submitter_id,
    computed.fingerprint,
    computed.submission_id,
    1,
    1,
    computed.payload
from (
    select
        a.id as submission_id,
        a.creator_id as submitter_id,
        public.submission_fingerprint(
            'achievement', a.creator_id, public.achievement_submission_payload(a)
        ) as fingerprint,
        public.achievement_submission_payload(a) as payload
    from public.achievements a
) as computed
order by computed.submitter_id, computed.fingerprint, computed.submission_id
on conflict (submission_type, submitter_id, fingerprint) do nothing;

insert into public.submission_fingerprints (
    submission_type,
    submitter_id,
    fingerprint,
    submission_id,
    first_attempt_no,
    last_attempt_no,
    canonical_payload
)
select distinct on (computed.submitter_id, computed.fingerprint)
    'completion',
    computed.submitter_id,
    computed.fingerprint,
    computed.submission_id,
    1,
    1,
    computed.payload
from (
    select
        c.id as submission_id,
        c.user_id as submitter_id,
        public.submission_fingerprint(
            'completion', c.user_id, public.completion_submission_payload(c)
        ) as fingerprint,
        public.completion_submission_payload(c) as payload
    from public.achievement_completions c
) as computed
order by computed.submitter_id, computed.fingerprint, computed.submission_id
on conflict (submission_type, submitter_id, fingerprint) do nothing;

insert into public.submission_fingerprints (
    submission_type,
    submitter_id,
    fingerprint,
    submission_id,
    first_attempt_no,
    last_attempt_no,
    canonical_payload
)
select distinct on (computed.submitter_id, computed.fingerprint)
    'top5',
    computed.submitter_id,
    computed.fingerprint,
    computed.submission_id,
    1,
    1,
    computed.payload
from (
    select
        a.id as submission_id,
        a.applicant_id as submitter_id,
        public.submission_fingerprint(
            'top5', a.applicant_id, public.top5_submission_payload(a)
        ) as fingerprint,
        public.top5_submission_payload(a) as payload
    from public.top5_applications a
) as computed
order by computed.submitter_id, computed.fingerprint, computed.submission_id
on conflict (submission_type, submitter_id, fingerprint) do nothing;

insert into public.submission_fingerprints (
    submission_type,
    submitter_id,
    fingerprint,
    submission_id,
    first_attempt_no,
    last_attempt_no,
    canonical_payload
)
select distinct on (computed.submitter_id, computed.fingerprint)
    'report',
    computed.submitter_id,
    computed.fingerprint,
    computed.submission_id,
    1,
    1,
    computed.payload
from (
    select
        r.id as submission_id,
        r.reporter_id as submitter_id,
        public.submission_fingerprint(
            'report', r.reporter_id, public.report_submission_payload(r)
        ) as fingerprint,
        public.report_submission_payload(r) as payload
    from public.reports r
) as computed
order by computed.submitter_id, computed.fingerprint, computed.submission_id
on conflict (submission_type, submitter_id, fingerprint) do nothing;

-- Триггер создаётся после backfill намеренно: при обнулении content_hash у
-- исторических файловых дублей BEFORE-триггер иначе требовал бы
-- PROOF_HASH_REQUIRED и сорвал бы применение миграции. Новые строки и изменения
-- проверяются уже полной версией триггера.
create or replace function public.set_proof_content_hash()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.proof_type = 'link' then
        if new.storage_path !~* '^https?://[^[:space:]]+$' then
            raise exception using errcode = '22023', message = 'INVALID_PROOF_URL';
        end if;
        -- Backfill намеренно оставляет NULL у повторных legacy-ссылок, чтобы
        -- unique index не удалял исторические строки. При обычном изменении
        -- ссылки hash вычисляется заново.
        if tg_op = 'UPDATE'
            and new.storage_path is not distinct from old.storage_path
            and new.proof_type is not distinct from old.proof_type
            and new.content_hash is null then
            return new;
        end if;
        new.content_hash := encode(
            sha256(convert_to(public.canonical_submission_url(new.storage_path), 'UTF8')),
            'hex'
        );
    else
        if new.content_hash is null or new.content_hash !~ '^[0-9a-f]{64}$' then
            raise exception using errcode = '23514', message = 'PROOF_HASH_REQUIRED';
        end if;
        if new.storage_path ~* '^https?://' then
            raise exception using errcode = '23514', message = 'INVALID_PROOF_PATH';
        end if;
        new.content_hash := lower(new.content_hash);
    end if;
    return new;
end;
$$;

drop trigger if exists proofs_content_hash on public.proofs;
create trigger proofs_content_hash
before insert or update of storage_path, proof_type, content_hash
on public.proofs
for each row execute function public.set_proof_content_hash();

create or replace function public.refresh_legacy_submission_fingerprints()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_count integer;
begin
    if auth.role() <> 'service_role' then
        raise exception using errcode = '42501', message = 'SYSTEM_ONLY';
    end if;

    -- Только provisional-записи, построенные по storage_path. После серверного
    -- обхода Storage их надо пересчитать по фактическим SHA-256 файлов.
    delete from public.submission_fingerprints f
    where f.submission_type in ('completion', 'top5')
      and f.canonical_payload::text like '%legacy-path:%';

    insert into public.submission_fingerprints (
        submission_type,
        submitter_id,
        fingerprint,
        submission_id,
        first_attempt_no,
        last_attempt_no,
        canonical_payload
    )
    select distinct on (
        computed.submission_type, computed.submitter_id, computed.fingerprint
    )
        computed.submission_type,
        computed.submitter_id,
        computed.fingerprint,
        computed.submission_id,
        1,
        1,
        computed.payload
    from (
        select
            'completion'::text as submission_type,
            c.id as submission_id,
            c.user_id as submitter_id,
            public.submission_fingerprint(
                'completion', c.user_id, public.completion_submission_payload(c)
            ) as fingerprint,
            public.completion_submission_payload(c) as payload
        from public.achievement_completions c
        where exists (
            select 1 from public.proofs p where p.completion_id = c.id
        )
        union all
        select
            'top5'::text as submission_type,
            a.id as submission_id,
            a.applicant_id as submitter_id,
            public.submission_fingerprint(
                'top5', a.applicant_id, public.top5_submission_payload(a)
            ) as fingerprint,
            public.top5_submission_payload(a) as payload
        from public.top5_applications a
    ) as computed
    order by computed.submission_type, computed.submitter_id,
             computed.fingerprint, computed.submission_id
    on conflict (submission_type, submitter_id, fingerprint) do nothing;

    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

-- +--------------------------------------------------+
-- | 6. Права: клиент не подделывает hash/реестр     |
-- +--------------------------------------------------+

revoke all on table public.submission_fingerprints from anon, authenticated;
grant select, insert, update on table public.submission_fingerprints to service_role;

revoke insert on table public.achievement_completions from anon, authenticated;
revoke insert on table public.proofs from anon, authenticated;

revoke execute on function public.canonical_submission_text(text)
    from public, anon, authenticated;
revoke execute on function public.canonical_submission_url(text)
    from public, anon, authenticated;
revoke execute on function public.submission_proof_set(bigint)
    from public, anon, authenticated;
revoke execute on function public.achievement_submission_payload(public.achievements)
    from public, anon, authenticated;
revoke execute on function public.completion_submission_payload(public.achievement_completions)
    from public, anon, authenticated;
revoke execute on function public.top5_submission_payload(public.top5_applications)
    from public, anon, authenticated;
revoke execute on function public.report_submission_payload(public.reports)
    from public, anon, authenticated;
revoke execute on function public.submission_fingerprint(text, uuid, jsonb)
    from public, anon, authenticated;
revoke execute on function public.register_submission_fingerprint(text, uuid, bigint, integer, jsonb, text)
    from public, anon, authenticated;
revoke execute on function public.register_submission_trigger()
    from public, anon, authenticated;
revoke execute on function public.set_proof_content_hash()
    from public, anon, authenticated;

revoke execute on function public.submit_completion_with_proofs(
    uuid, bigint, integer, boolean, text, jsonb
) from public, anon, authenticated;
grant execute on function public.submit_completion_with_proofs(
    uuid, bigint, integer, boolean, text, jsonb
) to service_role;

revoke execute on function public.refresh_legacy_submission_fingerprints()
    from public, anon, authenticated;
grant execute on function public.refresh_legacy_submission_fingerprints()
    to service_role;

-- Старые RPC изменения/отката доказательств обходили бы новую атомарную
-- отправку и уничтожали историю попытки. Сигнатуры могли отличаться между
-- окружениями, поэтому отзываем все существующие варианты, а не жёстко один.
do $$
declare
    v_rpc record;
begin
    for v_rpc in
        select
            p.proname,
            pg_get_function_identity_arguments(p.oid) as v_args
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'reset_rejected_completion',
              'set_completion_comment',
              'remove_completion_proofs'
          )
    loop
        execute format(
            'revoke execute on function public.%I(%s) from public, anon, authenticated',
            v_rpc.proname,
            v_rpc.v_args
        );
    end loop;
end $$;

notify pgrst, 'reload schema';

commit;
