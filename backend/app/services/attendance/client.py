from __future__ import annotations

from pathlib import Path
import logging
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from ...db import utc_now

from .contracts import AttendanceError, KINDS, classify_receipt, parse_courses, parse_activities, qr_credentials
from .availability import window

MOBILE = 'https://mobilelearn.chaoxing.com'
logger = logging.getLogger('answer-studio.attendance')


class AttendanceClient:
    def __init__(self, account):
        self.account = account
        self.driver = self.context = None

    async def __aenter__(self):
        if self.account['platform'] != 'chaoxing' or self.account['status'] != 'ready':
            raise AttendanceError('请先授权支持签到的学习通账号')
        if not Path(self.account['storage_state_path']).is_file(): raise AttendanceError('未找到已保存的登录态，请重新授权')
        self.driver = await async_playwright().start()
        try:
            self.context = await self.driver.request.new_context(storage_state=self.account['storage_state_path'],
                user_agent='Mozilla/5.0', timeout=20000, max_redirects=0)
            response = await self.context.get('https://i.chaoxing.com/base?ws=1')
            state = await self.context.storage_state()
            ids = {cookie['value'] for cookie in state.get('cookies', []) if cookie['name'] == '_uid' and cookie.get('domain','').lstrip('.').endswith('chaoxing.com')}
            expected = str(self.account.get('platform_user_id') or '')
            name = BeautifulSoup(await response.text(), 'html.parser').select_one('.user-name')
            if not response.ok or not expected or ids != {expected} or not name:
                raise AttendanceError('学习通登录态或账号身份未通过验证，请重新授权')
            return self
        except BaseException:
            await self.__aexit__(None, None, None); raise

    async def __aexit__(self, *args):
        try:
            if self.context: await self.context.dispose()
        finally:
            if self.driver: await self.driver.stop()

    async def json(self, path, params=None, origin=MOBILE):
        response = await self.context.get(origin + path, params=params)
        if response.status in {301,302,401,403}: raise AttendanceError('签到接口要求重新登录，请刷新授权')
        if not response.ok: raise AttendanceError(f'签到接口返回 HTTP {response.status}')
        try: return await response.json()
        except Exception as error: raise AttendanceError('签到接口返回格式无法确认') from error

    async def courses(self):
        return parse_courses(await self.json('/mycourse/backclazzdata', {'view':'json','getTchClazzType':'1','mcode':''}, 'https://mooc1-api.chaoxing.com'))

    async def activities(self, course):
        candidates = parse_activities(await self.json('/v2/apis/active/student/activelist',
            {'fid':'0','courseId':course['course_id'],'classId':course['class_id'],'showNotStartedActive':'0'}), course)
        result = []
        for candidate in candidates:
            detail = await self.json('/newsign/signDetail',{'activePrimaryId':candidate['upstream_id'],'type':'1'})
            detail = detail.get('data',detail) if isinstance(detail,dict) else None
            if not isinstance(detail,dict): raise AttendanceError('未能核验签到详情，未更新该课程活动')
            availability = window(candidate,detail)
            if availability['status'] != 'open':
                logger.info('Attendance activity excluded by window',extra={'diagnostic_code':'attendance.filtered','diagnostic_data':{
                    'account_id':self.account.get('id'),'upstream_id':candidate['upstream_id'],'reason':availability['status'],
                    'start_time':availability['start_time'],'end_time':availability['end_time'],
                    'is_over':detail.get('isOver'),'explicit_no_deadline':detail.get('setEndTimeNull')}})
            if availability['status']=='unknown': raise AttendanceError('签到有效时段未能核实，未更新该课程活动')
            if availability['status'] != 'open': continue
            attendance = await self.json('/v2/apis/sign/getAttendInfo',{'activeId':candidate['upstream_id'],'moreClassAttendEnc':''})
            if attendance.get('result') != 1 or not isinstance(attendance.get('data'),dict):
                raise AttendanceError('未能核验个人出勤，未更新该课程活动')
            if type(attendance['data'].get('status')) is not int or attendance['data']['status'] != 0:
                logger.info('Attendance activity excluded by personal state',extra={'diagnostic_code':'attendance.filtered','diagnostic_data':{
                    'account_id':self.account.get('id'),'upstream_id':candidate['upstream_id'],'reason':'personal_state','personal_status':attendance['data'].get('status')}})
                continue
            result.append({**candidate, **{key:availability[key] for key in ('start_time','end_time','open_ended')},
                'availability_status':'open','verified_at':utc_now(),'kind':KINDS.get(str(detail.get('otherId')),candidate['kind'])})
        return result

    async def prepare(self, activity, inputs):
        current = await self.activities(activity)
        matched = next((item for item in current if item['upstream_id']==activity['upstream_id']),None)
        if matched is None:
            receipt = await self.json('/v2/apis/sign/getAttendInfo', {'activeId':activity['upstream_id'],'moreClassAttendEnc':''})
            if receipt.get('result') != 1 or not isinstance(receipt.get('data'),dict):
                raise AttendanceError('活动已结束，但个人出勤记录尚未核实，请在平台核对')
            if type(receipt['data'].get('status')) is int and receipt['data']['status']==1:
                return {'status':'already_signed','message':'活动已结束，平台确认该账号已签到'}
            return {'status':'expired','message':'该活动已不在当前账号的进行中签到列表'}
        info = await self.json('/v2/apis/active/getPPTActiveInfo', {'activeId':activity['upstream_id']})
        attendance = await self.json('/v2/apis/sign/getAttendInfo', {'activeId':activity['upstream_id'],'moreClassAttendEnc':''})
        if info.get('result') != 1 or not isinstance(info.get('data'),dict) or attendance.get('result') != 1 or not isinstance(attendance.get('data'),dict):
            raise AttendanceError('活动详情或个人签到状态未通过核验，未提交')
        status = attendance['data'].get('status')
        if type(status) is int and status == 1: return {'status':'already_signed','message':'平台显示该账号已签到'}
        if status == 11: return {'status':'expired','message':'平台显示签到已过期'}
        if type(status) is not int or status != 0: return {'status':'manual_required','message':'该账号出勤状态需人工核对，未覆盖原记录'}
        detail = info['data']
        availability = window(matched,detail)
        if availability['status'] != 'open':
            return {'status':'expired' if availability['status']=='ended' else 'failed','message':'当前不在已核实的有效签到时段，未提交'}
        kind = KINDS.get(str(detail.get('otherId')), 'unknown')
        gated = any(detail.get(key) in (1, '1', True) for key in ('ifphoto','ifPhoto','openCheckFaceFlag','showVCode','ifNeedVCode','openPreventCheatFlag','openLocationFlag'))
        located = bool(detail.get('locationText')) and str(detail.get('locationRange') or 0) not in {'0','0.0','-1'}
        address_required = any(detail.get(key) in (1, '1', True) for key in ('ifopenAddress','ifOpenAddress'))
        if gated or kind == 'unknown' or (kind != 'location' and (located or address_required)):
            return {'status':'manual_required','message':'该签到需要位置、照片、人脸或验证，请在学习通完成'}
        params = {'activeId':activity['upstream_id'],'courseId':activity['course_id'],'uid':str(self.account['platform_user_id']),
                  'name':self.account['name'],'fid':'0','appType':'15','clientip':'','latitude':'-1','longitude':'-1'}
        if kind == 'location':
            if inputs.location is None:
                return {'status':'awaiting_input','message':'请提供并确认本次签到的实际位置，再继续执行'}
            params.update(latitude=str(inputs.location.latitude), longitude=str(inputs.location.longitude),
                          address=inputs.location.address, ifTiJiao='1')
        if kind in {'code','gesture'}:
            if not inputs.code: return {'status':'awaiting_input','message':'请提供签到码或手势码'}
            checked = await self.json('/widget/sign/pcStuSignController/checkSignCode', {'activeId':activity['upstream_id'],'signCode':inputs.code})
            if checked.get('result') != 1: return {'status':'awaiting_input','message':'签到码未通过校验，请核对后再提交'}
            params['signCode'] = inputs.code
        if kind == 'qr':
            if not inputs.qr: return {'status':'awaiting_input','message':'请提供当前有效的签到二维码内容'}
            try: params.update(qr_credentials(inputs.qr, activity['upstream_id']))
            except AttendanceError as error: return {'status':'awaiting_input','message':str(error)}
        return {'status':'ready','params':params,'kind':kind,'availability':{**{key:availability[key] for key in ('start_time','end_time','open_ended')},'availability_status':'open','verified_at':utc_now()}}

    async def submit(self, prepared):
        available = window(prepared.get('availability'))
        if available['status'] != 'open':
            return {'status':'expired' if available['status']=='ended' else 'failed','message':'签到时段已结束或尚未核实，未发出请求'}
        response = await self.context.post(MOBILE + '/pptSign/stuSignajax', form=prepared['params'], max_retries=0)
        if not response.ok: return {'status':'unknown','message':f'提交后收到 HTTP {response.status}，请先核对平台记录'}
        text = await response.text()
        status, message = classify_receipt(text)
        return {'status':status,'message':message,'receipt_kind':'platform_response'}
