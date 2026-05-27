import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


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


AuthMethod = Literal["api_key", "inherit"]


class CreateAgent(BaseModel):
    name: str
    displayName: Optional[str] = None
    color: Optional[str] = None
    provider: str = ""
    # Model is required for the api_key path (Hermes needs to know what to call).
    # Optional for the inherit path — host credentials determine the model implicitly.
    model: str = ""
    # `authMethod` selects how the new profile authenticates with the LLM provider.
    #   "api_key"  — orchestrator writes the supplied apiKey into the profile's .env (default).
    #   "inherit"  — skip writing provider creds; Hermes inherits whatever the host has
    #                already configured (OAuth tokens, ambient env vars, Codex/Claude Code CLI).
    authMethod: AuthMethod = "api_key"
    apiKey: Optional[str] = None
    systemPrompt: Optional[str] = None
    enabledSkills: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        validate_agent_name(v)
        return v

    @model_validator(mode="after")
    def _validate_credentials(self) -> "CreateAgent":
        if self.authMethod == "api_key":
            if not (self.apiKey or "").strip():
                raise ValueError("apiKey is required when authMethod is 'api_key'")
            if not self.model.strip():
                raise ValueError("model is required when authMethod is 'api_key'")
        return self


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
