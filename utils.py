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


def lookup_current_tenant(bunji1, bunji2, hosu):
    """호실의 현재 입주자(거주 우선). 없으면 최신 이력 1건."""
    hosu = (hosu or "").strip().upper()
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
    """페이지 번호: N개 단위 블록 (예: 1–6, 7–12). 이전/다음은 블록 점프."""
    page = max(1, int(page or 1))
    total_pages = max(1, int(total_pages or 1))
    page_window = []
    prev_block_page = 1
    next_block_page = 1
    has_prev_block = False
    has_next_block = False
    if total_pages > 0:
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

