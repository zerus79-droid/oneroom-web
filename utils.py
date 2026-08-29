"""범용 포맷/마스킹 유틸 및 인증 데코레이터.

app.py 전역에서 Jinja 필터·라우트 보호용으로 재사용되는, 특정 도메인에
종속되지 않은 순수 헬퍼 함수들을 모아둔 모듈입니다.
"""
from calendar import monthrange
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
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


def require_grade(*allowed_grades):
    """등급별 접근 제한 데코레이터.
    사용 예: @require_grade('U', 'A') - 무제한, 최고관리자만 접근 가능
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.get("sabun"):
                return redirect(url_for("login"))
            grade = (session.get("grade") or "").strip().upper()
            if grade not in allowed_grades:
                from flask import flash
                flash("접근 권한이 없습니다.", "err")
                return redirect(url_for("home"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_write_access(fn):
    """쓰기 권한 필요 (C 등급 제외). A, B, U만 수정/삭제 가능."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("sabun"):
            return redirect(url_for("login"))
        grade = (session.get("grade") or "").strip().upper()
        if grade == "C":
            from flask import flash
            flash("조회 전용 계정은 수정할 수 없습니다.", "err")
            return redirect(url_for("home"))
        return fn(*args, **kwargs)
    return wrapper


def require_admin(fn):
    """관리자 권한 필요 (U, A만 가능). 사용자 관리 등."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("sabun"):
            return redirect(url_for("login"))
        grade = (session.get("grade") or "").strip().upper()
        if grade not in ("U", "A"):
            from flask import flash
            flash("관리자 권한이 필요합니다.", "err")
            return redirect(url_for("home"))
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
    지역번호: 02-123-4567 → 02-**3-45**, 031-123-4567 → 031-**3-45**
    빈 값이면 '정보없음' 반환
    """
    if v is None:
        return "정보없음"
    s = str(v).strip()
    if not s:
        return "정보없음"
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
        # 3-4-4 (핸드폰 010)
        return f"{d[:3]}-**{d[5:7]}-{d[7:9]}**"
    if len(d) == 10 and d.startswith("02"):
        # 02 + 8 digits → 02-xxxx-xxxx
        mid, last = d[2:6], d[6:10]
        return f"02-**{mid[2:]}-{last[:2]}**"
    if len(d) == 10 and d.startswith("0"):
        # 핸드폰 0xx + 7 digits → 0xx-xxxx-xxx
        prefix, mid, last = d[:3], d[3:6], d[6:10]
        return f"{prefix}-**{mid[1:]}-{last[:2]}**"
    if len(d) == 9:
        # 지역번호 2자리 + 7자리 (예: 02-123-4567)
        mid, last = d[2:5], d[5:9]
        return f"{d[:2]}-**{mid[1:]}-{last[:2]}**"
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
    빈 값이면 '정보없음' 반환
    """
    if v is None:
        return "정보없음"
    d = re.sub(r"\D", "", str(v).strip())
    if not d:
        return "정보없음"
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
    s = re.sub(r"\D", "", (v or "").strip())
    if not s:
        return ""
    return s.zfill(width)


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
    """입주일 ~ 기준일의 납부기준일 경과 횟수.

    입주일이 29~31일인데 대상 월에 그 날짜가 없으면 그 달 말일을 기준일로 본다.
    예: 1/31 입주자는 2/28(윤년 2/29), 4/30에 각각 한 달이 경과한다.
    """
    if not ipju_dt:
        return 0
    if as_of is None:
        as_of = date.today()
    if isinstance(ipju_dt, datetime):
        ipju_dt = ipju_dt.date()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    if as_of < ipju_dt:
        return 0
    m = (as_of.year - ipju_dt.year) * 12 + (as_of.month - ipju_dt.month)
    if m <= 0:
        return 0
    due_day = min(ipju_dt.day, monthrange(as_of.year, as_of.month)[1])
    if as_of.day < due_day:
        m -= 1
    return max(0, m)


def ensure_contract_terms_history():
    """계약금액 변경이력 테이블. 기존 입주 폼 배치를 바꾸지 않고 수정 저장 시 기록한다."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS bd03_terms_hist (
          hist_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
          bunji1 CHAR(4) NOT NULL,
          bunji2 CHAR(4) NOT NULL,
          hosu CHAR(3) NOT NULL,
          ipju_seq CHAR(2) NOT NULL,
          effective_dt DATE NOT NULL,
          bojung_amt DECIMAL(18,0) NOT NULL DEFAULT 0,
          rent_amt DECIMAL(18,0) NOT NULL DEFAULT 0,
          manage_amt DECIMAL(18,0) NOT NULL DEFAULT 0,
          yechi_amt DECIMAL(18,0) NOT NULL DEFAULT 0,
          napbu_gb CHAR(1) NOT NULL DEFAULT 'B',
          change_desc VARCHAR(200) NOT NULL DEFAULT '',
          uid CHAR(5) NOT NULL DEFAULT '',
          sys_dt DATETIME NOT NULL,
          PRIMARY KEY (hist_id),
          KEY ix_terms_tenant_dt (bunji1,bunji2,hosu,ipju_seq,effective_dt,hist_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _terms_desc(old, new):
    labels = (("bojung_amt", "보증금"), ("yechi_amt", "예치금"),
              ("rent_amt", "월세"), ("manage_amt", "관리비"))
    bits = []
    for key, label in labels:
        a, b = to_int_amt(old.get(key)), to_int_amt(new.get(key))
        if a != b:
            bits.append(f"{label} {a:,}→{b:,}")
    a = str(old.get("napbu_gb") or "B").strip().upper()
    b = str(new.get("napbu_gb") or "B").strip().upper()
    if a != b:
        bits.append(f"납부 {'선' if a == 'A' else '후'}→{'선' if b == 'A' else '후'}")
    return ", ".join(bits)


def record_contract_terms_change(bunji1, bunji2, hosu, ipju_seq, ipju_dt, old, new, uid=""):
    """금액/선후불이 실제로 달라졌을 때 기존 조건과 새 조건을 보존한다."""
    desc = _terms_desc(old, new)
    if not desc:
        return False
    ensure_contract_terms_history()
    key = (bunji1, bunji2, (hosu or "").strip().upper(), str(ipju_seq or "").zfill(2))
    cnt = db.query_one(
        "SELECT COUNT(*) AS c FROM bd03_terms_hist WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s",
        key,
    )
    if to_int_amt((cnt or {}).get("c")) == 0:
        start = ipju_dt.date() if isinstance(ipju_dt, datetime) else ipju_dt
        db.execute(
            """INSERT INTO bd03_terms_hist
               (bunji1,bunji2,hosu,ipju_seq,effective_dt,bojung_amt,rent_amt,manage_amt,yechi_amt,napbu_gb,change_desc,uid,sys_dt)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'기존계약',%s,NOW())""",
            key + (start, to_int_amt(old.get("bojung_amt")), to_int_amt(old.get("rent_amt")),
                   to_int_amt(old.get("manage_amt")), to_int_amt(old.get("yechi_amt")),
                   str(old.get("napbu_gb") or "B").strip().upper(), (uid or "")[:5]),
        )
    db.execute(
        """INSERT INTO bd03_terms_hist
           (bunji1,bunji2,hosu,ipju_seq,effective_dt,bojung_amt,rent_amt,manage_amt,yechi_amt,napbu_gb,change_desc,uid,sys_dt)
           VALUES (%s,%s,%s,%s,CURDATE(),%s,%s,%s,%s,%s,%s,%s,NOW())""",
        key + (to_int_amt(new.get("bojung_amt")), to_int_amt(new.get("rent_amt")),
               to_int_amt(new.get("manage_amt")), to_int_amt(new.get("yechi_amt")),
               str(new.get("napbu_gb") or "B").strip().upper(), desc, (uid or "")[:5]),
    )
    return True


def record_initial_contract_terms(bunji1, bunji2, hosu, ipju_seq, ipju_dt, terms, uid=""):
    ensure_contract_terms_history()
    key = (bunji1, bunji2, (hosu or "").strip().upper(), str(ipju_seq or "").zfill(2))
    cnt = db.query_one(
        "SELECT COUNT(*) AS c FROM bd03_terms_hist WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s",
        key,
    )
    if to_int_amt((cnt or {}).get("c")):
        return False
    start = ipju_dt.date() if isinstance(ipju_dt, datetime) else ipju_dt
    db.execute(
        """INSERT INTO bd03_terms_hist
           (bunji1,bunji2,hosu,ipju_seq,effective_dt,bojung_amt,rent_amt,manage_amt,yechi_amt,napbu_gb,change_desc,uid,sys_dt)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'입주계약',%s,NOW())""",
        key + (start, to_int_amt(terms.get("bojung_amt")), to_int_amt(terms.get("rent_amt")),
               to_int_amt(terms.get("manage_amt")), to_int_amt(terms.get("yechi_amt")),
               str(terms.get("napbu_gb") or "B").strip().upper(), (uid or "")[:5]),
    )
    return True


def _add_months_clamped(d, months):
    total = d.year * 12 + d.month - 1 + months
    y, m0 = divmod(total, 12)
    return date(y, m0 + 1, min(d.day, monthrange(y, m0 + 1)[1]))


def calc_contract_period_charge(bunji1, bunji2, hosu, ipju_seq, ipju_dt, end_dt,
                                rent_amt=0, manage_amt=0):
    """입주일 이상 퇴실일 미만의 임대료+관리비를 변경이력별 실제 주기로 계산한다."""
    if isinstance(ipju_dt, datetime): ipju_dt = ipju_dt.date()
    if isinstance(end_dt, datetime): end_dt = end_dt.date()
    if not ipju_dt or not end_dt or end_dt <= ipju_dt:
        return 0
    try:
        ensure_contract_terms_history()
        rows = db.query(
            """SELECT effective_dt,rent_amt,manage_amt FROM bd03_terms_hist
               WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                 AND effective_dt < %s
               ORDER BY effective_dt,hist_id""",
            (bunji1, bunji2, (hosu or "").strip().upper(), str(ipju_seq or "").zfill(2), end_dt),
        )
    except Exception:
        rows = []
    terms = [(ipju_dt, to_int_amt(rent_amt), to_int_amt(manage_amt))]
    for r in rows or []:
        eff = r.get("effective_dt")
        if isinstance(eff, datetime): eff = eff.date()
        if eff and eff <= ipju_dt:
            terms[0] = (ipju_dt, to_int_amt(r.get("rent_amt")), to_int_amt(r.get("manage_amt")))
        elif eff:
            terms.append((eff, to_int_amt(r.get("rent_amt")), to_int_amt(r.get("manage_amt"))))
    terms.sort(key=lambda x: x[0])
    total = Decimal(0)
    idx = 0
    day = ipju_dt
    while day < end_dt:
        while idx + 1 < len(terms) and terms[idx + 1][0] <= day:
            idx += 1
        mdiff = (day.year - ipju_dt.year) * 12 + day.month - ipju_dt.month
        cycle_start = _add_months_clamped(ipju_dt, mdiff)
        if cycle_start > day:
            mdiff -= 1
            cycle_start = _add_months_clamped(ipju_dt, mdiff)
        cycle_end = _add_months_clamped(ipju_dt, mdiff + 1)
        cycle_days = max(1, (cycle_end - cycle_start).days)
        monthly = Decimal(terms[idx][1] + terms[idx][2])
        total += monthly / Decimal(cycle_days)
        day += timedelta(days=1)
    return int((total / Decimal(100)).to_integral_value(rounding=ROUND_CEILING) * 100)


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
    (월세+관리비) × 입주 후 경과연월 − 실입금(su_sil_amt) 합계.
    대체는 집주인 대납이라 세입자 미수에서 빼지 않음.
    as_of 가 있으면 그 날짜까지의 수금·경과연월 기준.
    음수(선수금)면 0.
    """
    monthly = to_int_amt(rent_amt) + to_int_amt(manage_amt)
    if monthly <= 0 or not (bunji1 and bunji2 and hosu and ipju_seq):
        return 0
    months = months_elapsed(ipju_dt, as_of)
    expected = monthly * months
    sql = """
        SELECT COALESCE(SUM(COALESCE(su_sil_amt,0)), 0) AS paid
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
    bunji1, bunji2, hosu, ipju_seq, rent_amt=None, manage_amt=None, as_of=None,
    include_dache=False,
):
    """이번 달 미입금액.
    기본은 실입금만 뺌(대체는 세입자 미수가 아님).
    include_dache=True 이면 대체도 빼서 일괄대체 잔액을 구할 때 씀.
    """
    monthly = to_int_amt(rent_amt) + to_int_amt(manage_amt)
    if monthly <= 0 or not (bunji1 and bunji2 and hosu and ipju_seq):
        return 0
    if as_of is None:
        as_of = date.today()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    elif isinstance(as_of, str):
        as_of = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
    month_start = as_of.replace(day=1)
    if as_of.month == 12:
        next_month = date(as_of.year + 1, 1, 1)
    else:
        next_month = date(as_of.year, as_of.month + 1, 1)
    paid_sql = (
        "COALESCE(SUM(COALESCE(su_sil_amt,0) + COALESCE(su_dache_amt,0)), 0)"
        if include_dache
        else "COALESCE(SUM(COALESCE(su_sil_amt,0)), 0)"
    )
    paid_row = db.query_one(
        f"""
        SELECT {paid_sql} AS paid
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


def tenant_is_past_out(out_dt) -> bool:
    """퇴실일 있으면 True (현세입자 아님)."""
    if out_dt is None:
        return False
    if isinstance(out_dt, datetime):
        return out_dt.year >= 1000
    if isinstance(out_dt, date):
        return out_dt.year >= 1000
    s = str(out_dt)[:10]
    if len(s) >= 4 and s[:4].isdigit():
        return int(s[:4]) >= 1000
    return False


def account_digits(s):
    """계좌번호에서 숫자만 추출 (은행마다 대시 규칙이 달라 숫자만 비교/검증에 사용)."""
    return re.sub(r"\D", "", str(s or ""))


def next_sukum_seq(sukum_dt, bunji1, bunji2, hosu):
    """순번: 같은 수금일 + 건물(주소) + 호실 에서만 증가"""
    max_seq = db.query_one(
        """
        SELECT MAX(CAST(sukum_seq AS UNSIGNED)) AS mx
        FROM sukum01
        WHERE sukum_dt >= %s AND sukum_dt < %s + INTERVAL 1 DAY
          AND bunji1=%s AND bunji2=%s AND hosu=%s
        """,
        (sukum_dt + " 00:00:00", sukum_dt, bunji1, bunji2, hosu),
    )
    next_n = int((max_seq or {}).get("mx") or 0) + 1
    return f"{next_n:04d}"


def parse_bunji_src(src):
    """요청 dict(args/form)에서 bunji1/bunji2 를 뽑음. 'bunji' 통합 필드 우선."""
    bunji_raw = (src.get("bunji") or "").strip()
    if bunji_raw:
        return parse_bunji_input(bunji_raw)
    return (
        pad_bunji((src.get("bunji1") or "").strip()),
        pad_bunji((src.get("bunji2") or "").strip()),
    )


def pad_ipju_seq(seq):
    """입주순번을 2자리로 맞춤 (숫자면 zfill, 아니면 그대로)."""
    seq = str(seq or "").strip()
    if seq.isdigit():
        return seq.zfill(2)
    return seq


def tenant_key(bunji1, bunji2, hosu, ipju_seq):
    """세입자 식별 키(주소·주소2·호수·순번, 대소문자/자리수 정규화)."""
    return (
        bunji1 or "",
        bunji2 or "",
        (hosu or "").strip().upper(),
        pad_ipju_seq(ipju_seq),
    )


def iso_min_date(v):
    """DB에서 나온 날짜값(문자/datetime/date)을 ISO 문자열로. 무효값이면 None."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)[:10]
    return s if len(s) >= 10 else None


def is_common_hosu(hosu):
    """건물 공용 수리. 특정 호실이 아님."""
    h = (hosu or "").replace(" ", "").strip()
    return h in ("공용", "00", "000") or h.upper() == "COM"


def resolve_hosu(bunji1, bunji2, hosu):
    """호수 입력 보정. ㅠ→B, 01→B01(그 건물에 지하호가 있을 때)."""
    raw = (hosu or "").replace("ㅠ", "B").replace(" ", "").strip()
    if is_common_hosu(raw):
        return "공용"
    h = raw.upper()
    b1, b2 = pad_bunji(bunji1), pad_bunji(bunji2)
    if not (b1 and b2 and h):
        return h

    def _find(x):
        return db.query_one(
            """
            SELECT hosu FROM bd03_m
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
            """,
            (b1, b2, x),
        )

    row = _find(h)
    if row:
        return (row.get("hosu") or h).strip().upper()
    if not h.startswith("B"):
        cands = ["B" + h]
        digits = h.lstrip("0") or "0"
        cands.append("B" + digits.zfill(2))
        seen = set()
        for cand in cands:
            if cand in seen:
                continue
            seen.add(cand)
            row = _find(cand)
            if row:
                return (row.get("hosu") or cand).strip().upper()
    return h


def lookup_current_tenant(bunji1, bunji2, hosu):
    """호실의 현재 입주자(거주 우선). 없으면 최신 이력 1건."""
    hosu = resolve_hosu(bunji1, bunji2, hosu)
    if not (bunji1 and bunji2 and hosu):
        return None
    cols = """
        bunji1, bunji2, hosu, ipju_seq, ipju_nm, out_dt,
        rent_amt, manage_amt, bojung_amt, yechi_amt,
        ipju_dt, ipju_tel1, ipju_tel2, misu_tot
    """
    # 거주 중 (out_dt 없음)
    row = db.query_one(
        f"""
        SELECT {cols}
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s
          AND (out_dt IS NULL OR out_dt < '1000-01-01')
        ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (bunji1, bunji2, hosu),
    )
    if row:
        return row
    # 퇴실 포함 최신
    return db.query_one(
        f"""
        SELECT {cols}
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s
        ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (bunji1, bunji2, hosu),
    )


def first_date_for_tenant(b1, b2, h, seq=""):
    """특정 입주자(주소·호·입주순번) 최초 입주일/수금일"""
    h = (h or "").strip().upper()
    b1 = pad_bunji(b1)
    b2 = pad_bunji(b2)
    if not (b1 and b2 and h):
        return None
    seq = (seq or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    if seq:
        row = db.query_one(
            """
            SELECT MIN(dt) AS mn FROM (
              SELECT d.ipju_dt AS dt
              FROM bd03_det d
              WHERE d.bunji1=%s AND d.bunji2=%s
                AND UPPER(TRIM(d.hosu))=%s
                AND LPAD(TRIM(d.ipju_seq),2,'0')=LPAD(TRIM(%s),2,'0')
                AND d.ipju_dt IS NOT NULL AND d.ipju_dt > '1000-01-01'
              UNION ALL
              SELECT s.sukum_dt AS dt
              FROM sukum01 s
              WHERE s.bunji1=%s AND s.bunji2=%s
                AND UPPER(TRIM(s.hosu))=%s
                AND LPAD(TRIM(s.ipju_seq),2,'0')=LPAD(TRIM(%s),2,'0')
                AND s.sukum_dt IS NOT NULL AND s.sukum_dt > '1000-01-01'
            ) t
            """,
            (b1, b2, h, seq, b1, b2, h, seq),
        )
    else:
        row = db.query_one(
            """
            SELECT MIN(dt) AS mn FROM (
              SELECT d.ipju_dt AS dt
              FROM bd03_det d
              WHERE d.bunji1=%s AND d.bunji2=%s
                AND UPPER(TRIM(d.hosu))=%s
                AND d.ipju_dt IS NOT NULL AND d.ipju_dt > '1000-01-01'
              UNION ALL
              SELECT s.sukum_dt AS dt
              FROM sukum01 s
              WHERE s.bunji1=%s AND s.bunji2=%s
                AND UPPER(TRIM(s.hosu))=%s
                AND s.sukum_dt IS NOT NULL AND s.sukum_dt > '1000-01-01'
            ) t
            """,
            (b1, b2, h, b1, b2, h),
        )
    if not row or not row.get("mn"):
        return None
    return iso_min_date(row["mn"])


def buildings_and_rooms():
    """화면 검증용: 등록 건물·호실 목록"""
    buildings = db.query(
        "SELECT bunji1, bunji2, juso FROM bd01 ORDER BY bunji1, bunji2"
    )
    rooms = db.query(
        """
        SELECT bunji1, bunji2, hosu
        FROM bd03_m
        ORDER BY bunji1, bunji2, hosu
        """
    )
    return buildings, rooms


# 목록 화면 공통. 화면마다 다시 짜지 말고 paginate / make_pager + templates/_pager.html
PAGE_SIZE = 20
PAGE_BLOCK_SIZE = 6


def parse_page(value=None):
    """?page= 를 1 이상 정수로. value 없으면 현재 request.args."""
    if value is None:
        try:
            from flask import request

            value = request.args.get("page")
        except RuntimeError:
            value = 1
    try:
        page = int(value or 1)
    except (TypeError, ValueError):
        page = 1
    return max(1, page)


def build_pager(page, total_pages, *, page_block_size=PAGE_BLOCK_SIZE):
    """페이지 번호: N개 단위 블록 (예: 1–6, 7–12). 이전/다음은 블록 점프.

    전체가 두 블록 이하면 번호를 숨기지 않는다. (11페이지가 6에서 끊기던 문제)
    그때 이전/다음은 한 페이지씩 이동.
    """
    page = max(1, int(page or 1))
    total_pages = max(1, int(total_pages or 1))
    page_block_size = max(1, int(page_block_size or PAGE_BLOCK_SIZE))
    page_window = []
    prev_block_page = 1
    next_block_page = 1
    has_prev_block = False
    has_next_block = False
    if total_pages <= page_block_size * 2:
        page_window = list(range(1, total_pages + 1))
        has_prev_block = page > 1
        has_next_block = page < total_pages
        prev_block_page = max(1, page - 1)
        next_block_page = min(total_pages, page + 1)
    elif total_pages > 0:
        block = (page - 1) // page_block_size
        start_p = block * page_block_size + 1
        end_p = min(total_pages, start_p + page_block_size - 1)
        page_window = list(range(start_p, end_p + 1))
        if start_p > 1:
            has_prev_block = True
            prev_block_page = max(1, start_p - page_block_size)
        if end_p < total_pages:
            has_next_block = True
            next_block_page = end_p + 1
    return {
        "page": page,
        "total_pages": total_pages,
        "page_window": page_window,
        "has_prev": has_prev_block,
        "has_next": has_next_block,
        "prev_page": prev_block_page,
        "next_page": next_block_page,
    }


def make_pager(total, page=None, *, per_page=PAGE_SIZE, page_block_size=PAGE_BLOCK_SIZE):
    """건수 기준 페이저. SQL LIMIT/OFFSET 은 pager['offset'], pager['per_page']."""
    total = max(0, int(total or 0))
    per_page = max(1, int(per_page or PAGE_SIZE))
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    page = parse_page(page)
    page = min(page, total_pages)
    pager = build_pager(page, total_pages, page_block_size=page_block_size)
    pager["total"] = total
    pager["per_page"] = per_page
    pager["offset"] = (page - 1) * per_page
    return pager


def paginate(items, page=None, *, per_page=PAGE_SIZE, page_block_size=PAGE_BLOCK_SIZE):
    """이미 가진 목록을 페이지당 건수로 자르고 페이저를 붙인다. (page_items, pager)."""
    items = list(items or [])
    pager = make_pager(
        len(items), page, per_page=per_page, page_block_size=page_block_size
    )
    start = pager["offset"]
    return items[start : start + pager["per_page"]], pager

