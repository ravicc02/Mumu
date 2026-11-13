from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

CATALOG = {item["name"]: item for item in json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))}


def platform_spec(name):
    try: return CATALOG[name]
    except KeyError as error: raise ValueError(f"暂不支持平台：{name}") from error


def require_business(name, business):
    labels = {"assignments": "作业", "learning": "网课", "attendance": "签到"}
    if business not in labels: raise ValueError("未知平台业务能力")
    spec = platform_spec(name)
    if spec.get(business) is not True:
        raise ValueError(f"{spec['label']}当前未接入{labels[business]}，请在平台页面选择支持该业务的账号。")
    return spec


def platform_url(name, url):
    spec = platform_spec(name)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("平台地址必须使用 HTTPS，不能包含账号密码或自定义端口")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in spec["hosts"]):
        raise ValueError(f"地址不属于{spec['label']}支持的域名")
    return url


def account_home(account):
    return platform_url(account["platform"], account.get("entry_url") or platform_spec(account["platform"])["home_url"])
