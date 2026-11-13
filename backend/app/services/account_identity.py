from __future__ import annotations

from pathlib import Path
import json
import logging
import sqlite3
import tempfile
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from ..db import utc_now


async def read_identity(platform, page):
    if platform == "educoder":
        from .educoder.client import EduCoderClient
        async with EduCoderClient({},page=page) as client: return await client.identity()
    if platform == "chaoxing":
        response = await page.context.request.get("https://i.chaoxing.com/base?ws=1",timeout=20000)
        document = BeautifulSoup(await response.text(),"html.parser")
        name = document.select_one(".user-name")
        image = document.select_one("img.head-img")
        cookies = await page.context.cookies("https://i.chaoxing.com")
        uid = next((item["value"] for item in cookies if item["name"] == "_uid"),"")
        if not response.ok or "passport" in response.url or not name or not name.get_text(strip=True) or not uid:
            raise RuntimeError("尚未读取到学习通账号资料，请完成登录后重试。")
        return {"name":name.get_text(strip=True),"platform_user_id":uid,"avatar_url":urljoin("https://i.chaoxing.com",image["src"]) if image else ""}
    return await page.evaluate("""() => {
        const visible=e=>e?.getClientRects().length;
        const name=[...document.querySelectorAll('.user-info .user-name,.userInfo .userName,.u-navLogin-container .userName,.student-info .name,.user-name')].find(visible);
        const avatar=document.querySelector('.user-info img,.userInfo img,img.avatar,.student-info img');
        return {name:name?.textContent.trim().slice(0,100)||'',avatar_url:avatar?.src||''};
    }""")


async def cache_avatar(account, identity, context):
    url = identity.get("avatar_url") or ""
    if not url: return None
    if not url.startswith("https://") and account["platform"] == "educoder":
        url = "https://ali-cdn.educoder.net/images/"+url.lstrip("/")
    hosts = {"chaoxing":{"photo.chaoxing.com","p.cldisk.com"},
             "educoder":{"www.educoder.net","ali-cdn.educoder.net","www-cdn.educoder.net"}}.get(account["platform"],set())
    for _ in range(4):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in {None,443}:
            return None
        response = await context.request.get(url,headers={"Referer":"https://i.chaoxing.com/" if account["platform"] == "chaoxing" else "https://www.educoder.net/"},timeout=15000,max_redirects=0)
        if response.status in {301,302,303,307,308}:
            url = urljoin(url,response.headers.get("location","")); continue
        if not response.ok: return None
        data = await response.body()
        if len(data)>1024*1024: return None
        mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if data.startswith(b"\xff\xd8\xff") else "image/webp" if data.startswith(b"RIFF") and data[8:12]==b"WEBP" else None
        if not mime: return None
        (Path(account["profile_dir"])/"avatar.bin").write_bytes(data)
        return mime
    return None


async def save_identity(database, account_id, page, *, identity=None):
    account = database.get_account(account_id)
    identity = identity if identity is not None else await read_identity(account["platform"],page)
    name = str(identity.get("name") or "").strip()
    uid = str(identity.get("platform_user_id") or "")
    if account["platform"] in {"educoder","chaoxing"} and (not name or not uid):
        raise RuntimeError("平台身份未读取完整，未将当前会话标记为授权成功。")
    expected_uid = stored_chaoxing_user_id(account) if account["platform"] == "chaoxing" else account.get("platform_user_id")
    if expected_uid and expected_uid != uid:
        raise RuntimeError("登录的用户与该授权记录不一致，请使用原账号登录。")
    if uid:
        duplicate = next((item for item in database.list_accounts() if item["id"] != account_id and item["platform"] == account["platform"] and item.get("platform_user_id") == uid),None)
        if duplicate: raise RuntimeError(f"该平台账号已授权，请使用已有记录：{duplicate['name']}")
    fields = {"identity_verified_at":utc_now()}
    if name: fields["name"] = name[:100]
    if uid: fields["platform_user_id"] = uid
    if identity.get("username"): fields["username"] = identity["username"]
    fields["avatar_url"] = identity.get("avatar_url") or None
    try: fields["avatar_mime"] = await cache_avatar(account,identity,page.context)
    except Exception: fields["avatar_mime"] = None
    try: database.update_account(account_id,**fields)
    except sqlite3.IntegrityError as error: raise RuntimeError("该平台账号已存在，请使用已有授权记录。") from error
    return database.get_account(account_id)


def stored_chaoxing_user_id(account):
    if account.get("platform_user_id"):
        return account["platform_user_id"]
    try:
        state = json.loads(Path(account["storage_state_path"]).read_text(encoding="utf-8"))
        ids = {str(item["value"]) for item in state.get("cookies", []) if item.get("name") == "_uid"
               and item.get("domain", "").lstrip(".") in {"chaoxing.com", "i.chaoxing.com", "passport2.chaoxing.com"} and item.get("value")}
        return next(iter(ids)) if len(ids) == 1 else ""
    except (OSError, ValueError, TypeError, KeyError):
        return ""


async def finish_chaoxing_authorization(database, account_id, page):
    source = database.get_account(account_id)
    identity = await read_identity("chaoxing", page)
    uid = str(identity.get("platform_user_id") or "")
    if not uid or not str(identity.get("name") or "").strip():
        raise RuntimeError("学习通身份未读取完整，请完成登录后重试。")
    bound_uid = stored_chaoxing_user_id(source)
    if bound_uid and bound_uid != uid:
        raise RuntimeError("登录的用户与该授权记录不一致，请使用原账号登录。")
    matches = [item for item in database.list_accounts() if item["id"] != account_id
               and item["platform"] == "chaoxing" and stored_chaoxing_user_id(item) == uid]
    if len(matches) > 1:
        raise RuntimeError("该学习通账号存在多条历史记录，需要核对后合并。")
    target = matches[0] if matches else source
    if target["id"] != account_id:
        if source.get("platform_user_id") or source.get("identity_verified_at") or database.account_has_history(account_id):
            raise RuntimeError("该授权记录已有业务数据，请从已有账号重新授权。")
        from .chaoxing import login_in_progress
        if login_in_progress(target["id"]) or target["status"] == "authorizing":
            raise RuntimeError("该账号正在授权，请先完成已有授权。")
        from .preview import preview_manager
        await preview_manager.invalidate(target["id"])
    state = await page.context.storage_state()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target["profile_dir"], prefix="authorization-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False)
        await save_identity(database, target["id"], page, identity=identity)
        temporary.replace(target["storage_state_path"])
        database.update_account(target["id"], status="ready", last_login_at=utc_now(), last_login_error=None)
        if target["id"] != account_id:
            database.discard_pending_account(account_id)
        return database.get_account(target["id"])
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


async def sync_legacy_chaoxing_identities(database):
    accounts = [item for item in database.list_accounts() if item["platform"] == "chaoxing" and item["status"] == "ready" and not item.get("identity_verified_at")]
    if not accounts:
        return
    from playwright.async_api import async_playwright
    from .browser import chrome_executable
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
        try:
            for account in accounts:
                context = None
                try:
                    context = await browser.new_context(storage_state=account["storage_state_path"])
                    await save_identity(database, account["id"], await context.new_page())
                except Exception as error:
                    logging.getLogger("answer-studio.identity").warning("Account identity sync failed account=%s error=%s", account["id"], type(error).__name__)
                finally:
                    if context:
                        await context.close()
        finally:
            await browser.close()
