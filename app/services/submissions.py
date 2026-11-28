DUPLICATE_SUBMISSION_MESSAGE = (
    "Такая заявка уже подана — измените текст, ссылки или файлы."
)
DUPLICATE_PROOF_MESSAGE = (
    "В одной заявке нельзя прикладывать одинаковые доказательства — "
    "измените набор файлов или ссылок."
)
PROOF_REQUIRED_MESSAGE = "Приложите файл или укажите ссылку на доказательство."
# Правило отправки (D28): достаточно хотя бы одного из трёх — файл(ы), ссылка
# или комментарий. Комментарий тоже считается содержанием заявки.
SUBMISSION_CONTENT_REQUIRED_MESSAGE = (
    "Приложите файл, укажите ссылку или напишите комментарий — нужно хотя бы одно."
)
# Автор своего достижения отправляет заявку с доказательством: без файла или
# ссылки публикация невозможна (enforce_creator_proof_on_publish, миграция 013).
CREATOR_PROOF_REQUIRED_MESSAGE = (
    "Приложите файл или укажите ссылку на доказательство: без них достижение "
    "нельзя отправить на модерацию."
)
PROOF_HASH_REQUIRED_MESSAGE = (
    "Не удалось вычислить отпечаток файла. Загрузите доказательство заново."
)
INVALID_PROOF_PATH_MESSAGE = (
    "Некорректный путь файла доказательства. Загрузите файл заново."
)
INVALID_PROOF_URL_MESSAGE = (
    "Некорректная ссылка: используйте http:// или https:// без пробелов."
)
SERVICE_NOT_CONFIGURED_MESSAGE = (
    "Отправка прохождения временно недоступна: сервисный доступ к БД не настроен."
)
MIGRATION_PENDING_MESSAGE = (
    "Обновление базы данных ещё применяется. Повторите отправку чуть позже."
)
# Ограничения целостности, которые сервер отдаёт вместо доменной ошибки.
# Без явного текста пользователь видел безликое «Не удалось отправить заявку»
# и не понимал, что именно сломалось (так проявился баг с NULL в
# proofs.description — SQLSTATE 23502).
CONSTRAINT_ERROR_MESSAGES = {
    "23502": "Заявка не сохранилась: сервер отклонил данные доказательства.",
    "23514": "Заявка не прошла проверку сервера.",
    "22P02": "Некорректный формат доказательства: приложите файл или ссылку заново.",
    "22023": "Заявка не отправлена: не хватает данных.",
    "23503": "Достижение больше не существует — обновите страницу.",
}


class DuplicateSubmissionError(Exception):
    pass


def _error_text(exc: BaseException) -> str:
    return (
        " ".join(
            str(getattr(exc, name, "") or "") for name in ("message", "details", "hint")
        )
        + f" {exc}"
    )


def is_duplicate_submission(exc: BaseException) -> bool:
    if str(getattr(exc, "code", "") or "") != "23505":
        return False
    return "DUPLICATE_SUBMISSION" in _error_text(exc)


def is_duplicate_proof(exc: BaseException) -> bool:
    if str(getattr(exc, "code", "") or "") != "23505":
        return False
    text = _error_text(exc)
    return "DUPLICATE_PROOF" in text or "proofs_completion_content_unique" in text


def is_already_completed(exc: BaseException) -> bool:
    return "ALREADY_COMPLETED" in _error_text(exc)


def proof_validation_error(exc: BaseException) -> str | None:
    """Возвращает понятный текст для ошибок проверки доказательства RPC."""
    text = _error_text(exc)
    for marker, message in (
        # БД отвечает PROOF_REQUIRED, когда нет ни доказательств, ни комментария.
        ("PROOF_REQUIRED", SUBMISSION_CONTENT_REQUIRED_MESSAGE),
        ("PROOF_HASH_REQUIRED", PROOF_HASH_REQUIRED_MESSAGE),
        ("INVALID_PROOF_PATH", INVALID_PROOF_PATH_MESSAGE),
        ("INVALID_PROOF_URL", INVALID_PROOF_URL_MESSAGE),
    ):
        if marker in text:
            return message
    return None


def constraint_error(exc: BaseException) -> str | None:
    """Понятный текст для ошибок целостности, которые поднимает сама БД.

    PostgREST отдаёт их SQLSTATE-кодом (23502 — NOT NULL, 23514 — CHECK и т.п.),
    поэтому без этой таблицы пользователь получал общий текст отказа.
    """
    text = _error_text(exc)
    code = str(getattr(exc, "code", "") or "")
    if code == "42501" and "SYSTEM_ONLY" in text:
        return SERVICE_NOT_CONFIGURED_MESSAGE
    message = CONSTRAINT_ERROR_MESSAGES.get(code)
    if message is None:
        return None
    if code == "23503" and "ACHIEVEMENT_NOT_FOUND" in text:
        return None
    return message
