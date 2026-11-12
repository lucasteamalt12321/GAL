"""Человекочитаемые подписи для значений из БД и форматирование дат.

Jinja-шаблоны не должны показывать сырые enum-значения (`pending`, `spam`,
`image`) — для этого регистрируем фильтры в `app.templating`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.config import get_settings

ACHIEVEMENT_STATUS_LABELS = {
    "pending": "На модерации",
    "published": "Опубликовано",
    "rejected": "Отклонено",
    "deleted": "Скрыто",
}

COMPLETION_STATUS_LABELS = {
    "pending": "На проверке",
    "approved": "Подтверждено",
    "rejected": "Отклонено",
}

REPORT_REASON_LABELS = {
    "duplicate": "Дубликат",
    "incorrect": "Некорректное описание или требования",
    "spam": "Спам",
    "offensive": "Оскорбительный контент",
    "other": "Другое",
}

REPORT_STATUS_LABELS = {
    "pending": "Ожидает рассмотрения",
    "accepted": "Принята",
    "rejected": "Отклонена",
}

PROOF_TYPE_LABELS = {
    "video": "Видео",
    "image": "Изображение",
    "link": "Ссылка",
    "document": "Документ",
    "other": "Файл",
}

REVIEW_DECISION_LABELS = {
    "approved": "Одобрено",
    "rejected": "Отклонено",
}

RANKING_STATUS_LABELS = {
    "unknown": "Без рейтинга",
    "ranked": "В рейтинге",
}

ROLE_LABELS = {
    "user": "Пользователь",
    "moderator": "Модератор",
    "admin": "Администратор",
}


def _label(mapping: dict[str, str], value: object) -> str:
    if value is None:
        return "—"
    return mapping.get(str(value), str(value))


def achievement_status(value: object) -> str:
    return _label(ACHIEVEMENT_STATUS_LABELS, value)


def completion_status(value: object) -> str:
    return _label(COMPLETION_STATUS_LABELS, value)


def report_reason(value: object) -> str:
    return _label(REPORT_REASON_LABELS, value)


def report_status(value: object) -> str:
    return _label(REPORT_STATUS_LABELS, value)


def proof_type(value: object) -> str:
    return _label(PROOF_TYPE_LABELS, value)


def review_decision(value: object) -> str:
    return _label(REVIEW_DECISION_LABELS, value)


def ranking_status(value: object) -> str:
    return _label(RANKING_STATUS_LABELS, value)


def role(value: object) -> str:
    return _label(ROLE_LABELS, value)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def datetime_local(value: object, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Форматирует ISO-дату в часовом поясе отображения (по умолчанию UTC+3)."""
    parsed = _parse_datetime(value)
    if parsed is None:
        return "—"
    offset = timedelta(hours=get_settings().display_utc_offset_hours)
    return parsed.astimezone(timezone(offset)).strftime(fmt)


def date_local(value: object) -> str:
    return datetime_local(value, "%d.%m.%Y")


def plural_ru(count: object, forms: tuple[str, str, str]) -> str:
    """Склонение существительного: 1 оценка, 2 оценки, 5 оценок."""
    try:
        number = abs(int(count))
    except (TypeError, ValueError):
        return forms[2]
    if number % 10 == 1 and number % 100 != 11:
        return forms[0]
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return forms[1]
    return forms[2]


def rating_ci(se: object, low: object, high: object, comparisons: object = 0) -> str:
    """Компактная запись погрешности BT: «± 32 · 95% ДИ 1180–1244».

    Пустая строка означает, что интервал показывать не нужно: данных мало
    (менее двух сравнений) или бутстрэп вырожден (нулевая погрешность).
    """
    try:
        count = int(comparisons)
    except (TypeError, ValueError):
        return ""
    # До применения миграции 025 колонок нет и Jinja передаёт Undefined:
    # проверяем типы, чтобы не уронить страницу.
    if count < 2 or not all(
        isinstance(value, (int, float)) for value in (se, low, high)
    ):
        return ""
    error = float(se)
    if error <= 0:
        return ""
    return f"± {round(error)} · 95% ДИ {round(low)}–{round(high)}"
