from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ExecutionPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    save: bool = True
    self_test: bool = False
    evaluate: bool = False


class ExecutionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_repair_rounds: int = Field(default=3, ge=0, le=3)
    max_model_calls: int = Field(default=4, ge=1, le=4)
    max_evaluations: int = Field(default=4, ge=1, le=4)


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = 1
    route: Literal["structured", "code", "desktop", "unsupported"]
    environment: Literal["fixed_controls", "web_code_editor", "remote_desktop_canvas", "unknown"]
    execution_location: str = "educoder_remote"
    instance_id: str
    challenge_id: str
    editable_files: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    permissions: ExecutionPermissions
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    model_role: str = "coder"
    protocol: str = "typed_completion"
    preserve_existing_work: bool = True
    reset_environment: bool = False
    blocker: str | None = None


def educoder_task_spec(data, *, instance_id, challenge_id, paths, auto_submit, has_editor=True, has_canvas=False):
    shixun = data.get("shixun") or {}
    desktop = has_canvas and not has_editor or any(shixun.get(key) is True for key in ("vnc", "windows_vnc", "linux_vnc"))
    if desktop:
        route, environment, blocker = "desktop", "remote_desktop_canvas", "当前任务是远程桌面实操；首轮尚未开放桌面执行工具，未写入或评测。"
    elif paths and has_editor and all(PurePosixPath(path).suffix.lower() == ".java" for path in paths):
        route, environment, blocker = "code", "web_code_editor", None
    else:
        route, environment, blocker = "unsupported", "unknown", "当前环境不属于已验证的 Java/JUnit 网页代码实操，未写入或评测。"
    return TaskSpec(route=route, environment=environment, instance_id=instance_id, challenge_id=challenge_id,
        editable_files=paths, capabilities=["read_file", "save_and_readback", "evaluate_and_verify"] if route == "code" else ["observe"],
        goals=["在授权区域实现当前关卡要求", "完整保存并回读代码", "按权限核验关卡和课堂结果"],
        permissions=ExecutionPermissions(save=route == "code", evaluate=bool(auto_submit) and route == "code"), blocker=blocker)
