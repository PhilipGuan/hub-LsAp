from pydantic import BaseModel, Field

class IntentRequest(BaseModel):
    request_id: str = Field(default="")
    text: str = Field(min_length=1, max_length=200)

class IntentResponse(BaseModel):
    request_id: str
    text: str
    intent: str
    confidence: float
    method: str
