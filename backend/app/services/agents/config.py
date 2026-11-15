from __future__ import annotations

import os
from typing import Any

from ...config import Settings
from .contracts import (
    AgentModelConfig,
    ModelCapability,
    ModelProfile,
    ModelRole,
    ProviderProfile,
    ROLE_REQUIRED_CAPABILITIES,
    RoleRoute,
)
from .secrets import secret_store_for


AGENT_MODEL_CONFIG_KEY = "agent_model_config"
LEGACY_MODEL_CONFIG_KEY = "model_config"
DEFAULT_PROVIDER_ID = "default"
DEFAULT_SECRET_REF = "model-provider/default"
ENV_SECRET_PREFIX = "env:"


def _default_capabilities(model: str) -> set[ModelCapability]:
    lower = model.lower()
    capabilities = {
        ModelCapability.TOOL_CALLING,
        ModelCapability.STRUCTURED_OUTPUT,
    }
    if "deepseek" not in lower:
        # This preserves the established execution behavior while the desktop
        # configuration UI gains explicit capability editing.
        capabilities.add(ModelCapability.VISION)
    return capabilities


def _model_protocol(model: str) -> str:
    return "deepseek_chat" if "deepseek" in model.lower() else "openai_chat"


def build_agent_model_config(
    *,
    provider_name: str,
    base_url: str,
    models: list[str],
    reasoning_effort: str,
    secret_ref: str | None,
    capabilities_by_model: dict[str, list[str]] | None = None,
    role_models: dict[str, list[str]] | None = None,
    max_concurrency: int = 2,
    requests_per_minute: int = 30,
    tokens_per_minute: int = 0,
    queue_limit: int = 50,
    cooldown_seconds: float = 30,
) -> AgentModelConfig:
    clean_models: list[str] = []
    for value in models:
        model = str(value).strip()
        if model and model not in clean_models:
            clean_models.append(model)
    if not clean_models:
        raise ValueError("至少配置一个模型")

    provider = ProviderProfile(
        id=DEFAULT_PROVIDER_ID,
        name=str(provider_name or "custom").strip() or "custom",
        base_url=base_url,
        secret_ref=secret_ref,
        max_concurrency=max_concurrency,
        requests_per_minute=requests_per_minute,
        tokens_per_minute=tokens_per_minute,
        queue_limit=queue_limit,
        cooldown_seconds=cooldown_seconds,
    )
    profiles: list[ModelProfile] = []
    by_name: dict[str, ModelProfile] = {}
    for model in clean_models:
        raw_capabilities = (capabilities_by_model or {}).get(model)
        capabilities = (
            {ModelCapability(str(item)) for item in raw_capabilities}
            if raw_capabilities is not None
            else _default_capabilities(model)
        )
        profile = ModelProfile(
            id=f"{provider.id}/{model}",
            provider_id=provider.id,
            model=model,
            protocol=_model_protocol(model),
            capabilities=frozenset(capabilities),
        )
        profiles.append(profile)
        by_name[model] = profile

    routes: dict[ModelRole, RoleRoute] = {}
    explicit_routes = {
        str(role): [str(item) for item in values]
        for role, values in (role_models or {}).items()
    }
    for role in ModelRole:
        selected_names = explicit_routes.get(role.value)
        if selected_names is None:
            selected_names = [
                model
                for model in clean_models
                if ROLE_REQUIRED_CAPABILITIES[role]
                <= by_name[model].capabilities
            ]
        selected_profiles: list[ModelProfile] = []
        for model in selected_names:
            profile = by_name.get(model)
            if profile is None:
                raise ValueError(f"角色 {role.value} 引用了未配置的模型：{model}")
            if profile not in selected_profiles:
                selected_profiles.append(profile)
        if not selected_profiles:
            continue
        routes[role] = RoleRoute(
            role=role,
            models=[profile.id for profile in selected_profiles],
            allow_cross_provider=False,
            reserved=role == ModelRole.ASSISTANT,
        )

    return AgentModelConfig(
        providers=[provider],
        models=profiles,
        routes=routes,
        reasoning_effort=reasoning_effort,
    )


def legacy_view(config: AgentModelConfig) -> dict[str, Any]:
    provider = sorted(config.providers, key=lambda item: item.priority)[0]
    profiles = [item for item in config.models if item.provider_id == provider.id]
    roles = {
        str(role.value): [
            config.model(model_id).model
            for model_id in route.models
        ]
        for role, route in config.routes.items()
    }
    return {
        "provider_name": provider.name,
        "base_url": provider.base_url,
        "models": [item.model for item in profiles],
        "reasoning_effort": config.reasoning_effort,
        "roles": roles,
        "capabilities": {
            item.model: sorted(capability.value for capability in item.capabilities)
            for item in profiles
        },
        "max_concurrency": provider.max_concurrency,
        "requests_per_minute": provider.requests_per_minute,
        "tokens_per_minute": provider.tokens_per_minute,
        "queue_limit": provider.queue_limit,
        "cooldown_seconds": provider.cooldown_seconds,
        "api_key_configured": bool(provider.secret_ref),
    }


def _store_config(database, config: AgentModelConfig) -> None:
    database.set_setting(AGENT_MODEL_CONFIG_KEY, config.model_dump(mode="json"))
    # The compatibility setting must never contain the credential itself.
    database.set_setting(LEGACY_MODEL_CONFIG_KEY, legacy_view(config))


def load_agent_model_config(
    database,
    settings: Settings,
    *,
    secret_store=None,
) -> AgentModelConfig | None:
    stored = database.get_setting(AGENT_MODEL_CONFIG_KEY, None)
    if stored:
        config = AgentModelConfig.model_validate(stored)
        legacy = database.get_setting(LEGACY_MODEL_CONFIG_KEY, {}) or {}
        if isinstance(legacy, dict) and "api_key" in legacy:
            # Compatibility for callers that still write the legacy object
            # directly. The new Settings API always uses the canonical config.
            legacy_key = str(legacy.get("api_key") or "").strip()
            if legacy_key:
                (secret_store or secret_store_for(settings)).put(
                    DEFAULT_SECRET_REF,
                    legacy_key,
                )
                secret_ref = DEFAULT_SECRET_REF
            else:
                secret_ref = None
            providers = [
                provider.model_copy(update={"secret_ref": secret_ref})
                if provider.id == DEFAULT_PROVIDER_ID
                else provider
                for provider in config.providers
            ]
            config = config.model_copy(update={"providers": providers})
            _store_config(database, config)
        return config

    store = secret_store or secret_store_for(settings)
    legacy = database.get_setting(LEGACY_MODEL_CONFIG_KEY, {}) or {}
    legacy_api_key = str(legacy.get("api_key") or "").strip() if isinstance(legacy, dict) else ""
    if legacy or settings.openai_api_key:
        secret_ref: str | None = None
        if legacy_api_key:
            store.put(DEFAULT_SECRET_REF, legacy_api_key)
            secret_ref = DEFAULT_SECRET_REF
        elif settings.openai_api_key:
            secret_ref = ENV_SECRET_PREFIX + "OPENAI_API_KEY"
        models = [
            str(item).strip()
            for item in (legacy.get("models") or [settings.openai_model])
            if str(item).strip()
        ]
        if settings.openai_fallback_model and settings.openai_fallback_model not in models:
            models.append(settings.openai_fallback_model)
        role_models = legacy.get("roles") if isinstance(legacy, dict) else None
        capabilities = legacy.get("capabilities") if isinstance(legacy, dict) else None
        config = build_agent_model_config(
            provider_name=str(legacy.get("provider_name") or "custom"),
            base_url=str(legacy.get("base_url") or settings.openai_base_url),
            models=models,
            reasoning_effort=str(legacy.get("reasoning_effort") or settings.reasoning_effort),
            secret_ref=secret_ref,
            capabilities_by_model=capabilities if isinstance(capabilities, dict) else None,
            role_models=role_models if isinstance(role_models, dict) else None,
            max_concurrency=int(legacy.get("max_concurrency") or 2),
            requests_per_minute=int(legacy.get("requests_per_minute") or 30),
            tokens_per_minute=int(legacy.get("tokens_per_minute") or 0),
            queue_limit=int(legacy.get("queue_limit") or 50),
            cooldown_seconds=float(legacy.get("cooldown_seconds") or 30),
        )
        _store_config(database, config)
        return config
    return None


def save_agent_model_config(
    database,
    settings: Settings,
    *,
    provider_name: str,
    base_url: str,
    api_key: str,
    models: list[str],
    reasoning_effort: str,
    capabilities_by_model: dict[str, list[str]] | None = None,
    role_models: dict[str, list[str]] | None = None,
    max_concurrency: int = 2,
    requests_per_minute: int = 30,
    tokens_per_minute: int = 0,
    queue_limit: int = 50,
    cooldown_seconds: float = 30,
    secret_store=None,
) -> AgentModelConfig:
    store = secret_store or secret_store_for(settings)
    existing = load_agent_model_config(database, settings, secret_store=store)
    existing_ref = existing.providers[0].secret_ref if existing and existing.providers else None
    new_key = str(api_key or "").strip()
    if new_key:
        store.put(DEFAULT_SECRET_REF, new_key)
        secret_ref = DEFAULT_SECRET_REF
    elif existing_ref:
        secret_ref = existing_ref
    elif settings.openai_api_key:
        secret_ref = ENV_SECRET_PREFIX + "OPENAI_API_KEY"
    else:
        secret_ref = None
    config = build_agent_model_config(
        provider_name=provider_name,
        base_url=base_url,
        models=models,
        reasoning_effort=reasoning_effort,
        secret_ref=secret_ref,
        capabilities_by_model=capabilities_by_model,
        role_models=role_models,
        max_concurrency=max_concurrency,
        requests_per_minute=requests_per_minute,
        tokens_per_minute=tokens_per_minute,
        queue_limit=queue_limit,
        cooldown_seconds=cooldown_seconds,
    )
    _store_config(database, config)
    return config


def resolve_provider_secret(
    database,
    settings: Settings,
    provider: ProviderProfile,
    *,
    secret_store=None,
) -> str:
    if not provider.secret_ref:
        return ""
    if provider.secret_ref.startswith(ENV_SECRET_PREFIX):
        name = provider.secret_ref[len(ENV_SECRET_PREFIX):]
        return os.getenv(name, "") or (
            settings.openai_api_key if name == "OPENAI_API_KEY" else ""
        )
    store = secret_store or secret_store_for(settings)
    return store.get(provider.secret_ref)
