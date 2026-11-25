DUPLICATE_SUBMISSION_MESSAGE = (
    "Такая заявка уже подана — измените текст, ссылки или файлы."
)
DUPLICATE_PROOF_MESSAGE = (
    "В одной заявке нельзя прикладывать одинаковые доказательства — "
    "измените набор файлов или ссылок."
)
PROOF_REQUIRED_MESSAGE = "Приложите файл или укажите ссылку на доказательство."
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
        ("PROOF_REQUIRED", PROOF_REQUIRED_MESSAGE),
        ("PROOF_HASH_REQUIRED", PROOF_HASH_REQUIRED_MESSAGE),
        ("INVALID_PROOF_PATH", INVALID_PROOF_PATH_MESSAGE),
        ("INVALID_PROOF_URL", INVALID_PROOF_URL_MESSAGE),
    ):
        if marker in text:
            return message
    return None
