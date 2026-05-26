import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator


_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
_RESERVED = {"default", "system", "admin", "root"}


def validate_agent_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(
            "name must be lowercase, 3-31 chars, start with a letter, "
            "and contain only letters, digits, or hyphens"
        )
    if name in _RESERVED:
        raise ValueError(f"name '{name}' is reserved")


class CreateAgent(BaseModel):
    name: str
    displayName: Optional[str] = None
    color: Optional[str] = None
    provider: str
    model: str
    apiKey: str = Field(min_length=1)
    systemPrompt: Optional[str] = None
    enabledSkills: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        validate_agent_name(v)
        return v


class UpdateAgent(BaseModel):
    displayName: Optional[str] = None
    color: Optional[str] = None
    model: Optional[str] = None
    systemPrompt: Optional[str] = None
    enabledSkills: Optional[list[str]] = None
    apiKey: Optional[str] = None


class Agent(BaseModel):
    id: str
    displayName: str
    color: str
    provider: str
    model: str
    gatewayPort: int
    dashboardPort: int
    systemPrompt: Optional[str] = None
    enabledSkills: list[str] = Field(default_factory=list)
