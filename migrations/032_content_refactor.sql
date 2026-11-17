-- GAL — контент-рефактор под школьную аудиторию
-- 032_content_refactor.sql
--
-- 1) Названия категорий приводятся к русскому языку (slug сохраняются,
--    чтобы не ломать существующие ссылки /achievements?category=...).
-- 2) Добавляются категории для достижений, которые сейчас свалены в «Other»:
--    творчество, спорт/здоровье, дом/быт.
-- 3) Тексты достижений формализуются: убираются просторечные выражения
--    и сленг, исправляются опечатки и написание брендов.

-- +---------------------------+
-- | categories                |
-- +---------------------------+

update public.categories set name = 'Музыка' where slug = 'music';
update public.categories set name = 'Фортепиано' where slug = 'piano';
update public.categories set name = 'Программирование' where slug = 'programming';
update public.categories set name = 'Образование' where slug = 'education';
update public.categories set name = 'Наука' where slug = 'science';
update public.categories set name = 'Другое' where slug = 'other';

insert into public.categories (name, slug)
values
    ('Творчество', 'creativity'),
    ('Спорт и здоровье', 'sport-health'),
    ('Дом и быт', 'household')
on conflict (name) do nothing;

-- +---------------------------+
-- | achievements              |
-- +---------------------------+

update public.achievements
set title = 'Пройти уровень Supersonic в Geometry Dash на 100% и собрать 3 монеты'
where id = 31;

update public.achievements
set title = 'Сыграть Linkin Park — In the End на фортепиано',
    requirements = 'Узнаваемая мелодия'
where id = 32;

update public.achievements
set title = 'Снять короткое видео о математике'
where id = 33;

update public.achievements
set description = 'Выпить стакан воды за один раз.',
    category_id = (select id from public.categories where slug = 'sport-health')
where id = 35;

update public.achievements
set description = 'Лечь спать и заснуть до 00:00.',
    requirements = 'Заснуть до полуночи',
    category_id = (select id from public.categories where slug = 'sport-health')
where id = 36;

update public.achievements
set title = 'Пройти ежедневный уровень в Geometry Dash'
where id = 46;

update public.achievements
set category_id = (select id from public.categories where slug = 'household')
where id = 47;

update public.achievements
set title = 'Вернуться в Duolingo спустя месяц'
where id = 48;

update public.achievements
set title = 'Сгенерировать изображение по описанию',
    requirements = 'Сгенерировать изображение с помощью ChatGPT так, чтобы оно соответствовало заданному описанию, и приложить результат вместе с текстом промпта.',
    category_id = (select id from public.categories where slug = 'creativity')
where id = 49;

update public.achievements
set title = 'Объявление в чате',
    description = 'Сообщить участникам чата о своём временном отсутствии по личной надобности.'
where id = 50;