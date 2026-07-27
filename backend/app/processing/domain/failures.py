import re


MAX_SAFE_ERROR_MESSAGE_LENGTH = 500
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(password|secret|token|access[_-]?key|credential)(\s*[=:]\s*)([^\s,;]+)"
)

YOUTUBE_UNAVAILABLE = "YOUTUBE_UNAVAILABLE"
YOUTUBE_LIVE_NOT_SUPPORTED = "YOUTUBE_LIVE_NOT_SUPPORTED"
YOUTUBE_DURATION_LIMIT_EXCEEDED = "YOUTUBE_DURATION_LIMIT_EXCEEDED"
YOUTUBE_SIZE_LIMIT_EXCEEDED = "YOUTUBE_SIZE_LIMIT_EXCEEDED"
YOUTUBE_ACQUISITION_TIMEOUT = "YOUTUBE_ACQUISITION_TIMEOUT"
YOUTUBE_ACQUISITION_FAILED = "YOUTUBE_ACQUISITION_FAILED"


class YouTubeAcquisitionError(RuntimeError):
    code = YOUTUBE_ACQUISITION_FAILED
    safe_message = "YouTube media acquisition failed"

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class YouTubeUnavailableError(YouTubeAcquisitionError):
    code = YOUTUBE_UNAVAILABLE
    safe_message = "YouTube video is unavailable for public unauthenticated acquisition"


class YouTubeLiveNotSupportedError(YouTubeAcquisitionError):
    code = YOUTUBE_LIVE_NOT_SUPPORTED
    safe_message = "Active or upcoming YouTube livestreams are not supported"


class YouTubeDurationLimitExceededError(YouTubeAcquisitionError):
    code = YOUTUBE_DURATION_LIMIT_EXCEEDED
    safe_message = "YouTube video exceeds the configured duration limit"


class YouTubeSizeLimitExceededError(YouTubeAcquisitionError):
    code = YOUTUBE_SIZE_LIMIT_EXCEEDED
    safe_message = "YouTube media exceeds the configured file-size limit"


class YouTubeAcquisitionTimeoutError(YouTubeAcquisitionError):
    code = YOUTUBE_ACQUISITION_TIMEOUT
    safe_message = "YouTube media acquisition exceeded the configured timeout"


def processing_failure_details(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, YouTubeAcquisitionError):
        return exc.code, exc.safe_message
    return "PROCESSING_FAILED", safe_processing_error_message(exc)


def safe_processing_error_message(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").replace("\r", " ").strip()
    message = _SENSITIVE_VALUE_PATTERN.sub(r"\1\2[redacted]", message)
    if not message:
        message = exc.__class__.__name__
    if len(message) > MAX_SAFE_ERROR_MESSAGE_LENGTH:
        message = message[: MAX_SAFE_ERROR_MESSAGE_LENGTH - 3].rstrip() + "..."
    return message
