from __future__ import annotations

import hashlib
import secrets
import time
import uuid

from ...db import utc_now
from .contracts import AttendanceError
from .availability import window

SUCCESS = {'succeeded', 'already_signed'}
ACTIVE = {'queued', 'running', 'submitting'}


class AttendanceStore:
    def __init__(self, database): self.database = database

    def initialize(self):
        with self.database.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS attendance_activities(
                  id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                  upstream_id TEXT NOT NULL, course_id TEXT NOT NULL, class_id TEXT NOT NULL, course_name TEXT NOT NULL,
                  title TEXT NOT NULL, kind TEXT NOT NULL, start_time TEXT, active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
                  UNIQUE(account_id,upstream_id,course_id,class_id));
                CREATE TABLE IF NOT EXISTS attendance_batches(id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attendance_items(
                  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES attendance_batches(id) ON DELETE CASCADE,
                  activity_id TEXT NOT NULL REFERENCES attendance_activities(id) ON DELETE CASCADE,
                  status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', attempt INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                  UNIQUE(batch_id,activity_id));
                CREATE TABLE IF NOT EXISTS attendance_shares(
                  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES attendance_batches(id) ON DELETE CASCADE,
                  token_hash TEXT UNIQUE NOT NULL, expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS attendance_item_activity_status ON attendance_items(activity_id,status);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(attendance_activities)')}
            additions = {'end_time':'REAL','availability_status':"TEXT NOT NULL DEFAULT 'unverified'",'verified_at':'TEXT','open_ended':'INTEGER NOT NULL DEFAULT 0'}
            migrated = False
            for name, definition in additions.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE attendance_activities ADD COLUMN {name} {definition}')
                    migrated = True
            if migrated:
                db.execute("UPDATE attendance_activities SET active=0 WHERE availability_status='unverified'")

    def recover(self):
        with self.database.connect() as db:
            db.execute("UPDATE attendance_items SET status='unknown',message='应用重启，提交回执未确认，请先核对平台记录' WHERE status='submitting'")
            db.execute("UPDATE attendance_items SET status='cancelled',message='应用重启，尚未发出的操作已取消' WHERE status IN ('queued','running')")

    def save_activities(self, account_id, rows, successful_courses, all_courses=None, failed_courses=None):
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for course_id, class_id in failed_courses or []:
                db.execute("UPDATE attendance_activities SET availability_status='unverified' WHERE account_id=? AND course_id=? AND class_id=?",(account_id,course_id,class_id))
            if all_courses is not None:
                known = set(all_courses)
                old = db.execute('SELECT DISTINCT course_id,class_id FROM attendance_activities WHERE account_id=?',(account_id,)).fetchall()
                for course in old:
                    if (course['course_id'],course['class_id']) not in known:
                        db.execute('UPDATE attendance_activities SET active=0 WHERE account_id=? AND course_id=? AND class_id=?',(account_id,course['course_id'],course['class_id']))
            for course_id, class_id in successful_courses:
                db.execute('UPDATE attendance_activities SET active=0 WHERE account_id=? AND course_id=? AND class_id=?', (account_id,course_id,class_id))
            for row in rows:
                availability = window(row)
                if row.get('availability_status') != 'open' or not row.get('verified_at') or availability['status'] != 'open': continue
                db.execute('''INSERT INTO attendance_activities(id,account_id,upstream_id,course_id,class_id,course_name,title,kind,start_time,active,updated_at,end_time,availability_status,verified_at,open_ended)
                    VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)
                    ON CONFLICT(account_id,upstream_id,course_id,class_id) DO UPDATE SET title=excluded.title,
                    course_name=excluded.course_name,kind=excluded.kind,start_time=excluded.start_time,active=1,updated_at=excluded.updated_at,
                    end_time=excluded.end_time,availability_status=excluded.availability_status,verified_at=excluded.verified_at,open_ended=excluded.open_ended''',
                    (uuid.uuid4().hex,account_id,row['upstream_id'],row['course_id'],row['class_id'],row['course_name'],row['title'],row['kind'],availability['start_time'],utc_now(),availability['end_time'],'open',row['verified_at'],int(availability['open_ended'])))
        return self.activities([account_id])

    def activities(self, account_ids=None):
        query = "SELECT t.*,a.name AS account_name,a.platform FROM attendance_activities t JOIN accounts a ON a.id=t.account_id WHERE t.active=1 AND t.availability_status='open' AND t.verified_at IS NOT NULL AND CAST(t.start_time AS REAL)<=? AND (t.end_time>? OR (t.end_time IS NULL AND t.open_ended=1)) AND NOT EXISTS (SELECT 1 FROM attendance_items i WHERE i.activity_id=t.id AND i.status IN ('succeeded','already_signed','unknown','queued','running','submitting'))"
        now = time.time(); args = [now, now]
        if account_ids:
            query += ' AND t.account_id IN (' + ','.join('?' for _ in account_ids) + ')'; args.extend(account_ids)
        with self.database.connect() as db:
            return [dict(row) for row in db.execute(query + ' ORDER BY t.course_name,a.name,t.title', args)]

    def create_batch(self, activity_ids):
        if not activity_ids or len(activity_ids) != len(set(activity_ids)): raise AttendanceError('请选择签到活动，且不能重复选择')
        identifier = uuid.uuid4().hex
        with self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for activity_id in activity_ids:
                row = db.execute('''SELECT t.id FROM attendance_activities t JOIN accounts a ON a.id=t.account_id
                  WHERE t.id=? AND t.active=1 AND a.platform='chaoxing' AND a.status='ready' AND t.availability_status='open' AND t.verified_at IS NOT NULL
                  AND CAST(t.start_time AS REAL)<=? AND (t.end_time>? OR (t.end_time IS NULL AND t.open_ended=1))
                  AND NOT EXISTS (SELECT 1 FROM attendance_items i WHERE i.activity_id=t.id AND i.status IN ('succeeded','already_signed','unknown','queued','running','submitting'))''', (activity_id,time.time(),time.time())).fetchone()
                if not row: raise AttendanceError('所选活动已失效或账号尚未授权，请重新采集')
            db.execute('INSERT INTO attendance_batches VALUES(?,?)', (identifier,utc_now()))
            for activity_id in activity_ids:
                db.execute('INSERT INTO attendance_items VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex,identifier,activity_id,'pending','待执行',0,utc_now()))
        return self.batch(identifier)

    def batch(self, identifier):
        with self.database.connect() as db:
            batch = db.execute('SELECT * FROM attendance_batches WHERE id=?',(identifier,)).fetchone()
            if not batch: raise KeyError('签到批次不存在')
            items = [dict(row) for row in db.execute('''SELECT i.*,t.account_id,t.upstream_id,t.course_id,t.class_id,t.course_name,t.title,t.kind,
                t.start_time,t.end_time,t.availability_status,t.verified_at,t.open_ended,a.name AS account_name FROM attendance_items i JOIN attendance_activities t ON t.id=i.activity_id
                JOIN accounts a ON a.id=t.account_id WHERE i.batch_id=? ORDER BY t.course_name,a.name,i.id''',(identifier,))]
            shares = [dict(row) for row in db.execute('SELECT id,expires_at,revoked FROM attendance_shares WHERE batch_id=? ORDER BY expires_at DESC',(identifier,))]
        states = [item['status'] for item in items]
        state = 'running' if any(value in ACTIVE for value in states) else 'completed' if states and all(value in SUCCESS for value in states) else 'needs_review' if 'unknown' in states else 'awaiting_input' if any(value in {'pending','awaiting_input'} for value in states) else 'finished'
        return {**dict(batch),'items':items,'shares':shares,'status':state,'completed':sum(value in SUCCESS for value in states),'total':len(items)}

    def batches(self):
        with self.database.connect() as db: ids = [row['id'] for row in db.execute('SELECT id FROM attendance_batches ORDER BY created_at DESC LIMIT 100')]
        return [self.batch(identifier) for identifier in ids]

    def update_item(self, identifier, status, message):
        with self.database.connect() as db:
            db.execute('UPDATE attendance_items SET status=?,message=?,updated_at=?,attempt=attempt+? WHERE id=?',
                (status,message[:240],utc_now(),int(status=='running'),identifier))

    def item_status(self, identifier):
        with self.database.connect() as db: return db.execute('SELECT status FROM attendance_items WHERE id=?',(identifier,)).fetchone()['status']

    def account_active(self, account_id):
        with self.database.connect() as db:
            return db.execute("SELECT 1 FROM attendance_items i JOIN attendance_activities a ON a.id=i.activity_id WHERE a.account_id=? AND i.status IN ('queued','running','submitting') LIMIT 1",(account_id,)).fetchone() is not None

    def prior_outcome(self, activity_id, item_id):
        with self.database.connect() as db:
            row = db.execute("SELECT status FROM attendance_items WHERE activity_id=? AND id!=? AND status IN ('succeeded','already_signed','unknown','submitting') ORDER BY CASE WHEN status IN ('unknown','submitting') THEN 0 ELSE 1 END,updated_at DESC LIMIT 1",(activity_id,item_id)).fetchone()
        return row['status'] if row else None

    def share(self, batch_id, minutes):
        self.batch(batch_id)
        token = secrets.token_urlsafe(32); identifier = uuid.uuid4().hex
        expires = time.time() + minutes * 60
        with self.database.connect() as db:
            db.execute('INSERT INTO attendance_shares VALUES(?,?,?,?,0)',(identifier,batch_id,hashlib.sha256(token.encode()).hexdigest(),expires))
        return {'id':identifier,'token':token,'expires_at':expires}

    def resolve_share(self, token):
        if not token or len(token) > 128: raise AttendanceError('分享链接无效或已失效')
        with self.database.connect() as db:
            row = db.execute('SELECT * FROM attendance_shares WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row['revoked'] or row['expires_at'] <= time.time(): raise AttendanceError('分享链接无效、已过期或已撤销')
        return dict(row)

    def revoke(self, identifier):
        with self.database.connect() as db: db.execute('UPDATE attendance_shares SET revoked=1 WHERE id=?',(identifier,))

    def revoke_batch(self, identifier):
        with self.database.connect() as db: db.execute('UPDATE attendance_shares SET revoked=1 WHERE batch_id=?',(identifier,))

    def has_live_shares(self):
        with self.database.connect() as db: return db.execute('SELECT 1 FROM attendance_shares WHERE revoked=0 AND expires_at>? LIMIT 1',(time.time(),)).fetchone() is not None
