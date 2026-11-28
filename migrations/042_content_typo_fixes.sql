-- GAL — исправление опечаток в текстах достижений (D31)
-- 042_content_typo_fixes.sql
--
-- Меняются только title / description / requirements. Статусы, категории,
-- ранги, bt_* и оценки не трогаются — пересчёт BT не требуется.
-- Применено к проде 2026-11-28 через PostgREST PATCH (service role):
-- HTTP 204 у всех четырёх карточек, повторный GET подтвердил содержимое.
-- Триггер 036 (BEFORE UPDATE OF title, description, requirements, category_id)
-- при таких правках перерегистрирует отпечаток achievement по старой и новой строке.

begin;

update public.achievements
set requirements = 'Выпить полный стакан воды.'
where id = 35;

update public.achievements
set requirements = 'Заснуть до полуночи.'
where id = 36;

update public.achievements
set description = 'Нежить, вампиры, ночные духи ведут ночной образ жизни, а достижение по типу «Заснуть раньше полночи» дискриминирует ночных существ.'
where id = 51;

update public.achievements
set title = 'Программист Python',
    requirements = 'Скриншот своего места в таблице лидеров.'
where id = 60;

commit;
