-- GAL — storage bucket for proofs
-- 004_storage_proofs.sql
-- Приватный bucket "proofs" для доказательств выполнения.
-- Путь объекта: {user_id}/{achievement_id}/{uuid}-{filename}
-- Читать объекты могут владелец и модераторы (через signed URL).

insert into storage.buckets (id, name, public, file_size_limit)
values ('proofs', 'proofs', false, 52428800)
on conflict (id) do nothing;

drop policy if exists proofs_objects_insert_own on storage.objects;
create policy proofs_objects_insert_own on storage.objects
    for insert to authenticated
    with check (
        bucket_id = 'proofs'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists proofs_objects_select_own on storage.objects;
create policy proofs_objects_select_own on storage.objects
    for select to authenticated
    using (
        bucket_id = 'proofs'
        and (storage.foldername(name))[1] = auth.uid()::text
    );

drop policy if exists proofs_objects_select_moderator on storage.objects;
create policy proofs_objects_select_moderator on storage.objects
    for select to authenticated
    using (bucket_id = 'proofs' and public.is_moderator());

drop policy if exists proofs_objects_delete_own on storage.objects;
create policy proofs_objects_delete_own on storage.objects
    for delete to authenticated
    using (
        bucket_id = 'proofs'
        and (storage.foldername(name))[1] = auth.uid()::text
    );
