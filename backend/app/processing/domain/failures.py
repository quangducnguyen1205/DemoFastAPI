import re


MAX_SAFE_ERROR_MESSAGE_LENGTH = 500
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(password|secret|token|access[_-]?key|credential)(\s*[=:]\s*)([^\s,;]+)"
)


def safe_processing_error_message(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").replace("\r", " ").strip()
    message = _SENSITIVE_VALUE_PATTERN.sub(r"\1\2[redacted]", message)
    if not message:
        message = exc.__class__.__name__
    if len(message) > MAX_SAFE_ERROR_MESSAGE_LENGTH:
        message = message[: MAX_SAFE_ERROR_MESSAGE_LENGTH - 3].rstrip() + "..."
    return message
