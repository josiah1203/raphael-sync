"""Agent configuration loaded from ~/.calliope/config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from raphael_audit.core.paths import default_config_path, default_graph_db_path
from raphael_audit.core.security.jwt_config import resolve_jwt_secret


@dataclass
class AgentConfig:
    mode: str = "local"  # local | platform
    host: str = "127.0.0.1"
    port: int = 8742
    ui_port: int = 8750
    diff_budget_ms: int = 500
    user_id: str = "local-user"
    tenant_id: str = "local"
    tool_version: str = "unknown"
    encrypted_at_rest: bool = False
    structured_logging: bool = False
    platform: dict[str, Any] = field(default_factory=dict)
    webhook_secrets: dict[str, str] = field(default_factory=dict)
    api_keys: list[str] = field(default_factory=list)
    admin_api_keys: list[str] = field(default_factory=list)
    graph_backend: str = "sqlite"  # sqlite | neo4j
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: str | None = None
    graph_db_path: Path | None = None
    auth_mode: str = "open"  # open | api_key | jwt
    jwt_secret: str | None = None
    jwt_public_key: str | None = None
    jwt_issuer: str | None = None
    auth_public_read: bool = False
    tls_cert: str | None = None
    tls_key: str | None = None
    rbac: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.api_keys and self.auth_mode == "open":
            self.auth_mode = "api_key"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentConfig:
        graph_path = data.get("graph_db_path")
        return cls(
            mode=str(data.get("mode", cls.mode)),
            host=str(data.get("host", cls.host)),
            port=int(data.get("port", cls.port)),
            ui_port=int(data.get("ui_port", cls.ui_port)),
            diff_budget_ms=int(data.get("diff_budget_ms", cls.diff_budget_ms)),
            user_id=str(data.get("user_id", cls.user_id)),
            tenant_id=str(data.get("tenant_id", cls.tenant_id)),
            tool_version=str(data.get("tool_version", cls.tool_version)),
            encrypted_at_rest=bool(data.get("encrypted_at_rest", cls.encrypted_at_rest)),
            structured_logging=bool(data.get("structured_logging", cls.structured_logging)),
            platform=dict(data.get("platform", {})),
            webhook_secrets=dict(data.get("webhook_secrets", {})),
            api_keys=list(data.get("api_keys", [])),
            admin_api_keys=list(data.get("admin_api_keys", [])),
            graph_backend=str(data.get("graph_backend", cls.graph_backend)),
            neo4j_uri=data.get("neo4j_uri") or os.environ.get("NEO4J_URI"),
            neo4j_user=data.get("neo4j_user") or os.environ.get("NEO4J_USER"),
            neo4j_password=data.get("neo4j_password") or os.environ.get("NEO4J_PASSWORD"),
            graph_db_path=Path(graph_path) if graph_path else None,
            auth_mode=str(data.get("auth_mode", cls.auth_mode)),
            jwt_secret=data.get("jwt_secret"),
            jwt_public_key=data.get("jwt_public_key"),
            jwt_issuer=data.get("jwt_issuer"),
            auth_public_read=bool(data.get("auth_public_read", cls.auth_public_read)),
            tls_cert=data.get("tls_cert"),
            tls_key=data.get("tls_key"),
            rbac=dict(data.get("rbac", {})),
        )


def load_config(path: Path | None = None) -> AgentConfig:
    config_path = path or default_config_path()
    if not config_path.exists():
        config = AgentConfig()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump(
                {
                    "mode": config.mode,
                    "host": config.host,
                    "port": config.port,
                    "diff_budget_ms": config.diff_budget_ms,
                    "user_id": config.user_id,
                    "tenant_id": config.tenant_id,
                    "tool_version": config.tool_version,
                    "encrypted_at_rest": config.encrypted_at_rest,
                    "structured_logging": config.structured_logging,
                    "platform": config.platform,
                    "ui_port": config.ui_port,
                    "webhook_secrets": config.webhook_secrets,
                    "api_keys": config.api_keys,
                    "admin_api_keys": config.admin_api_keys,
                    "graph_backend": config.graph_backend,
                    "neo4j_uri": config.neo4j_uri,
                    "neo4j_user": config.neo4j_user,
                    "neo4j_password": config.neo4j_password,
                    "graph_db_path": str(config.graph_db_path or default_graph_db_path()),
                    "auth_mode": config.auth_mode,
                    "jwt_secret": config.jwt_secret,
                    "jwt_public_key": config.jwt_public_key,
                    "jwt_issuer": config.jwt_issuer,
                    "auth_public_read": config.auth_public_read,
                    "tls_cert": config.tls_cert,
                    "tls_key": config.tls_key,
                    "rbac": config.rbac,
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return config

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config = AgentConfig.from_dict(data)
    if config.auth_mode == "jwt":
        config.jwt_secret = resolve_jwt_secret(
            config_value=config.jwt_secret,
            dev_default=config.jwt_secret,
        )
    return config
