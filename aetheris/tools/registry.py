"""Dynamic Tool Registry: converts administrator JSON configs into
executable LangChain tools at runtime setup.

`ToolFactory` is a Factory (config -> executable tool synthesis only);
network execution is delegated to `SandboxedHTTPExecutor` and auth
resolution to a dedicated static method — three single-responsibility
units instead of one that does everything.
"""
from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, create_model

from aetheris.tools.sandbox import SandboxedHTTPExecutor
from aetheris.tools.schema import AdminToolConfig, AuthConfig

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class ToolFactory:
    def __init__(self, executor: SandboxedHTTPExecutor) -> None:
        self._executor = executor

    def build(self, config: AdminToolConfig) -> StructuredTool:
        args_model = self._build_args_model(config)

        def _run(**kwargs: Any) -> dict:
            headers = self._resolve_auth_headers(config.auth)
            is_get = config.method.value == "GET"
            body = None if is_get else kwargs
            url = config.endpoint
            if is_get and kwargs:
                query = "&".join(f"{k}={v}" for k, v in kwargs.items())
                url = f"{url}?{query}"
            return self._executor.execute(
                url=url,
                method=config.method.value,
                headers=headers,
                json_body=body,
                timeout_seconds=config.timeout_seconds,
            )

        return StructuredTool(
            name=config.name,
            description=config.description,
            args_schema=args_model,
            func=_run,
        )

    @staticmethod
    def _build_args_model(config: AdminToolConfig) -> type[BaseModel]:
        fields: dict[str, Any] = {}
        for param in config.parameters:
            py_type = _TYPE_MAP[param.type]
            if param.required:
                fields[param.name] = (py_type, ...)
            else:
                fields[param.name] = (py_type | None, None)
        return create_model(f"{config.name}_Args", **fields)

    @staticmethod
    def _resolve_auth_headers(auth: AuthConfig) -> dict[str, str]:
        if auth.scheme == "none":
            return {}
        secret = os.environ.get(auth.env_var or "", "")
        if not secret:
            raise ValueError(f"missing secret in env var {auth.env_var!r}")
        if auth.scheme == "bearer_env":
            return {"Authorization": f"Bearer {secret}"}
        if auth.scheme == "api_key_header_env":
            return {auth.header_name: secret}  # type: ignore[dict-item]
        return {}


class ToolRegistry:
    """Per-tenant lookup of compiled tools, built once at session/process
    setup from admin JSON configs (kept out of the request hot path)."""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], StructuredTool] = {}

    def register_from_configs(
        self,
        raw_configs: list[dict[str, Any]],
        *,
        tenant_id: str,
        domain_allowlist: frozenset[str],
    ) -> None:
        factory = ToolFactory(SandboxedHTTPExecutor(domain_allowlist=domain_allowlist))
        for raw in raw_configs:
            config = AdminToolConfig.model_validate({**raw, "tenant_id": tenant_id})
            self._tools[(tenant_id, config.name)] = factory.build(config)

    def get(self, name: str, *, tenant_id: str) -> StructuredTool | None:
        return self._tools.get((tenant_id, name))

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.args_schema.model_json_schema() if t.args_schema else {},
            }
            for t in self._tools.values()
        ]
