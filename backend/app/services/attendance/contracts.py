from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field


class AttendanceError(ValueError):
    pass


def upstream_id(value):
    value = str(value or '')
    if not re.fullmatch(r'[0-9]{1,24}', value):
        raise AttendanceError('平台没有返回有效的签到标识')
    return value


KINDS = {'0': 'normal', '1': 'photo', '2': 'qr', '3': 'gesture', '4': 'location', '5': 'code'}


class SignLocation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str = Field(min_length=1, max_length=200)
    source: str = Field(default='manual', pattern=r'^(manual|browser)$')


class SignInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(default='', max_length=64, pattern=r'^(?:[0-9]{0,16}|[A-Za-z0-9+/]{20,64}={0,2})$')
    qr: str = Field(default='', max_length=4096)
    location: SignLocation | None = None


def parse_qr_payload(text):
    try:
        if text.strip().startswith('{'):
            values = json.loads(text)
        else:
            url = urlsplit(text.strip())
            if url.scheme != 'https' or url.hostname != 'mobilelearn.chaoxing.com' or url.username or url.password or url.port not in (None, 443):
                raise ValueError()
            values = {key: items[0] for key, items in parse_qs(url.query).items()}
        identity = values.get('activeId') or values.get('activePrimaryId') or values.get('id')
        enc = values.get('enc')
        if not isinstance(enc, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,512}', enc): raise ValueError()
        return {'active_id': str(identity or ''), 'enc': enc}
    except AttendanceError: raise
    except (ValueError, TypeError, AttributeError) as error:
        raise AttendanceError('未识别到有效的学习通签到二维码内容') from error


def qr_credentials(text, active_id):
    payload = parse_qr_payload(text)
    if str(payload['active_id']) != str(active_id):
        raise AttendanceError('二维码不属于当前签到，请提供对应活动的二维码')
    return {'enc': payload['enc']}


QR_RECAPTURE_MARKERS = (
    '重新扫码',
    '重新扫描',
    '二维码已过期',
    '二维码无效',
    '二维码失效',
    '无效或已过期',
)


def is_expired_qr_message(message):
    text = str(message or '')
    return any(marker in text for marker in QR_RECAPTURE_MARKERS)


def parse_courses(payload):
    if not isinstance(payload, dict) or payload.get('result') != 1 or not isinstance(payload.get('channelList'), list):
        raise AttendanceError('课程列表格式未确认，请刷新学习通授权后重试')
    courses = {}
    for row in payload['channelList']:
        content = row.get('content') or {}
        if not content.get('course') or content.get('state') != 0: continue
        data = content['course'].get('data')
        if not isinstance(data, list) or not data: raise AttendanceError('课程信息不完整，未将其当作空列表')
        course = {'course_id': upstream_id(data[0].get('id')), 'class_id': upstream_id(content.get('id')),
                  'course_name': str(data[0].get('name') or '未命名课程')[:200]}
        courses[(course['course_id'], course['class_id'])] = course
    return list(courses.values())


def parse_activities(payload, course):
    if not isinstance(payload, dict) or payload.get('result') != 1 or not isinstance(payload.get('data'), dict) or not isinstance(payload['data'].get('activeList'), list):
        raise AttendanceError('活动列表格式未确认，未清除原有记录')
    records = []
    for row in payload['data']['activeList']:
        if str(row.get('activeType')) not in {'2', '54', '74'} or str(row.get('status')) != '1': continue
        records.append({**{key:course[key] for key in ('course_id','class_id','course_name')}, 'upstream_id': upstream_id(row.get('id')), 'title': str(row.get('nameOne') or '签到')[:200],
                        'kind': KINDS.get(str(row.get('otherId')), 'unknown'), 'start_time':row.get('startTime'), 'end_time':row.get('endTime')})
    return records


def classify_receipt(text):
    clean = text.strip()
    if clean.lower() in {'success', 'ok', 'true'} or clean in {'签到成功', '签到成功！', '签到成功!'}: return 'succeeded', '平台已确认签到成功'
    if clean.lower() == 'success2' or '下次早点' in clean or 'next time earlier' in clean.lower():
        return 'succeeded', '平台已确认签到成功（迟到）'
    if clean in {'您已签到', '您已签到过了', '你已经签到过了', '已签到'}: return 'already_signed', '平台显示已签到，无须重复提交'
    if '90002' in clean:
        return 'awaiting_input', '签到码或手势码未通过平台校验，请核对后重试'
    if is_expired_qr_message(clean):
        return 'awaiting_input', '二维码无效或已过期，请重新扫码'
    match = re.search(r'errorLocation[_\-]?(\d+(?:\.\d+)?)?', clean, re.IGNORECASE)
    if match or '不在签到范围' in clean:
        distance = round(float(match.group(1))) if match and match.group(1) else 0
        return 'failed', f'不在签到范围内（约 {distance} 米）' if distance else '不在签到范围内'
    if 'validate' in clean.lower() or '验证码' in clean or '人脸' in clean: return 'manual_required', '平台要求验证，请在学习通完成后重新查询'
    if '过期' in clean or '已结束' in clean: return 'expired', '签到活动已结束或已过期'
    if '非法请求' in clean: return 'expired', '签到活动已结束或请求无效'
    if '登录' in clean: return 'auth_required', '登录态失效，请重新授权'
    if '失败' in clean or '错误' in clean or clean.lower() in {'fail', 'failed'}: return 'failed', '平台未接受签到，请核对输入后重试'
    return 'unknown', '未收到明确回执，请先核对签到结果，勿重复提交'
