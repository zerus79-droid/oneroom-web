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

import db


# 현재 입주 중: out_dt 없음 또는 레거시 무효 날짜 (여러 화면에서 공통 사용)
CURRENT_TENANT_SQL = "(d.out_dt IS NULL OR d.out_dt < '1000-01-01')"


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


def pad_bunji(v, width=4):
    """번지(주소) 문자열을 DB 저장 형식인 4자리 숫자로 맞춤. 예: '88' -> '0088'"""
    s = (v or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s.zfill(width)
    return s[:width]


def parse_money(raw):
    """콤마 섞인 금액 문자열을 정수로 변환. 빈 값이면 None."""
    s = (raw or "").replace(",", "").strip()
    if s == "":
        return None
    return int(s)


def parse_bunji_input(raw, bunji1="", bunji2=""):
    """'508-88' / '50888' / '05080088' → DB용 4자리 bunji1, bunji2.
    하이픈 없이 연속 숫자면 등록 건물 매칭 또는 4+4 분할.
    """
    s = (raw or "").strip().replace(" ", "")
    if s:
        s = re.sub(r"[^\d\-]", "", s)
        if "-" in s:
            parts = s.split("-", 1)
            b1 = parts[0].strip()
            b2 = parts[1].strip() if len(parts) > 1 else ""
            bunji1, bunji2 = b1, b2
        else:
            digits = re.sub(r"\D", "", s)
            matched = False
            if digits:
                # 등록 건물: 앞0 제거한 주소+주소2 와 일치하는 항목 찾기
                try:
                    rows = db.query("SELECT bunji1, bunji2 FROM bd01")
                except Exception:
                    rows = []
                for r in rows or []:
                    key = f"{fmt_bunji(r.get('bunji1'))}{fmt_bunji(r.get('bunji2'))}"
                    key_pad = f"{pad_bunji(r.get('bunji1'))}{pad_bunji(r.get('bunji2'))}"
                    if digits == key or digits == key_pad or digits.lstrip("0") == key.lstrip("0"):
                        bunji1 = r.get("bunji1") or ""
                        bunji2 = r.get("bunji2") or ""
                        matched = True
                        break
            if not matched:
                if len(digits) == 8:
                    bunji1, bunji2 = digits[:4], digits[4:]
                elif len(digits) > 4:
                    # 뒤 4자리를 주소2, 앞을 주소
                    bunji1, bunji2 = digits[:-4], digits[-4:]
                else:
                    bunji1, bunji2 = digits, ""
    bunji1 = pad_bunji(bunji1)
    bunji2 = pad_bunji(bunji2)
    return bunji1, bunji2


def building_label(bunji1, bunji2):
    """화면에 건물명만 표시 (주소 접두어 없음)."""
    if not bunji1 or not bunji2:
        return ""
    b = db.query_one(
        "SELECT juso, owner_nm FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (bunji1, bunji2),
    )
    if not b:
        return "미등록 주소"
    name = (b.get("juso") or "").strip() or (b.get("owner_nm") or "").strip()
    return name or "이름 없음"


def to_int_amt(v):
    """콤마 섞인 금액 값(문자/숫자/None 등)을 정수로 변환. 실패하면 0."""
    if v is None or v == "":
        return 0
    try:
        return int(Decimal(str(v).replace(",", "").strip() or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def months_elapsed(ipju_dt, as_of=None):
    """입주일 ~ 기준일 경과연월 (같은 달이면 0)."""
    if not ipju_dt:
        return 0
    if as_of is None:
        as_of = date.today()
    if isinstance(ipju_dt, datetime):
        ipju_dt = ipju_dt.date()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    m = (as_of.year - ipju_dt.year) * 12 + (as_of.month - ipju_dt.month)
    return max(0, m)


def fmt_ipju_short(v):
    """입주일 인쇄용 짧은 표시: 15-12-27"""
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%y-%m-%d")
    s = str(v)[:10]
    if len(s) >= 10 and s[4] == "-":
        return s[2:4] + "-" + s[5:7] + "-" + s[8:10]
    return s


def calc_misu_amt(
    bunji1, bunji2, hosu, ipju_seq, rent_amt=None, manage_amt=None, ipju_dt=None, as_of=None
):
    """전월미수총액(누적 추정).
    (월세+관리비) × 입주 후 경과연월 − 수금성격「월세+관리비」합계.
    as_of 가 있으면 그 날짜까지의 수금·경과연월 기준.
    음수(선수금)면 0.
    """
    monthly = to_int_amt(rent_amt) + to_int_amt(manage_amt)
    if monthly <= 0 or not (bunji1 and bunji2 and hosu and ipju_seq):
        return 0
    months = months_elapsed(ipju_dt, as_of)
    expected = monthly * months
    sql = """
        SELECT COALESCE(SUM(COALESCE(su_sil_amt,0) + COALESCE(su_dache_amt,0)), 0) AS paid
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
          AND sukum_char='01'
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
    """
    args = [bunji1, bunji2, (hosu or "").strip().upper(), ipju_seq]
    if as_of is not None:
        if isinstance(as_of, datetime):
            as_of = as_of.date()
        sql += " AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)"
        args.append(as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)[:10])
    paid_row = db.query_one(sql, args)
    paid = to_int_amt((paid_row or {}).get("paid"))
    return max(0, expected - paid)


def calc_month_misu_amt(
    bunji1, bunji2, hosu, ipju_seq, rent_amt=None, manage_amt=None, as_of=None
):
    """이번 달 미입금액(미수총액).
    (월세+관리비) − 이번 달 수금성격「월세+관리비」합.
    이미 다 냈으면 0.
    """
    monthly = to_int_amt(rent_amt) + to_int_amt(manage_amt)
    if monthly <= 0 or not (bunji1 and bunji2 and hosu and ipju_seq):
        return 0
    if as_of is None:
        as_of = date.today()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    month_start = as_of.replace(day=1)
    if as_of.month == 12:
        next_month = date(as_of.year + 1, 1, 1)
    else:
        next_month = date(as_of.year, as_of.month + 1, 1)
    paid_row = db.query_one(
        """
        SELECT COALESCE(SUM(COALESCE(su_sil_amt,0) + COALESCE(su_dache_amt,0)), 0) AS paid
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
          AND sukum_char='01'
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
          AND sukum_dt >= %s AND sukum_dt < %s
        """,
        (
            bunji1,
            bunji2,
            (hosu or "").strip().upper(),
            ipju_seq,
            month_start.isoformat() + " 00:00:00",
            next_month.isoformat(),
        ),
    )
    paid = to_int_amt((paid_row or {}).get("paid"))
    return max(0, monthly - paid)

