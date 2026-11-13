from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import store
from .adapter import discover_courses


class CourseSelection(BaseModel):
    course_ids: list[str] = Field(max_length=100)


def router(database_provider, guard_idle, collection_lock):
    api = APIRouter(tags=["platform-courses"])

    def account(identifier):
        database = database_provider()
        try: record = database.get_account(identifier)
        except KeyError as error: raise HTTPException(404,"账号不存在") from error
        if record["platform"] != "educoder": raise HTTPException(409,"当前平台没有接入课堂选择。")
        if record["status"] != "ready": raise HTTPException(409,"请先完成头歌授权。")
        return database, record

    @api.get("/api/accounts/{identifier}/courses")
    async def list_courses(identifier: str):
        database,_ = account(identifier)
        return store.courses(database,identifier)

    @api.post("/api/accounts/{identifier}/courses/collect")
    async def collect_courses(identifier: str):
        database,_ = account(identifier)
        guard_idle(identifier)
        try:
            async with collection_lock(identifier): return await discover_courses(database,identifier)
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    @api.put("/api/accounts/{identifier}/course-selections")
    async def choose_courses(identifier: str, payload: CourseSelection):
        database,_ = account(identifier)
        try: return store.select_courses(database,identifier,payload.course_ids)
        except RuntimeError as error: raise HTTPException(409,str(error)) from error

    return api
