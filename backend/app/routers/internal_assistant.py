import logging

from fastapi import APIRouter, HTTPException

from app.schemas.assistant import AssistantAnswerRequest, AssistantAnswerResponse
from app.services.assistant_ollama import AssistantLlmUnavailable, generate_assistant_answer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/assistant", tags=["internal-assistant"])


@router.post("/answer", response_model=AssistantAnswerResponse)
def answer(request: AssistantAnswerRequest) -> AssistantAnswerResponse:
    try:
        return generate_assistant_answer(request)
    except AssistantLlmUnavailable as exc:
        logger.warning("assistant LLM unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Assistant LLM is unavailable") from exc
