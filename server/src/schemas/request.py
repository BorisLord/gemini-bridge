from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    """One entry in the OpenAI `messages[]` array. Strict on `role`,
    permissive on extras so vendor SDKs that smuggle extra keys don't 422."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[Union[str, List[dict]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[dict]] = None
    tool_call_id: Optional[str] = None


class OpenAIChatRequest(BaseModel):
    """Subset of the OpenAI Chat Completions schema. Fields under "ignored"
    below are accepted for client compatibility but the bridge does not
    forward them to Gemini Web (gemini-webapi exposes no knob for them).
    Declaring them explicitly surfaces the no-op via the OpenAPI schema and
    in the warn log emitted at request time, instead of letting clients
    silently believe they took effect."""

    messages: List[ChatMessage]
    model: Optional[str] = None
    stream: Optional[bool] = False
    tools: Optional[List[dict]] = None
    # OpenAI spec: "none" | "auto" | "required" | {"type":"function","function":{"name":"..."}}
    tool_choice: Optional[Union[str, dict]] = None

    # --- accepted-but-ignored sampling / control params ---
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_tokens: Optional[int] = None
    n: Optional[int] = None
    seed: Optional[int] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    response_format: Optional[dict] = None
    user: Optional[str] = None
    stop: Optional[Union[str, List[str]]] = None
    logit_bias: Optional[dict] = None
    parallel_tool_calls: Optional[bool] = None
    metadata: Optional[Any] = None
