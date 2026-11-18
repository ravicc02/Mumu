from __future__ import annotations

import hashlib
import json
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
    max_model_calls: int = Field(default=4, ge=1, le=60)
    max_evaluations: int = Field(default=4, ge=1, le=4)
    max_desktop_steps: int = Field(default=60, ge=1, le=60)


CODE_EXTENSIONS = frozenset({".java", ".py", ".c", ".cpp", ".cc", ".h", ".hpp", ".js", ".ts", ".cs", ".go", ".rs", ".rb", ".sh", ".sql", ".html", ".css", ".ipynb"})


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
    model_role: Literal[
        "assistant",
        "structured_answer",
        "code",
        "desktop_vision",
        "summarizer",
        "verifier_assist",
    ] = "code"
    protocol: str = "typed_completion"
    preserve_existing_work: bool = True
    reset_environment: bool = False
    blocker: str | None = None

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


def educoder_task_spec(data, *, instance_id, challenge_id, paths, auto_submit, has_editor=True, has_canvas=False):
    shixun = data.get("shixun") or {}
    choices = [item for item in (data.get("chooses") or []) if isinstance(item, dict)]
    desktop = has_canvas and not has_editor or any(shixun.get(key) is True for key in ("vnc", "windows_vnc", "linux_vnc"))
    if desktop:
        route, environment, blocker = "desktop", "remote_desktop_canvas", None if has_canvas else "远程桌面画面尚未就绪，未发送输入。"
    elif choices:
        route, environment, blocker = "structured", "fixed_controls", None
    elif paths and (has_editor or all(PurePosixPath(path).suffix.lower() == ".ipynb" for path in paths)) and all(PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS for path in paths):
        route, environment, blocker = "code", "web_code_editor", None
    else:
        route, environment, blocker = "unsupported", "unknown", "当前环境没有可验证的代码文件或远程桌面输入通道，未写入或评测。"
    return TaskSpec(route=route, environment=environment, instance_id=instance_id, challenge_id=challenge_id,
        editable_files=paths, capabilities=(["observe", "pointer", "keyboard", "evaluate_and_verify"] if route == "desktop" else
            ["read_file", "save_and_readback", "evaluate_and_verify"] if route == "code" else
            ["read_question", "select_choice", "evaluate_and_verify"] if route == "structured" else ["observe"]),
        goals=["在授权区域实现当前关卡要求", "保存并核验实际产物", "按权限核验关卡和课堂结果"],
        model_role="desktop_vision" if route == "desktop" else "structured_answer" if route == "structured" else "code",
        budget=ExecutionBudget(max_model_calls=60 if route == "desktop" else 4),
        permissions=ExecutionPermissions(save=route in {"code", "desktop", "structured"}, evaluate=bool(auto_submit) and route in {"code", "desktop", "structured"}), blocker=blocker)
