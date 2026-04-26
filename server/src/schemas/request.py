# src/schemas/request.py
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class GeminiModels(str, Enum):
    """Model names matching gemini-webapi >= 2.0.0."""

    PRO_3 = "gemini-3-pro"
    FLASH_3 = "gemini-3-flash"
    FLASH_3_THINKING = "gemini-3-flash-thinking"
    PRO_3_PLUS = "gemini-3-pro-plus"
    FLASH_3_PLUS = "gemini-3-flash-plus"
    FLASH_3_THINKING_PLUS = "gemini-3-flash-thinking-plus"
    PRO_3_ADVANCED = "gemini-3-pro-advanced"
    FLASH_3_ADVANCED = "gemini-3-flash-advanced"
    FLASH_3_THINKING_ADVANCED = "gemini-3-flash-thinking-advanced"
    UNSPECIFIED = "unspecified"


class GeminiRequest(BaseModel):
    message: str
    model: GeminiModels = Field(default=GeminiModels.FLASH_3_PLUS, description="Model to use for Gemini.")
    files: Optional[List[str]] = []


class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[str] = None
    stream: Optional[bool] = False
    tools: Optional[List[dict]] = None
    tool_choice: Optional[object] = None


class Part(BaseModel):
    text: str


class Content(BaseModel):
    parts: List[Part]


class GoogleGenerativeRequest(BaseModel):
    contents: List[Content]
