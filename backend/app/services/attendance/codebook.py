from __future__ import annotations

import re


CODE_PATTERN = re.compile(r'^[A-Za-z0-9+/=_\-]{3,64}$')


def normalize_code(value) -> str:
    code = str(value or '').strip()
    return code if CODE_PATTERN.fullmatch(code) else ''


def find_sign_code(payload):
    """Find a sign code in the activity object without trusting arbitrary response text."""
    if not isinstance(payload, dict):
        return ''
    candidates = payload.get('data')
    if isinstance(candidates, dict):
        for key in ('signCode', 'signcode', 'sign_code'):
            code = normalize_code(candidates.get(key))
            if code:
                return code
    return ''
