from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelRole(str, Enum):
    ASSISTANT = "assistant"
    STRUCTURED_ANSWER = "structured_answer"
    CODE = "code"
    DESKTOP_VISION = "desktop_vision"
    SUMMARIZER = "summarizer"
    VERIFIER_ASSIST = "verifier_assist"


class ModelCapability(str, Enum):
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"
    LONG_CONTEXT = "long_context"


ROLE_REQUIRED_CAPABILITIES: dict[ModelRole, frozenset[ModelCapability]] = {
    ModelRole.ASSISTANT: frozenset({ModelCapability.TOOL_CALLING}),
    ModelRole.STRUCTURED_ANSWER: frozenset({ModelCapability.STRUCTURED_OUTPUT}),
    ModelRole.CODE: frozenset({ModelCapability.STRUCTURED_OUTPUT}),
    ModelRole.DESKTOP_VISION: frozenset({
        ModelCapability.TOOL_CALLING,
        ModelCapability.VISION,
    }),
    ModelRole.SUMMARIZER: frozenset({ModelCapability.STRUCTURED_OUTPUT}),
    ModelRole.VERIFIER_ASSIST: frozenset({ModelCapability.STRUCTURED_OUTPUT}),
}


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderProfile(AgentContract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=2048)
    secret_ref: str | None = Field(default=None, max_length=512)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    api_format: str = Field(default="openai", pattern=r"^(openai|anthropic)$")
    use_responses_api: bool = False
    max_concurrency: int = Field(default=2, ge=1, le=32)
    requests_per_minute: int = Field(default=30, ge=1, le=10000)
    tokens_per_minute: int = Field(default=0, ge=0)
    queue_limit: int = Field(default=50, ge=1, le=10000)
    cooldown_seconds: float = Field(default=30, ge=5, le=3600)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("模型服务地址必须是有效的 HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("模型服务地址不能包含账号或密钥")
        if parsed.query or parsed.fragment:
            raise ValueError("模型服务地址不能包含查询参数或片段")
        return value.rstrip("/")


class ModelProfile(AgentContract):
    id: str = Field(min_length=1, max_length=512)
    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    model: str = Field(min_length=1, max_length=240)
    protocol: str = Field(default="openai_chat", pattern=r"^[a-z0-9_]+$")
    capabilities: frozenset[ModelCapability] = Field(default_factory=frozenset)
    context_window: int = Field(default=32768, ge=1024)


class RoleRoute(AgentContract):
    role: ModelRole
    models: list[str] = Field(min_length=1)
    allow_cross_provider: bool = False
    reserved: bool = False

    @field_validator("models")
    @classmethod
    def unique_models(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            if item not in result:
                result.append(item)
        if not result:
            raise ValueError("角色路由至少需要一个模型")
        return result


class AgentModelConfig(AgentContract):
    version: int = 1
    providers: list[ProviderProfile] = Field(min_length=1)
    models: list[ModelProfile] = Field(min_length=1)
    routes: dict[ModelRole, RoleRoute] = Field(default_factory=dict)
    reasoning_effort: str = Field(default="high", pattern=r"^(none|minimal|low|medium|high|xhigh)$")

    @model_validator(mode="after")
    def validate_references(self):
        provider_ids = [item.id for item in self.providers]
        model_ids = [item.id for item in self.models]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("模型服务 ID 不能重复")
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("模型档案 ID 不能重复")
        providers = {item.id: item for item in self.providers}
        models = {item.id: item for item in self.models}
        for model in self.models:
            if model.provider_id not in providers:
                raise ValueError(f"模型 {model.model} 引用了不存在的服务")
        for role, route in self.routes.items():
            if route.role != role:
                raise ValueError(f"角色路由 {role.value} 与声明角色不一致")
            provider_chain: list[str] = []
            for model_id in route.models:
                profile = models.get(model_id)
                if profile is None:
                    raise ValueError(f"角色路由 {role.value} 引用了不存在的模型")
                missing = ROLE_REQUIRED_CAPABILITIES[role] - set(profile.capabilities)
                if missing:
                    names = "、".join(sorted(item.value for item in missing))
                    raise ValueError(f"模型 {profile.model} 缺少 {role.value} 所需能力：{names}")
                provider = providers[profile.provider_id]
                if not provider.enabled:
                    raise ValueError(f"角色路由 {role.value} 引用了已停用的服务")
                provider_chain.append(profile.provider_id)
            if not route.allow_cross_provider and len(set(provider_chain)) > 1:
                raise ValueError(f"角色路由 {role.value} 未允许跨服务回退")
        return self

    def provider(self, provider_id: str) -> ProviderProfile:
        for provider in self.providers:
            if provider.id == provider_id:
                return provider
        raise KeyError(provider_id)

    def model(self, model_id: str) -> ModelProfile:
        for model in self.models:
            if model.id == model_id:
                return model
        raise KeyError(model_id)

    def route(self, role: ModelRole | str) -> RoleRoute:
        normalized = ModelRole(role)
        route = self.routes.get(normalized)
        if route is None:
            raise KeyError(f"未配置模型角色：{normalized.value}")
        return route
