from typing import Any, Literal

from litestar.openapi.datastructures import ResponseSpec
from pydantic import BaseModel, ConfigDict


class OpenAIErrorBody(BaseModel):
    """OpenAI-compat error envelope produced by `_http_exc_handler`."""
    message: str
    type: str
    param: str | None = None
    code: str | None = None


class OpenAIErrorResponse(BaseModel):
    error: OpenAIErrorBody


def err_response(description: str) -> ResponseSpec:
    """Shorthand for an OpenAI-shaped error response in OpenAPI metadata."""
    return ResponseSpec(data_container=OpenAIErrorResponse, description=description)


class ChatMessage(BaseModel):
    """One entry in the OpenAI `messages[]` array. Strict on `role`,
    permissive on extras so vendor SDKs that smuggle extra keys don't 422."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict] | None = None
    name: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


class OpenAIChatRequest(BaseModel):
    """Subset of the OpenAI Chat Completions schema. Sampling/control fields
    are accepted but not forwarded — declared explicitly so OpenAPI + the
    `[REQ.IGNORED]` warn log surface the no-op instead of silently dropping it."""

    messages: list[ChatMessage]
    model: str | None = None
    stream: bool | None = False
    tools: list[dict] | None = None
    # OpenAI spec: "none" | "auto" | "required" | {"type":"function","function":{"name":"..."}}
    tool_choice: str | dict | None = None

    # --- accepted-but-ignored sampling / control params ---
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_tokens: int | None = None
    n: int | None = None
    seed: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    response_format: dict | None = None
    user: str | None = None
    stop: str | list[str] | None = None
    logit_bias: dict | None = None
    parallel_tool_calls: bool | None = None
    metadata: Any | None = None
