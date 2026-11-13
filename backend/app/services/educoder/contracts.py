from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit


class ContractError(RuntimeError):
    pass


def identifier(value):
    text = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", text):
        raise ContractError("头歌返回了无效的资源标识，请导出诊断包。")
    return text


def site_url(url):
    parsed = urlsplit(str(url))
    if parsed.scheme != "https" or parsed.hostname != "www.educoder.net" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ContractError("实验入口不属于头歌官方网站。")
    return str(url)


def paged(payload, key):
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise ContractError(f"头歌响应缺少 {key} 列表，未将异常结果当成空列表。")
    # Homework all_count includes records hidden by publication and query filters.
    count = payload.get("query_total_count", payload.get("count", payload.get("all_count")))
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ContractError("头歌分页总数缺失，无法确认已采集全部记录。")
    return payload[key], count


def course_record(raw):
    if not isinstance(raw, dict) or not raw.get("name"):
        raise ContractError("课堂缺少名称。")
    route = identifier(raw.get("identifier") or raw.get("id"))
    entry = raw.get("first_category_url") or f"/classrooms/{route}/announcement"
    if entry.startswith("/"): entry = "https://www.educoder.net" + entry
    site_url(entry)
    match = re.match(r"/classrooms/([A-Za-z0-9_-]+)(?:/|$)", urlsplit(entry).path)
    if not match: raise ContractError("课堂入口格式发生变化。")
    return {"id": identifier(raw.get("id")), "route_id": identifier(match[1]),
            "name": str(raw["name"])[:200], "url": entry,
            "active": raw.get("is_end") is False, "accessible": raw.get("is_accessible") is not False}


def experiment_record(raw, course):
    work_id = identifier(raw.get("homework_id") or raw.get("id"))
    name = str(raw.get("name") or raw.get("homework_name") or "").strip()
    if not name: raise ContractError("实验缺少名称。")
    total, passed = raw.get("checked_challenge_count"), raw.get("finished_challenge_count")
    if not isinstance(total, int) or not isinstance(passed, int) or isinstance(total,bool) or isinstance(passed,bool) or not 0 <= passed <= total:
        raise ContractError("实验缺少可信的关卡完成进度。")
    labels = raw.get("status", [])
    if isinstance(labels, str): labels = [labels]
    if not isinstance(labels, list): raise ContractError("实验开放状态格式未知。")
    states = {str(item) for item in labels}
    available = "open" if "提交中" in states else "late_open" if "补交中" in states else "archived" if "已归档" in states else "closed" if "已截止" in states else "not_open" if "未发布" in states or "未开始" in states else "unknown"
    complete = total > 0 and passed == total
    return {"workId": work_id, "courseId": course["id"], "classId": "", "courseName": course["name"],
            "title": name[:300], "academicYear": 0, "semester": None,
            "status": "finished" if complete else "unfinished",
            "submittable": not complete and total > 0 and available in {"open", "late_open"},
            "deadlineText": str((raw.get("late_time") if available == "late_open" else raw.get("end_time_s") or raw.get("end_time")) or ""),
            "taskUrl": f"https://www.educoder.net/classrooms/{course["route_id"]}/shixun_homework/{work_id}/detail?tabs=1",
            "assessment": {"source_kind": "shixun_homework", "runtime": "educoder", "course_route_id": course["route_id"],
                "execution_state": "code_experiment", "availability": available, "unit_count": total, "filled_units": passed,
                "answer_state": "passed" if complete else "partial" if passed else "empty", "checked_at": datetime.now().astimezone().isoformat()}}
