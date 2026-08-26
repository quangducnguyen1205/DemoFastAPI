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
    diagnostic_family = "youtube_acquisition_unknown"
    retryable = True

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class YouTubeUnavailableError(YouTubeAcquisitionError):
    code = YOUTUBE_UNAVAILABLE
    safe_message = "YouTube video is unavailable for public unauthenticated acquisition"
    diagnostic_family = "youtube_unavailable"
    retryable = False


class YouTubeLiveNotSupportedError(YouTubeAcquisitionError):
    code = YOUTUBE_LIVE_NOT_SUPPORTED
    safe_message = "Active or upcoming YouTube livestreams are not supported"
    diagnostic_family = "youtube_live_not_supported"
    retryable = False


class YouTubeDurationLimitExceededError(YouTubeAcquisitionError):
    code = YOUTUBE_DURATION_LIMIT_EXCEEDED
    safe_message = "YouTube video exceeds the configured duration limit"
    diagnostic_family = "youtube_duration_limit_exceeded"
    retryable = False


class YouTubeSizeLimitExceededError(YouTubeAcquisitionError):
    code = YOUTUBE_SIZE_LIMIT_EXCEEDED
    safe_message = "YouTube media exceeds the configured file-size limit"
    diagnostic_family = "youtube_size_limit_exceeded"
    retryable = False


class YouTubeAcquisitionTimeoutError(YouTubeAcquisitionError):
    code = YOUTUBE_ACQUISITION_TIMEOUT
    safe_message = "YouTube media acquisition exceeded the configured timeout"
    diagnostic_family = "youtube_acquisition_timeout"
    retryable = False


class YouTubePoTokenProviderUnavailableError(YouTubeAcquisitionError):
    diagnostic_family = "youtube_pot_provider_unavailable"


class YouTubeGvsForbiddenError(YouTubeAcquisitionError):
    diagnostic_family = "youtube_gvs_forbidden"


class YouTubeRateLimitedError(YouTubeAcquisitionError):
    diagnostic_family = "youtube_rate_limited"


class YouTubeNetworkTransientError(YouTubeAcquisitionError):
    diagnostic_family = "youtube_network_transient"


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
