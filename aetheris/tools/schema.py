"""Pydantic schema for administrator-defined tool configurations.

This is the sole entry point through which admin JSON becomes trusted —
every field is validated here (A04 Insecure Design: trust boundary drawn
at config ingestion, not at call time).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ParameterSchema(BaseModel):
    """One JSON-Schema-style parameter definition for a dynamic tool."""

    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    type: Literal["string", "integer", "number", "boolean", "array", "object"]
    description: str = Field(min_length=1, max_length=512)
    required: bool = False
    enum: list[Any] | None = None


class AuthConfig(BaseModel):
    """Where the executor pulls credentials from at call time. Admin
    configs never carry inline secrets — only a reference to an env var
    resolved server-side (A02 Cryptographic Failures / A05 Misconfiguration)."""

    scheme: Literal["none", "bearer_env", "api_key_header_env"] = "none"
    env_var: str | None = None
    header_name: str | None = None

    @model_validator(mode="after")
    def _env_var_required_for_scheme(self) -> "AuthConfig":
        if self.scheme != "none" and not self.env_var:
            raise ValueError(f"auth scheme {self.scheme!r} requires env_var")
        if self.scheme == "api_key_header_env" and not self.header_name:
            raise ValueError("api_key_header_env requires header_name")
        return self


class AdminToolConfig(BaseModel):
    """The administrator-authored JSON contract for a dynamically
    registered tool. Fully validated before the ToolFactory ever sees it."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str = Field(min_length=10, max_length=1024)
    endpoint: str
    method: HttpMethod
    parameters: list[ParameterSchema] = Field(default_factory=list)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    tenant_id: str

    @field_validator("endpoint")
    @classmethod
    def _https_only(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("endpoint must use https://")
        return v

    @field_validator("parameters")
    @classmethod
    def _unique_parameter_names(cls, v: list[ParameterSchema]) -> list[ParameterSchema]:
        names = [p.name for p in v]
        if len(names) != len(set(names)):
            raise ValueError("parameter names must be unique")
        return v
