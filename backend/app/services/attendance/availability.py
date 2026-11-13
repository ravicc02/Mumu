from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
import time

PLATFORM_TIMEZONE = timezone(timedelta(hours=8))
START_FIELDS = ('startTime', 'starttime', 'start_time', 'starttimeStr')
END_FIELDS = ('endTime', 'endtime', 'end_time', 'endtimeStr')


def timestamp(value):
    if isinstance(value, bool): raise ValueError('Invalid attendance time')
    if value is None or value in ('', 0, -1, '0', '-1'): return None
    if isinstance(value, dict):
        if 'time' not in value: raise ValueError('Unknown attendance date object')
        return timestamp(value['time'])
    if isinstance(value, (int, float)) or isinstance(value, str) and re.fullmatch(r'[0-9]+(?:[.][0-9]+)?', value.strip()):
        number = float(value)
        if number >= 100_000_000_000: number /= 1000
        if not math.isfinite(number) or not 946684800 <= number <= 7258118400: raise ValueError('Out of range attendance time')
        return number
    if not isinstance(value, str) or ':' not in value: raise ValueError('Unknown attendance time')
    date = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    if date.tzinfo is None: date = date.replace(tzinfo=PLATFORM_TIMEZONE)
    return date.timestamp()


def window(*sources, now=None):
    now = time.time() if now is None else now
    starts, ends = [], []
    ended = False
    unlimited = False
    try:
        for source in sources:
            if not isinstance(source, dict): raise ValueError('Unknown attendance metadata')
            if any(source.get(key) in (1, '1', True) for key in ('isOver', 'isEnd', 'isExpired', 'ifEnd', 'isdelete', 'isDelete')): ended = True
            if 'status' in source and str(source['status']) not in {'1', 'None'}: ended = True
            explicit_unlimited = source.get('setEndTimeNull') in (1, '1', True) and source.get('isOver') in (0, '0', False)
            verified_unlimited = source.get('open_ended') in (1, True) and source.get('availability_status') == 'open' and bool(source.get('verified_at'))
            unlimited = unlimited or explicit_unlimited or verified_unlimited
            for key in START_FIELDS:
                parsed = timestamp(source.get(key))
                if parsed is not None: starts.append(parsed)
            for key in END_FIELDS:
                parsed = timestamp(source.get(key))
                if parsed is not None: ends.append(parsed)
    except (ValueError, TypeError, OverflowError):
        return {'status':'unknown','start_time':None,'end_time':None,'open_ended':False}
    start, end = max(starts, default=None), min(ends, default=None)
    if ended: status = 'ended'
    elif start is None or end is None and not unlimited: status = 'unknown'
    elif end is not None and end <= start: status = 'unknown'
    elif now < start: status = 'not_started'
    elif end is not None and now >= end: status = 'ended'
    else: status = 'open'
    return {'status':status,'start_time':start,'end_time':end,'open_ended':bool(unlimited and end is None)}
