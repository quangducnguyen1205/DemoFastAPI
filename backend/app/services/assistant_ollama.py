import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from pydantic import ValidationError

from app.config.settings import settings
from app.schemas.assistant import AssistantAnswerRequest, AssistantAnswerResponse, AssistantSource

logger = logging.getLogger(__name__)

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
        ollama_response = self._post_generate(payload)
        return self._parse_structured_response(ollama_response)

    def _ensure_enabled(self) -> None:
        if not settings.ASSISTANT_LLM_ENABLED:
            raise AssistantLlmUnavailable("assistant LLM is disabled")
        if not settings.ASSISTANT_OLLAMA_BASE_URL.strip():
            raise AssistantLlmUnavailable("assistant Ollama base URL is not configured")
        if not settings.ASSISTANT_OLLAMA_MODEL.strip():
            raise AssistantLlmUnavailable("assistant Ollama model is not configured")

    def _post_generate(self, payload: dict) -> dict:
        url = urljoin(settings.ASSISTANT_OLLAMA_BASE_URL.rstrip("/") + "/", "api/generate")
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=settings.ASSISTANT_OLLAMA_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise AssistantLlmUnavailable("assistant Ollama request failed") from exc

    def _parse_structured_response(self, ollama_response: dict) -> AssistantAnswerResponse:
        response_text = ollama_response.get("response")
        if not isinstance(response_text, str) or not response_text.strip():
            raise AssistantLlmUnavailable("assistant Ollama response did not include structured content")
        try:
            return AssistantAnswerResponse.model_validate_json(response_text)
        except (ValidationError, ValueError) as exc:
            raise AssistantLlmUnavailable("assistant Ollama response did not match the answer contract") from exc

    def _build_prompt(self, request: AssistantAnswerRequest) -> str:
        sources_text = "\n\n".join(self._format_source(source) for source in request.sources)
        if not sources_text:
            sources_text = "No sources were supplied."
        return f"""You are the internal grounded assistant for AI Knowledge Workspace.
Return exactly one JSON object with this shape:
{{"answer":"string","citedSourceIds":["source-id"],"insufficientContext":false}}

Rules:
- Answer only from the supplied sources.
- Do not use outside knowledge.
- If the sources are insufficient, set insufficientContext to true and citedSourceIds to [].
- For a normal answer, cite only supplied Source ID values.
- Do not include Markdown links, invented metadata, or hidden reasoning.

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
