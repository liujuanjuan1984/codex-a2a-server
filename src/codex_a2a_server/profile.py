from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings

COMPATIBILITY_PROFILE_ID = "codex-a2a-single-tenant-coding-v1"
DEPLOYMENT_PROFILE_ID = "single_tenant_shared_workspace"


@dataclass(frozen=True)
class DeploymentProfile:
    id: str
    single_tenant: bool
    shared_workspace_across_consumers: bool
    tenant_isolation: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "single_tenant": self.single_tenant,
            "shared_workspace_across_consumers": self.shared_workspace_across_consumers,
            "tenant_isolation": self.tenant_isolation,
        }


@dataclass(frozen=True)
class DirectoryBindingProfile:
    allow_override: bool
    scope: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "allow_override": self.allow_override,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class SessionShellProfile:
    enabled: bool
    availability: str
    toggle: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "availability": self.availability,
            "toggle": self.toggle,
        }


@dataclass(frozen=True)
class InterruptProfile:
    request_ttl_seconds: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_ttl_seconds": self.request_ttl_seconds,
        }


@dataclass(frozen=True)
class ServiceFeaturesProfile:
    streaming: dict[str, Any]
    health_endpoint: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "streaming": dict(self.streaming),
            "health_endpoint": dict(self.health_endpoint),
        }


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    deployment: DeploymentProfile
    directory_binding: DirectoryBindingProfile
    session_shell: SessionShellProfile
    interrupts: InterruptProfile
    service_features: ServiceFeaturesProfile

    def runtime_features_dict(self) -> dict[str, Any]:
        return {
            "directory_binding": self.directory_binding.as_dict(),
            "session_shell": self.session_shell.as_dict(),
            "interrupts": self.interrupts.as_dict(),
            "service_features": self.service_features.as_dict(),
        }

    def summary_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "deployment": self.deployment.as_dict(),
            "runtime_features": self.runtime_features_dict(),
        }

    def deployment_context_dict(self) -> dict[str, Any]:
        deployment = self.deployment.as_dict()
        runtime_features = self.runtime_features_dict()
        return {
            "profile": self.summary_dict(),
            "profile_id": self.profile_id,
            "deployment_profile": deployment["id"],
            "allow_directory_override": self.directory_binding.allow_override,
            "health_endpoint_enabled": self.service_features.health_endpoint["enabled"],
            "interrupt_request_ttl_seconds": self.interrupts.request_ttl_seconds,
            "session_shell_enabled": self.session_shell.enabled,
            "single_tenant": deployment["single_tenant"],
            "shared_workspace_across_consumers": deployment["shared_workspace_across_consumers"],
            "streaming_enabled": self.service_features.streaming["enabled"],
            "tenant_isolation": deployment["tenant_isolation"],
            "runtime_features": runtime_features,
        }

    def health_payload(self, *, service: str, version: str) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": service,
            "version": version,
            "profile": self.summary_dict(),
            "streaming_enabled": self.service_features.streaming["enabled"],
            "session_shell_enabled": self.session_shell.enabled,
            "interrupt_request_ttl_seconds": self.interrupts.request_ttl_seconds,
        }


def build_runtime_profile(settings: Settings) -> RuntimeProfile:
    deployment = DeploymentProfile(
        id=DEPLOYMENT_PROFILE_ID,
        single_tenant=True,
        shared_workspace_across_consumers=True,
        tenant_isolation="none",
    )
    directory_scope = (
        "workspace_root_or_descendant"
        if settings.a2a_allow_directory_override
        else "workspace_root_only"
    )
    return RuntimeProfile(
        profile_id=COMPATIBILITY_PROFILE_ID,
        deployment=deployment,
        directory_binding=DirectoryBindingProfile(
            allow_override=settings.a2a_allow_directory_override,
            scope=directory_scope,
        ),
        session_shell=SessionShellProfile(
            enabled=settings.a2a_enable_session_shell,
            availability="enabled" if settings.a2a_enable_session_shell else "disabled",
            toggle="A2A_ENABLE_SESSION_SHELL",
        ),
        interrupts=InterruptProfile(
            request_ttl_seconds=settings.a2a_interrupt_request_ttl_seconds,
        ),
        service_features=ServiceFeaturesProfile(
            streaming={"enabled": True, "availability": "always"},
            health_endpoint={
                "enabled": settings.a2a_enable_health_endpoint,
                "availability": ("enabled" if settings.a2a_enable_health_endpoint else "disabled"),
                "toggle": "A2A_ENABLE_HEALTH_ENDPOINT",
            },
        ),
    )
