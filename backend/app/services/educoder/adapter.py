from __future__ import annotations

from ..platforms.base import PlatformAdapter
from . import store
from .client import EduCoderClient
from .contracts import ContractError, course_record, experiment_record, identifier


async def discover_courses(database, account_id):
    account = database.get_account(account_id)
    async with EduCoderClient(account) as client:
        identity = await client.identity()
        if account.get("platform_user_id") and account["platform_user_id"] != identity["platform_user_id"]:
            raise ContractError("当前登录用户与已绑定账号不一致，请重新授权。")
        raw = await client.all_pages(f"/api/users/{identity["username"]}/courses.json", "courses", {"status":"processing","category":"study"})
        records = [course_record(item) for item in raw]
    return store.save_courses(database, account_id, records)


async def verify_assignment(database, assignment_id):
    assignment = database.get_assignment(assignment_id)
    account = database.get_account(assignment["account_id"])
    route = identifier(assignment["assessment"]["course_route_id"])
    async with EduCoderClient(account) as client:
        identity = await client.identity()
        if account.get("platform_user_id") != identity["platform_user_id"]: raise ContractError("授权用户不一致。")
        records = await client.all_pages(f"/api/courses/{route}/homework_commons.json","homeworks",{"type":4})
        raw = next((item for item in records if str(item.get("homework_id") or item.get("id")) == assignment["work_id"]),None)
        if raw is None: raise ContractError("当前账号已无法访问该课堂实验。")
        result = experiment_record(raw,{"id":assignment["course_id"],"route_id":route,"name":assignment["course_name"]})
        database.update_assignment_assessment(assignment_id,result["assessment"])
        return result


async def collect_assignments(database, account_id, settings, run_id=None, progress=None):
    account = database.get_account(account_id)
    selected = [item for item in store.courses(database, account_id) if item["selected"] and item["active"] and item["accessible"]]
    if not selected: raise ContractError("请先获取并选择要采集的头歌课堂。")
    report = {"assignments":[],"total_courses":len(selected),"processed_courses":0,"failed_courses":0,"failed_course_ids":[],"errors":[],"scope_course_ids":[item["id"] for item in selected]}
    async with EduCoderClient(account) as client:
        identity = await client.identity()
        if account.get("platform_user_id") != identity["platform_user_id"]:
            raise ContractError("头歌登录用户与当前授权记录不一致，请重新授权。")
        for index, course in enumerate(selected):
            try:
                raw = await client.all_pages(f"/api/courses/{identifier(course["route_id"])}/homework_commons.json", "homeworks", {"type":4})
                records = [experiment_record(item,course) for item in raw]
                for item in records:
                    local_id = database.upsert_assignment(account_id,"educoder",item,settings,run_id)
                    database.update_assignment_assessment(local_id,item["assessment"])
                    report["assignments"].append({**item,"id":local_id})
            except Exception as error:
                report["failed_courses"] += 1
                report["failed_course_ids"].append(course["id"])
                report["errors"].append({"course_id":course["id"],"course_name":course["name"],"error":str(error)})
            report["processed_courses"] = index+1
            if progress: await progress({"total_courses":len(selected),"processed_courses":index+1,"failed_courses":report["failed_courses"],"current_course":course["name"],"progress":int(95*(index+1)/len(selected))})
    return report


class EducoderAdapter(PlatformAdapter):
    name, label = "educoder", "头歌"

    def start_login(self,database,account_id,settings):
        from ..platform_auth import start_login
        return start_login(database,account_id,settings)

    def login_status(self,account_id):
        from ..platform_auth import login_status
        return login_status(account_id)

    async def refresh_session(self,database,account_id):
        from ..platform_auth import refresh_session
        return await refresh_session(database,account_id)

    async def collect_assignments(self,database,account_id,academic_year,run_id=None,progress=None):
        from ...config import settings
        return await collect_assignments(database,account_id,settings,run_id,progress)
