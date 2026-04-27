from typing import List, Optional, Union
from pydantic import BaseModel


class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[str] = None
    stream: Optional[bool] = False
    tools: Optional[List[dict]] = None
    # OpenAI spec: "none" | "auto" | "required" | {"type":"function","function":{"name":"..."}}
    tool_choice: Optional[Union[str, dict]] = None
