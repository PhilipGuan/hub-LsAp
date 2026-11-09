from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: Literal["hallway", "non_hallway"]
    risk_level: Literal["none", "low", "medium", "high"] | None
    evidence: list[str] = Field(default_factory=list, max_length=4)
    recommendation: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def clear_risk_for_non_hallway(self) -> "RiskAssessment":
        if self.scene == "non_hallway" and self.risk_level is not None:
            raise ValueError("non-hallway images must not receive a hallway risk level")
        if self.scene == "hallway" and self.risk_level is None:
            raise ValueError("hallway images must receive a risk level")
        return self
