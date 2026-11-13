from __future__ import annotations

import json
from ...db import utc_now
from .contracts import ContractError


def initialize(database):
    with database.connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS educoder_instance_leases (
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            instance_id TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            PRIMARY KEY(account_id,instance_id))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS platform_courses (
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            upstream_id TEXT NOT NULL, route_id TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, accessible INTEGER NOT NULL DEFAULT 1, selected INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL, PRIMARY KEY(account_id,upstream_id))""")


def courses(database, account_id):
    initialize(database)
    with database.connect() as connection:
        return [{**dict(row), "id": row["upstream_id"], "selected": bool(row["selected"])} for row in connection.execute(
            "SELECT * FROM platform_courses WHERE account_id=? ORDER BY name,upstream_id", (account_id,))]


def save_courses(database, account_id, records):
    initialize(database)
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE platform_courses SET active=0 WHERE account_id=?", (account_id,))
        for record in records:
            connection.execute("""INSERT INTO platform_courses(account_id,upstream_id,route_id,name,url,active,accessible,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account_id,upstream_id) DO UPDATE SET
                route_id=excluded.route_id,name=excluded.name,url=excluded.url,active=excluded.active,
                accessible=excluded.accessible,updated_at=excluded.updated_at""",
                (account_id,record["id"],record["route_id"],record["name"],record["url"],int(record["active"]),int(record["accessible"]),utc_now()))
    return courses(database, account_id)


def select_courses(database, account_id, identifiers):
    if len(identifiers) != len(set(identifiers)): raise ContractError("课程选择不能重复。")
    initialize(database)
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        available = {row["upstream_id"] for row in connection.execute(
            "SELECT upstream_id FROM platform_courses WHERE account_id=? AND active=1 AND accessible=1", (account_id,))}
        if not set(identifiers) <= available: raise ContractError("所选课程不属于当前账号可访问的进行中课堂，请重新获取课程。")
        connection.execute("UPDATE platform_courses SET selected=0 WHERE account_id=?", (account_id,))
        for identifier in identifiers:
            connection.execute("UPDATE platform_courses SET selected=1 WHERE account_id=? AND upstream_id=?", (account_id,identifier))
    return courses(database, account_id)


def claim_instance(database, account_id, instance_id, job_id):
    initialize(database)
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        owner = connection.execute("""SELECT j.id,j.status,j.browser_retained FROM educoder_instance_leases l
            JOIN jobs j ON j.id=l.job_id WHERE l.account_id=? AND l.instance_id=?""",(account_id,instance_id)).fetchone()
        if owner and owner["id"] != job_id and (owner["status"] in {"queued","running","paused","stopping"} or owner["browser_retained"]):
            raise ContractError("同账号的另一项任务正在使用这个头歌实验实例，请先结束该任务的浏览器会话。")
        connection.execute("INSERT INTO educoder_instance_leases VALUES (?,?,?) ON CONFLICT(account_id,instance_id) DO UPDATE SET job_id=excluded.job_id",(account_id,instance_id,job_id))


def instance_for_job(database, account_id, job_id):
    initialize(database)
    with database.connect() as connection:
        rows = connection.execute('SELECT instance_id FROM educoder_instance_leases WHERE account_id=? AND job_id=?', (account_id, job_id)).fetchall()
    if len(rows) != 1:
        raise ContractError('原任务的实验实例无法唯一确认，未重新打开或覆盖当前页面。')
    return rows[0]['instance_id']
