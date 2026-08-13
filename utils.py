"""범용 포맷/마스킹 유틸 및 인증 데코레이터.

app.py 전역에서 Jinja 필터·라우트 보호용으로 재사용되는, 특정 도메인에
종속되지 않은 순수 헬퍼 함수들을 모아둔 모듈입니다.
"""
from calendar import monthrange
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from functools import wraps
import re

from flask import redirect, session, url_for


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("sabun"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapper


def money(v):
    """모든 금액 표시 공통: 천 단위 콤마. 예: 250000 -> 250,000"""
    if v is None or v == "":
        return ""
    try:
        if isinstance(v, str):
            s = v.replace(",", "").replace(" ", "").replace("원", "").strip()
            if s == "":
                return ""
            n = int(Decimal(s))
        elif isinstance(v, Decimal):
            n = int(v)
        else:
            n = int(v)
        return f"{n:,}"
    except (TypeError, ValueError, InvalidOperation):
        return str(v)


def fmt_date(v):
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    s = str(v)
    return s[:10] if len(s) >= 10 else s


def fmt_bunji(v):
    """화면 표시용: 앞자리 0 제거 (DB는 4자리 유지). 0000 -> 0"""
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    if s.isdigit():
        return str(int(s))
    # 숫자 앞에만 0이 있는 경우
    t = s.lstrip("0")
    return t if t else "0"


def fmt_bunji_pair(b1, b2=None):
    """주소-주소2 표시. 주소2가 0이면 -0 생략 (예: 1731-0 -> 1731)"""
    # Jinja may call as filter with one arg if misused; support both
    a = fmt_bunji(b1)
    b = fmt_bunji(b2)
    if b == "" or b == "0":
        return a
    if a == "":
        return b
    return f"{a}-{b}"


def mask_phone(v):
    """전화 마스킹: xxx-xxxx-xxxx → 중간 앞2자리·끝 2자리 **
    예: 010-9151-9635 → 010-**51-96**
    """
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    parts = s.split("-")
    if len(parts) == 3:
        a, b, c = parts[0], parts[1], parts[2]
        if len(b) >= 2:
            b = "**" + b[2:]
        if len(c) >= 2:
            c = c[:-2] + "**"
        return f"{a}-{b}-{c}"
    # 하이픈 없이 숫자만
    d = re.sub(r"\D", "", s)
    if len(d) == 11:
        # 3-4-4
        return f"{d[:3]}-**{d[5:7]}-{d[7:9]}**"
    if len(d) == 10 and d.startswith("02"):
        # 02 + 8 digits → 02-xxxx-xxxx
        mid, last = d[2:6], d[6:10]
        return f"02-**{mid[2:]}-{last[:2]}**"
    if len(d) >= 9:
        # 3-3-4 or 3-4-4 guess
        a, mid, last = d[:3], d[3:-4], d[-4:]
        if len(mid) >= 2:
            mid = "**" + mid[2:]
        if len(last) >= 2:
            last = last[:-2] + "**"
        return f"{a}-{mid}-{last}"
    return s


def mask_jumin(v):
    """주민번호 마스킹: 생년월일 6자리 + 성별 1자리 + xxxxxx
    예: 9001011234567 → 900101-1xxxxxx
    """
    if v is None:
        return ""
    d = re.sub(r"\D", "", str(v).strip())
    if not d:
        return ""
    if len(d) >= 7:
        return f"{d[:6]}-{d[6]}xxxxxx"
    if len(d) == 6:
        return f"{d}-xxxxxxx"
    # 짧은 값은 그대로(식별 불가 수준)
    return d


def clamp_date_str(s):
    """YYYY-MM-DD 를 유효한 날짜로 보정. 일 32 등이면 해당 월 말일로."""
    if not s:
        return ""
    s = str(s).strip()[:10]
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return s
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12):
        mo = min(12, max(1, mo))
    last = monthrange(y, mo)[1]
    if d < 1:
        d = 1
    if d > last:
        d = last
    return f"{y:04d}-{mo:02d}-{d:02d}"
