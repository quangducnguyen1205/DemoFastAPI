import json
import logging
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.config.settings import settings
from app.schemas.assistant import AssistantAnswerRequest, AssistantAnswerResponse, AssistantSource

logger = logging.getLogger(__name__)

MAX_LOGGED_PROVIDER_KEYS = 20

ASSISTANT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "minLength": 1},
        "citedSourceIds": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 10,
        },
        "insufficientContext": {"type": "boolean"},
    },
    "required": ["answer", "citedSourceIds", "insufficientContext"],
    "additionalProperties": False,
}


class AssistantLlmUnavailable(RuntimeError):
    pass


class OllamaAssistantClient:
    def answer(self, request: AssistantAnswerRequest) -> AssistantAnswerResponse:
        self._ensure_enabled()
        payload = {
            "model": settings.ASSISTANT_OLLAMA_MODEL,
            "prompt": self._build_prompt(request),
            "stream": False,
            "format": ASSISTANT_RESPONSE_SCHEMA,
            "options": {"temperature": 0},
        }
        ollama_response, provider_elapsed_ms = self._post_generate(payload)
        return self._parse_structured_response(ollama_response, provider_elapsed_ms)

    def _ensure_enabled(self) -> None:
        if not settings.ASSISTANT_LLM_ENABLED:
            raise AssistantLlmUnavailable("assistant LLM is disabled")
        if not settings.ASSISTANT_OLLAMA_BASE_URL.strip():
            raise AssistantLlmUnavailable("assistant Ollama base URL is not configured")
        if not settings.ASSISTANT_OLLAMA_MODEL.strip():
            raise AssistantLlmUnavailable("assistant Ollama model is not configured")

    def _post_generate(self, payload: dict) -> tuple[dict, int]:
        url = urljoin(settings.ASSISTANT_OLLAMA_BASE_URL.rstrip("/") + "/", "api/generate")
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started_at = time.perf_counter()
        try:
            with urlopen(request, timeout=settings.ASSISTANT_OLLAMA_TIMEOUT_SECONDS) as response:
                response_body = response.read()
                try:
                    return json.loads(response_body.decode("utf-8")), self._elapsed_ms(started_at)
                except json.JSONDecodeError as exc:
                    self._log_provider_failure(
                        "assistant_ollama_invalid_provider_json",
                        started_at,
                        provider_response_body_length=len(response_body),
                    )
                    raise AssistantLlmUnavailable("assistant Ollama request failed") from exc
        except HTTPError as exc:
            self._log_provider_failure(
                "assistant_ollama_http_error",
                started_at,
                provider_http_status=exc.code,
                provider_response_body_length=self._http_error_body_length(exc),
            )
            raise AssistantLlmUnavailable("assistant Ollama request failed") from exc
        except URLError as exc:
            event = "assistant_ollama_timeout" if self._is_timeout_exception(exc) else "assistant_ollama_connection_error"
            self._log_provider_failure(event, started_at)
            raise AssistantLlmUnavailable("assistant Ollama request failed") from exc
        except (TimeoutError, OSError) as exc:
            event = "assistant_ollama_timeout" if self._is_timeout_exception(exc) else "assistant_ollama_connection_error"
            self._log_provider_failure(event, started_at)
            raise AssistantLlmUnavailable("assistant Ollama request failed") from exc

    def _parse_structured_response(self, ollama_response: dict, provider_elapsed_ms: int) -> AssistantAnswerResponse:
        response_text = ollama_response.get("response")
        if not isinstance(response_text, str) or not response_text.strip():
            self._log_provider_failure(
                "assistant_ollama_missing_structured_content",
                provider_elapsed_ms=provider_elapsed_ms,
                provider_response=ollama_response,
            )
            raise AssistantLlmUnavailable("assistant Ollama response did not include structured content")
        try:
            return AssistantAnswerResponse.model_validate_json(response_text)
        except (ValidationError, ValueError) as exc:
            self._log_provider_failure(
                self._structured_response_error_event(response_text),
                provider_elapsed_ms=provider_elapsed_ms,
                provider_response=ollama_response,
            )
            raise AssistantLlmUnavailable("assistant Ollama response did not match the answer contract") from exc

    def _log_provider_failure(
        self,
        event: str,
        started_at: float | None = None,
        *,
        provider_elapsed_ms: int | None = None,
        provider_response: object | None = None,
        provider_http_status: int | None = None,
        provider_response_body_length: int | None = None,
    ) -> None:
        try:
            context = self._safe_provider_summary(provider_response)
            context.update({
                "event": event,
                "model": settings.ASSISTANT_OLLAMA_MODEL,
                "elapsed_ms": provider_elapsed_ms if provider_elapsed_ms is not None else self._elapsed_ms(started_at),
                "provider_http_status": provider_http_status,
                "provider_response_body_length": provider_response_body_length,
            })
            logger.warning("assistant Ollama provider diagnostic context=%s", context)
        except Exception:
            logger.warning("assistant Ollama provider diagnostic failed event=%s", event)

    def _safe_provider_summary(self, provider_response: object | None) -> dict:
        try:
            response = provider_response if isinstance(provider_response, dict) else {}
            message = response.get("message") if isinstance(response.get("message"), dict) else {}
            message_content = message.get("content") if "content" in message else None
            response_field = response.get("response") if "response" in response else None
            error_field = response.get("error") if "error" in response else None
            return {
                "payload_top_level_keys": self._safe_keys(response),
                "message_field_present": "message" in response,
                "message_keys": self._safe_keys(message),
                "message_content_present": "content" in message,
                "message_content_type": self._type_name(message_content) if "content" in message else None,
                "message_content_length": self._safe_length(message_content),
                "response_field_present": "response" in response,
                "response_field_type": self._type_name(response_field) if "response" in response else None,
                "response_field_length": self._safe_length(response_field),
                "done_present": "done" in response,
                "done_reason_present": "done_reason" in response,
                "error_field_present": "error" in response,
                "error_field_type": self._type_name(error_field) if "error" in response else None,
            }
        except Exception:
            return {
                "payload_top_level_keys": [],
                "message_field_present": False,
                "message_keys": [],
                "message_content_present": False,
                "message_content_type": None,
                "message_content_length": None,
                "response_field_present": False,
                "response_field_type": None,
                "response_field_length": None,
                "done_present": False,
                "done_reason_present": False,
                "error_field_present": False,
                "error_field_type": None,
            }

    def _safe_keys(self, value: dict) -> list[str]:
        try:
            return sorted(str(key)[:64] for key in value.keys())[:MAX_LOGGED_PROVIDER_KEYS]
        except Exception:
            return []

    def _safe_length(self, value: object | None) -> int | None:
        if isinstance(value, (str, bytes, list, tuple, dict)):
            return len(value)
        return None

    def _type_name(self, value: object | None) -> str:
        return type(value).__name__

    def _elapsed_ms(self, started_at: float | None) -> int | None:
        if started_at is None:
            return None
        return round((time.perf_counter() - started_at) * 1000)

    def _http_error_body_length(self, exc: HTTPError) -> int | None:
        try:
            content_length = exc.headers.get("Content-Length") if exc.headers else None
            return int(content_length) if content_length is not None else None
        except (TypeError, ValueError):
            return None

    def _is_timeout_exception(self, exc: BaseException) -> bool:
        reason = getattr(exc, "reason", None)
        candidates = [exc, reason]
        return any(
            isinstance(candidate, (TimeoutError, socket.timeout))
            or "timed out" in str(candidate).lower()
            or "timeout" in str(candidate).lower()
            for candidate in candidates
            if candidate is not None
        )

    def _structured_response_error_event(self, response_text: str) -> str:
        try:
            json.loads(response_text)
        except json.JSONDecodeError:
            return "assistant_ollama_invalid_structured_content_json"
        return "assistant_ollama_invalid_structured_response_shape"

    def _build_prompt(self, request: AssistantAnswerRequest) -> str:
        sources_text = "\n\n".join(self._format_source(source) for source in request.sources)
        if not sources_text:
            sources_text = "No sources were supplied."
        return f"""You are the internal grounded assistant for AI Knowledge Workspace.
Return exactly one JSON object with this shape:
{{"answer":"string","citedSourceIds":["source-id"],"insufficientContext":false}}

Rules:
- Answer only from the supplied sources, keep the answer concise, and do not use outside knowledge.
- Do not include Markdown links, invented metadata, or hidden reasoning.

Evidence selection rules:
- Read all supplied sources before answering.
- Do not assume earlier or higher-ranked sources are more factually correct.
- For factual questions, answer only when a supplied source directly states or unambiguously supports the exact requested fact.
- Cite only supplied Source ID values from sources that directly support the exact answer.
- Do not cite a source merely because it is related to the topic.
- Do not infer, substitute, blend, or generalize facts across different features, examples, or transcript segments.
- If no supplied source directly supports the exact requested fact, set insufficientContext to true, make answer a short insufficiency explanation, and set citedSourceIds to [].

Question:
{request.question}

Sources:
{sources_text}
"""

    def _format_source(self, source: AssistantSource) -> str:
        return "\n".join([
            f"Source ID: {source.sourceId}",
            f"Asset title: {source.assetTitle or ''}",
            f"Transcript row ID: {source.transcriptRowId}",
            f"Segment index: {source.segmentIndex if source.segmentIndex is not None else ''}",
            f"Created at: {source.createdAt or ''}",
            "Context:",
            source.text,
        ])


def generate_assistant_answer(request: AssistantAnswerRequest) -> AssistantAnswerResponse:
    return OllamaAssistantClient().answer(request)
