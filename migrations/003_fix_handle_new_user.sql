-- GAL — fix handle_new_user under RLS
-- 003_fix_handle_new_user.sql
-- Триггер создаёт строку в public.profiles при регистрации. Так как на profiles
-- включён RLS, функция обязана выполняться как владелец (security definer),
-- иначе вставка отклоняется политиками и регистрация падает с
-- "Database error creating new user".

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, username, display_name)
    values (
        new.id,
        coalesce(
            new.raw_user_meta_data ->> 'username',
            'user_' || substr(replace(new.id::text, '-', ''), 1, 12)
        ),
        nullif(new.raw_user_meta_data ->> 'display_name', '')
    )
    on conflict (id) do nothing;
    return new;
end;
$$;
