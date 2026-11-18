from __future__ import annotations

import os
import re
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


def _model_protocol(
    model: str,
    *,
    api_format: str = "openai",
    use_responses_api: bool = False,
) -> str:
    if api_format == "anthropic":
        return "anthropic_messages"
    if use_responses_api:
        return "openai_responses"
    return "deepseek_chat" if "deepseek" in model.lower() else "openai_chat"


def provider_secret_ref(provider_id: str) -> str:
    return f"model-provider/{provider_id}"


def _slugify_provider(name: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower()).strip("-")
    base = (base or "provider")[:48]
    if not re.match(r"^[a-z0-9]", base):
        base = f"p{base}"[:48]
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


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
    api_format: str = "openai",
    use_responses_api: bool = False,
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
        api_format=api_format,
        use_responses_api=use_responses_api,
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
            protocol=_model_protocol(
                model,
                api_format=api_format,
                use_responses_api=use_responses_api,
            ),
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


def build_multi_provider_config(
    *,
    providers: list[dict[str, Any]],
    reasoning_effort: str,
    capabilities_by_model: dict[str, list[str]] | None = None,
    role_models: dict[str, list[str]] | None = None,
) -> AgentModelConfig:
    """按列表顺序构建供应商与模型优先级。

    角色路由把各供应商的模型按「供应商顺序 → 供应商内模型顺序」串联，并开启跨服务
    回退：同一供应商内先逐个模型尝试，全部不可用时才换到下一个供应商。
    """
    if not providers:
        raise ValueError("至少配置一个模型服务")
    used: set[str] = set()
    provider_profiles: list[ProviderProfile] = []
    profiles: list[ModelProfile] = []
    by_name: dict[str, ModelProfile] = {}
    for index, raw in enumerate(providers):
        if not isinstance(raw, dict):
            raise ValueError("模型服务配置格式不正确")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError("供应商名称不能为空")
        base_url = str(raw.get("base_url") or "").strip()
        if not base_url:
            raise ValueError(f"供应商 {name} 缺少接口地址")
        api_format = str(raw.get("api_format") or "openai").strip().lower()
        if api_format not in {"openai", "anthropic"}:
            raise ValueError(f"供应商 {name} 的 API 格式不受支持")
        use_responses_api = bool(raw.get("use_responses_api"))
        if api_format == "anthropic" and use_responses_api:
            raise ValueError(f"供应商 {name} 使用 Anthropic 格式时不支持 Responses API")
        raw_id = str(raw.get("id") or "").strip().lower()
        if raw_id:
            if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", raw_id):
                raise ValueError(f"供应商标识 {raw_id} 不合法")
            provider_id = raw_id if raw_id not in used else _slugify_provider(raw_id, used)
            used.add(provider_id)
        else:
            provider_id = _slugify_provider(name, used)
        secret_ref = raw.get("secret_ref")
        provider_profiles.append(
            ProviderProfile(
                id=provider_id,
                name=name,
                base_url=base_url.rstrip("/"),
                secret_ref=str(secret_ref) if secret_ref else None,
                priority=index,
                api_format=api_format,
                use_responses_api=use_responses_api,
                max_concurrency=int(raw.get("max_concurrency") or 2),
                requests_per_minute=int(raw.get("requests_per_minute") or 30),
                tokens_per_minute=int(raw.get("tokens_per_minute") or 0),
                queue_limit=int(raw.get("queue_limit") or 50),
                cooldown_seconds=float(raw.get("cooldown_seconds") or 30),
            )
        )
        for value in raw.get("models") or []:
            model = str(value).strip()
            if not model:
                continue
            profile_id = f"{provider_id}/{model}"
            if any(item.id == profile_id for item in profiles):
                continue
            raw_capabilities = (capabilities_by_model or {}).get(model)
            capabilities = (
                {ModelCapability(str(item)) for item in raw_capabilities}
                if raw_capabilities is not None
                else _default_capabilities(model)
            )
            profile = ModelProfile(
                id=profile_id,
                provider_id=provider_id,
                model=model,
                protocol=_model_protocol(
                    model,
                    api_format=api_format,
                    use_responses_api=use_responses_api,
                ),
                capabilities=frozenset(capabilities),
            )
            profiles.append(profile)
            by_name.setdefault(model, profile)
    if not profiles:
        raise ValueError("至少配置一个模型")

    routes: dict[ModelRole, RoleRoute] = {}
    explicit_routes = {
        str(role): [str(item) for item in values]
        for role, values in (role_models or {}).items()
    }
    for role in ModelRole:
        selected_names = explicit_routes.get(role.value)
        if selected_names is None:
            selected = [
                profile
                for profile in profiles
                if ROLE_REQUIRED_CAPABILITIES[role] <= profile.capabilities
            ]
        else:
            selected = []
            for model in selected_names:
                profile = by_name.get(model)
                if profile is None:
                    raise ValueError(f"角色 {role.value} 引用了未配置的模型：{model}")
                if profile not in selected:
                    selected.append(profile)
        if not selected:
            continue
        routes[role] = RoleRoute(
            role=role,
            models=[profile.id for profile in selected],
            allow_cross_provider=True,
            reserved=role == ModelRole.ASSISTANT,
        )
    return AgentModelConfig(
        providers=provider_profiles,
        models=profiles,
        routes=routes,
        reasoning_effort=reasoning_effort,
    )


def save_multi_provider_config(
    database,
    settings: Settings,
    *,
    providers: list[dict[str, Any]],
    reasoning_effort: str,
    capabilities_by_model: dict[str, list[str]] | None = None,
    role_models: dict[str, list[str]] | None = None,
    secret_store=None,
) -> AgentModelConfig:
    store = secret_store or secret_store_for(settings)
    existing = load_agent_model_config(database, settings, secret_store=store)
    existing_providers = list(existing.providers) if existing else []
    existing_refs = {provider.id: provider.secret_ref for provider in existing_providers}
    reuse_single_id = (
        len(providers) == 1
        and len(existing_providers) == 1
        and not str(providers[0].get("id") or "").strip()
    )
    used: set[str] = set()
    prepared: list[dict[str, Any]] = []
    for index, raw in enumerate(providers):
        if not isinstance(raw, dict):
            raise ValueError("模型服务配置格式不正确")
        item = dict(raw)
        api_key = str(item.pop("api_key", "") or "").strip()
        raw_id = str(item.get("id") or "").strip().lower()
        if reuse_single_id and index == 0:
            provider_id = existing_providers[0].id
            used.add(provider_id)
        elif raw_id and re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", raw_id) and raw_id not in used:
            provider_id = raw_id
            used.add(provider_id)
        else:
            provider_id = _slugify_provider(
                str(item.get("name") or raw_id or "provider"), used
            )
        item["id"] = provider_id
        existing_ref = existing_refs.get(provider_id)
        if api_key:
            store.put(provider_secret_ref(provider_id), api_key)
            item["secret_ref"] = provider_secret_ref(provider_id)
        elif existing_ref:
            item["secret_ref"] = existing_ref
        elif index == 0 and settings.openai_api_key and not existing_refs:
            item["secret_ref"] = ENV_SECRET_PREFIX + "OPENAI_API_KEY"
        else:
            item["secret_ref"] = None
        prepared.append(item)
    config = build_multi_provider_config(
        providers=prepared,
        reasoning_effort=reasoning_effort,
        capabilities_by_model=capabilities_by_model,
        role_models=role_models,
    )
    _store_config(database, config)
    return config


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
    api_format: str = "openai",
    use_responses_api: bool = False,
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
        api_format=api_format,
        use_responses_api=use_responses_api,
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
