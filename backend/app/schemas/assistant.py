from pydantic import BaseModel, Field


class AssistantSource(BaseModel):
    sourceId: str = Field(min_length=1, max_length=96)
    assetId: str = Field(min_length=1)
    assetTitle: str | None = None
    transcriptRowId: str = Field(min_length=1)
    segmentIndex: int | None = None
    createdAt: str | None = None
    text: str = Field(min_length=1, max_length=2000)


class AssistantAnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    sources: list[AssistantSource] = Field(default_factory=list, max_length=10)


class AssistantAnswerResponse(BaseModel):
    answer: str = Field(min_length=1)
    citedSourceIds: list[str] = Field(default_factory=list, max_length=10)
    insufficientContext: bool
