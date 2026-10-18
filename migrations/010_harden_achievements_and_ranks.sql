-- GAL — hardening D11 (010)
-- 1. Запрет подделки ранга/метрик через REST: ограничить UPDATE achievements
--    только разрешёнными колонками для authenticated (owner может править pending).
revoke update on public.achievements from authenticated;

grant update (
  title,
  description,
  requirements,
  category_id,
  status
) on public.achievements to authenticated;

-- 2. recompute_ranks: убрать публичный execute у authenticated, добавить гард
revoke execute on function public.recompute_ranks() from public;
revoke execute on function public.recompute_ranks() from authenticated;
grant execute on function public.recompute_ranks() to service_role;

create or replace function public.recompute_ranks()
returns void
language sql
security definer
set search_path = public
as $$
  -- Defense-in-depth: только модератор/админ (JWT-роль) или сервис-роль
  -- auth.jwt() доступен при наличии claim; is_moderator() читает role из JWT
  if not public.is_moderator() then
      raise exception 'FORBIDDEN';
  end if;

  -- Фаза 1: вывести существующие ранги на безопасную высоту
  update public.achievements
  set rank = rank + 1000000000
  where rank is not null and rank < 1000000000;

  -- Фаза 2: плотное перенумерование опубликованных ранжированных с 1..N
  with ranked as (
      select id,
             row_number() over (
                 order by average_position asc, rank_order asc, id asc
             ) as new_rank
      from public.achievements
      where ranking_status = 'ranked'
        and status = 'published'
  )
  update public.achievements a
  set rank = r.new_rank
  from ranked r
  where a.id = r.id;
$$;

grant execute on function public.recompute_ranks() to service_role;

-- 3. submit_evaluation: при >=3 оценках всегда фиксируем ranked + lock всех
create or replace function public.submit_evaluation(
    p_achievement_id bigint,
    p_harder_count integer
)
returns table (
    achievement_id bigint,
    evaluation_count integer,
    average_position double precision,
    ranking_status public.ranking_status,
    my_position double precision
)
language plpgsql
security definer
set search_path = public
as $$
#variable_conflict use_column
declare
    v_uid uuid := auth.uid();
    v_approved boolean;
    v_row evaluations%rowtype;
    v_old_pos double precision;
    v_max_pos double precision;
    v_new_pos double precision;
    v_count integer;
    v_avg double precision;
    v_rstatus public.ranking_status;
begin
    if v_uid is null then
        raise exception 'AUTH_REQUIRED';
    end if;

    perform pg_advisory_xact_lock(hashtext('gal_eval_' || v_uid::text));

    select exists (
        select 1
        from public.achievement_completions c
        join public.achievements a on a.id = c.achievement_id
                                   and a.status = 'published'
        where c.user_id = v_uid
          and c.achievement_id = p_achievement_id
          and c.status = 'approved'
    ) into v_approved;

    if not v_approved then
        raise exception 'NOT_APPROVED';
    end if;

    select *
    into v_row
    from public.evaluations e
    where e.achievement_id = p_achievement_id
      and e.user_id = v_uid;

    if v_row.id is not null and v_row.locked then
        raise exception 'LOCKED';
    end if;

    select max(e.position)
    into v_max_pos
    from public.evaluations e
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id
      and e.locked = false;

    v_old_pos := v_row.position;
    v_new_pos := least(
        greatest(coalesce(p_harder_count, 0), 0),
        coalesce(v_max_pos, 0)::integer
    ) + 1;

    if v_old_pos is not null then
        update public.evaluations e
        set position = e.position - 1
        where e.user_id = v_uid
          and e.achievement_id <> p_achievement_id
          and e.locked = false
          and e.position > v_old_pos;
    end if;

    update public.evaluations e
    set position = e.position + 1
    where e.user_id = v_uid
      and e.achievement_id <> p_achievement_id
      and e.locked = false
      and e.position >= v_new_pos;

    insert into public.evaluations (achievement_id, user_id, position)
    values (p_achievement_id, v_uid, v_new_pos)
    on conflict (achievement_id, user_id)
    do update set position = excluded.position;

    select count(*), avg(e.position)
    into v_count, v_avg
    from public.evaluations e
    where e.achievement_id = p_achievement_id;

    update public.achievements
    set average_position = v_avg,
        evaluation_count = v_count
    where id = p_achievement_id;

    if v_count >= 3 then
        select a.ranking_status
        into v_rstatus
        from public.achievements a
        where a.id = p_achievement_id;

        update public.achievements
        set ranking_status = 'ranked',
            rank_order = coalesce(rank_order, random()),
            rank = coalesce(
                rank,
                coalesce((select max(a.rank) from public.achievements a where a.rank is not null), 0) + 1
            )
        where id = p_achievement_id;

        update public.evaluations e
        set locked = true
        where e.achievement_id = p_achievement_id;
    end if;

    perform public.recompute_ranks();

    return query
        select a.id,
               a.evaluation_count,
               a.average_position,
               a.ranking_status,
               e.position as my_position
        from public.achievements a
        left join public.evaluations e
               on e.achievement_id = a.id and e.user_id = v_uid
        where a.id = p_achievement_id;
end;
$$;

grant execute on function public.submit_evaluation(bigint, integer) to authenticated;
