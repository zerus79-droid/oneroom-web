"""수금 입금 자동반영 화면.

농협 등 은행 입출금 내역 파일(.xls/.xlsx)을 업로드하면 적요(입금자명)를
전체 건물의 현재 입주자 이름과 비교해 건물을 자동 감지하고, 그 건물의
입주자와 매칭한 뒤 선택한 건만 sukum01에 등록합니다.
"""
import io
import json
import os
import re
import time
import uuid
from datetime import date, datetime

from flask import flash, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    account_digits as _account_digits,
    building_label as _building_label,
    buildings_and_rooms as _buildings_and_rooms,
    calc_misu_amt as _calc_misu_amt,
    fmt_bunji_pair as _fmt_bunji_pair,
    login_required,
    make_pager as _make_pager,
    next_sukum_seq as _next_sukum_seq,
    pad_bunji as _pad_bunji,
    parse_page as _parse_page,
    require_write_access,
    table_columns as _table_columns,
)

try:
    import xlrd
except ImportError:  # pragma: no cover
    xlrd = None
from openpyxl import load_workbook

# 파일 업로드 보안 설정
ALLOWED_EXTENSIONS = {'.xls', '.xlsx', '.xlsm'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Excel 파일 매직 넘버 (파일 시작 바이트로 검증)
EXCEL_MAGIC_NUMBERS = {
    b'\xd0\xcf\x11\xe0',  # XLS (OLE2)
    b'PK\x03\x04',         # XLSX (ZIP)
}


def _patch_xlrd_object_errors():
    """농협 등 은행 .xls에 섞인 깨진 OBJECT 레코드(로고 등) 때문에 xlrd가
    'Unexpected data at end of OBJECT record'로 죽는 걸 막음 — 입금 데이터엔
    필요 없는 레코드라 통째로 무시."""
    if xlrd is None:
        return
    import xlrd.sheet as xlrd_sheet

    if getattr(xlrd_sheet.Sheet.handle_obj, "_patched", False):
        return

    def _ignore(self, data):
        return None

    _ignore._patched = True
    xlrd_sheet.Sheet.handle_obj = _ignore


def _upload_basename(filename):
    """폴더 선택 시 브라우저가 넘기는 'dir/file.xlsx' 경로에서 파일명만 뽑는다."""
    name = (filename or "").replace("\\", "/").strip()
    return os.path.basename(name) or name


def validate_file_upload(file):
    """파일 업로드 보안 검증.

    파일 크기, 확장자, 파일명, MIME 타입, 매직 넘버를 검증합니다.
    폴더에서 고른 파일(경로 포함 파일명)과 은행 내보내기 흔한 괄호·공백 파일명도 허용합니다.
    """
    if not file or not file.filename:
        return False, "파일이 선택되지 않았습니다."

    display_name = _upload_basename(file.filename)
    if not display_name or ".." in display_name or "\x00" in display_name:
        return False, "파일명이 올바르지 않습니다."

    # 파일 크기 확인
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return False, f"파일 크기가 너무 큽니다. 최대 {MAX_FILE_SIZE // (1024*1024)}MB 허용."

    if file_size == 0:
        return False, "빈 파일입니다."

    # 파일 확장자 확인 (경로가 있어도 basename 기준)
    file_ext = os.path.splitext(display_name)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        return False, f"허용되지 않는 파일 형식입니다. 허용: {', '.join(sorted(ALLOWED_EXTENSIONS))}"

    # 파일명 검증: 경로 조작만 막고, 은행 파일에 흔한 ()[]~ 등은 허용
    if not re.match(
        r"^[a-zA-Z0-9._\-가-힣\s()\[\]（）【】~,#&+'·]+$",
        display_name,
    ):
        return False, "파일명에 허용되지 않는 문자가 포함되어 있습니다."

    # MIME 타입 검증
    mime_type = (file.content_type or "").lower()
    mime_ok = any(
        allowed in mime_type
        for allowed in (
            "spreadsheet",
            "sheet",
            "excel",
            "ms-excel",
            "octet-stream",  # 일부 브라우저/OS는 Excel을 이렇게 보냄
            "zip",  # xlsx를 zip으로 보고하는 경우
        )
    )
    if not mime_ok:
        file.seek(0)
        header = file.read(8)
        file.seek(0)
        if not any(header.startswith(magic) for magic in EXCEL_MAGIC_NUMBERS):
            return False, "유효한 Excel 파일이 아닙니다. 파일 형식을 확인하세요."

    # 파일을 올바르게 읽을 수 있는지 검증
    try:
        file.seek(0)
        if file_ext in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(file, read_only=True, data_only=True)
            if not wb.sheetnames:
                return False, "Excel 파일에 시트가 없습니다."
            wb.close()
        elif file_ext == ".xls":
            if xlrd:
                # 농협 등 은행 .xls의 깨진 OBJECT 레코드 — 로더와 동일하게 패치 후 연다
                _patch_xlrd_object_errors()
                file.seek(0)
                wb = xlrd.open_workbook(
                    file_contents=file.read(),
                    formatting_info=False,
                )
                if wb.nsheets == 0:
                    return False, "Excel 파일에 시트가 없습니다."
            else:
                return False, "XLS 파일 지원이 설정되지 않았습니다. XLSX 파일을 사용하세요."
        file.seek(0)
    except Exception as e:
        return False, f"파일을 읽을 수 없습니다. 올바른 Excel 파일인지 확인하세요: {str(e)[:100]}"

    return True, None

# 매칭 결과(입금 목록)를 새로고침해도 다시 안 나오게 세션 쿠키 대신
# 서버 임시 파일에 저장 (쿠키엔 담기엔 큼) — POST 응답을 바로 렌더하지 않고
# GET으로 리다이렉트(PRG 패턴)해서 새로고침·뒤로가기로 인한 재제출을 막음
_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_import_tmp")
_TMP_MAX_AGE_SEC = 6 * 3600


def _tmp_path(token):
    safe = re.sub(r"[^a-f0-9]", "", token or "")
    return os.path.join(_TMP_DIR, f"{safe}.json") if safe else None


def _cleanup_tmp():
    try:
        now = time.time()
        for name in os.listdir(_TMP_DIR):
            p = os.path.join(_TMP_DIR, name)
            if now - os.path.getmtime(p) > _TMP_MAX_AGE_SEC:
                os.remove(p)
    except OSError:
        pass


def _save_state(building_list, deposits, auto_detected, filename="", account_no="", bunji1="", bunji2=""):
    """상태 저장. 하위호환성: bunji1/bunji2로도 받아서 building_list로 변환 가능."""
    os.makedirs(_TMP_DIR, exist_ok=True)
    _cleanup_tmp()
    token = uuid.uuid4().hex
    
    # 호환성: bunji1/bunji2가 전달되면 building_list로 변환
    if not building_list and bunji1 and bunji2:
        building_list = [(bunji1, bunji2)]
    
    with open(_tmp_path(token), "w", encoding="utf-8") as f:
        json.dump(
            {
                "building_list": building_list,
                "deposits": deposits,
                "auto_detected": auto_detected,
                "filename": filename or "",
                "account_no": account_no or "",
                # 하위호환성: 첫 번째 건물을 bunji1/bunji2로도 저장
                "bunji1": building_list[0][0] if building_list else "",
                "bunji2": building_list[0][1] if building_list else "",
            },
            f,
            ensure_ascii=False,
        )
    return token


def _load_state(token):
    path = _tmp_path(token)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _delete_state(token):
    path = _tmp_path(token)
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _write_state(token, state):
    path = _tmp_path(token)
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def _update_state_building(token, bunji1, bunji2):
    """건물 선택을 재설정. (사용자가 건물 선택을 다시 했을 때)

    Intentional: explicit address selection narrows matching to that one building
    (building_list = [(bunji1, bunji2)]). Multi-building auto-detect is overridden.
    """
    state = _load_state(token)
    if not state:
        return None
    # Explicit user choice → match only this building (intentional narrow).
    state["building_list"] = [(bunji1, bunji2)]
    state["bunji1"] = bunji1
    state["bunji2"] = bunji2
    state["auto_detected"] = False
    _write_state(token, state)
    return state


def _find_col(headers, *keys):
    for i, h in enumerate(headers):
        for k in keys:
            if k in h:
                return i
    return None


def _parse_amount(v):
    if v is None:
        return 0
    s = str(v).replace(",", "").strip()
    if not s:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _parse_date_any(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", str(v or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


_ACCOUNT_NO_RE = re.compile(r"\d[\d\-]{7,}\d")


def _extract_account_number(rows, header_row):
    """헤더 행 이전 부분(계좌정보 등)에서 계좌번호를 찾아 숫자만 반환.
    은행 파일은 보통 '계좌번호' 라벨과 실제 번호가 같은 행의 다른 칸에 들어있음
    (예: | 계좌번호 | | 356-1174-4206-13 | )."""
    scan_until = header_row if header_row is not None else min(15, len(rows))
    for row in rows[:scan_until]:
        if not row:
            continue
        cells = ["" if c is None else str(c).strip() for c in row[:10]]
        if not any("계좌" in c for c in cells):
            continue
        for c in cells:
            if "계좌" in c:
                continue
            m = _ACCOUNT_NO_RE.search(c)
            if m:
                return _account_digits(m.group())
    return None


def _extract_deposits_from_rows(rows, xlrd_book=None):
    header_row = None
    headers = []
    for i, row in enumerate(rows[:30]):
        vals = ["" if c is None else str(c).replace("\n", "").strip() for c in row[:14]]
        joined = " ".join(vals)
        if "거래일시" in joined and "입금" in joined:
            header_row = i
            headers = vals
            break
    account_no = _extract_account_number(rows, header_row)
    if header_row is None:
        return [], account_no

    col_dt = _find_col(headers, "거래일시")
    col_in = _find_col(headers, "입금금액", "입금액")
    col_name = _find_col(headers, "거래기록사항", "적요")
    if col_dt is None or col_in is None or col_name is None:
        return [], account_no

    deposits = []
    for row in rows[header_row + 1 :]:
        if col_dt >= len(row) or col_in >= len(row) or col_name >= len(row):
            continue
        raw_dt = row[col_dt]
        d = None
        if xlrd_book is not None and isinstance(raw_dt, float):
            try:
                t = xlrd.xldate_as_tuple(raw_dt, xlrd_book.datemode)
                d = date(t[0], t[1], t[2])
            except Exception:
                d = None
        if d is None:
            d = _parse_date_any(raw_dt)
        if not d:
            continue
        amount = _parse_amount(row[col_in])
        if amount <= 0:
            continue
        name = str(row[col_name] or "").strip()
        if not name or "예금이자" in name:
            continue
        deposits.append({"date": d.isoformat(), "amount": amount, "name": name})
    return deposits, account_no



def _load_bank_deposits_xls(raw_bytes):
    if xlrd is None:
        raise RuntimeError("xlrd 패키지가 설치되어 있지 않습니다.")
    _patch_xlrd_object_errors()
    book = xlrd.open_workbook(file_contents=raw_bytes, formatting_info=False)
    sheet = book.sheet_by_index(0)
    rows = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
    return _extract_deposits_from_rows(rows, xlrd_book=book)


def _load_bank_deposits_xlsx(raw_bytes):
    wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = [list(row) for row in ws.iter_rows(values_only=True)]
    return _extract_deposits_from_rows(rows)


def load_bank_deposits(filename, raw_bytes):
    """반환: (deposits, account_no_digits_or_None)"""
    suffix = (filename or "").lower().rsplit(".", 1)[-1]
    if suffix == "xls":
        return _load_bank_deposits_xls(raw_bytes)
    if suffix in ("xlsx", "xlsm"):
        return _load_bank_deposits_xlsx(raw_bytes)
    raise ValueError("지원 형식: .xls / .xlsx")


def _name_core(s):
    t = (s or "").strip()
    t = re.sub(r"\(.*?\)", "", t)
    t = re.sub(r"\s+", "", t)
    return t


def _name_parts(s):
    """비교용 조각. 괄호 안(닫히지 않은 '김호진(행복요양' 포함)도 따로 둠."""
    t = re.sub(r"\s+", "", (s or "").strip())
    if not t:
        return []
    parts = [t]
    for m in re.finditer(r"\(([^)]+)\)", t):
        inner = (m.group(1) or "").strip()
        if len(inner) >= 2:
            parts.append(inner)
    m = re.search(r"\(([^)]+)$", t)
    if m:
        inner = (m.group(1) or "").strip()
        if len(inner) >= 2:
            parts.append(inner)
    outer = re.sub(r"\(.*", "", t).strip()
    if len(outer) >= 2:
        parts.append(outer)
    seen = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return seen


def _name_matches(deposit_name, tenant_name):
    ds = _name_parts(deposit_name)
    ts = _name_parts(tenant_name)
    for d in ds:
        for t in ts:
            if len(d) < 2 or len(t) < 2:
                continue
            if d == t or d in t or t in d:
                return True
    return False


def _room_attached_names(text):
    """적요에서 호수 바로 옆 입금자명 후보를 뽑는다.

    마지막 'N호'를 입금 호수로 보고, 바로 뒤·앞 한글에서 2~4글자 후보를 만든다.
    (앞에 다른 이름이 길게 붙어도 세입자 매칭으로 걸러진다.)
    """
    text = re.sub(r"\s+", "", text or "")
    if not text:
        return []
    marks = list(re.finditer(r"(\d{2,4})호", text))
    if not marks:
        return []
    m = marks[-1]
    hosu = m.group(1).lstrip("0") or "0"
    pairs = []
    after = text[m.end():]
    m_after = re.match(r"([가-힣]{2,4})", after)
    if m_after:
        pairs.append((hosu, m_after.group(1)))
    before = text[: m.start()]
    m_run = re.search(r"([가-힣]{2,8})$", before)
    if m_run:
        run = m_run.group(1)
        for length in range(min(4, len(run)), 1, -1):
            pairs.append((hosu, run[-length:]))
            if len(run) > length:
                pairs.append((hosu, run[:length]))
    seen = set()
    out = []
    for hosu_n, name in pairs:
        key = (hosu_n, name)
        if key in seen:
            continue
        seen.add(key)
        out.append((hosu_n, name))
    return out


def _tenants_for_room_name(tenants, hosu_num, person_name):
    """호수+이름 쌍에 맞는 세입자."""
    hits = []
    for trow in tenants:
        hosu = (trow.get("hosu") or "").strip().upper().lstrip("0") or "0"
        if hosu != hosu_num:
            continue
        if _name_matches(person_name, trow.get("ipju_nm") or ""):
            hits.append(trow)
    return hits


def _name_near_hosu_score(text, tenant_name, hosu_num):
    """호수 힌트 근처(바로 뒤/앞)에 세입자명이 있으면 높은 점수."""
    text = re.sub(r"\s+", "", text or "")
    nm = re.sub(r"\s+", "", tenant_name or "")
    if len(nm) < 2 or not hosu_num:
        return 0
    nums = {hosu_num, hosu_num.zfill(3), hosu_num.zfill(4)}
    best = 0
    for num in nums:
        if not num:
            continue
        if re.search(re.escape(num) + r"호" + re.escape(nm), text):
            best = max(best, 100)
        if re.search(re.escape(nm) + r"\(?" + re.escape(num) + r"호", text):
            best = max(best, 90)
        for m in re.finditer(re.escape(num) + r"호", text):
            window = text[m.end() : m.end() + 12]
            if nm in window:
                best = max(best, 70)
        for m in re.finditer(re.escape(num) + r"호", text):
            window = text[max(0, m.start() - 12) : m.start()]
            if nm in window:
                best = max(best, 60)
        if nm in text:
            best = max(best, 10)
    return best


def _prefer_name_near_room(text, candidates):
    """여러 후보 중 적요의 호수 옆에 이름이 있는 쪽을 고른다."""
    if len(candidates) <= 1:
        return candidates
    scored = []
    for c in candidates:
        hosu = (c.get("hosu") or "").strip().upper().lstrip("0") or "0"
        scored.append((_name_near_hosu_score(text, c.get("ipju_nm") or "", hosu), c))
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    if top < 60:
        return candidates
    return [c for s, c in scored if s == top]


def _rent_amount_matches(amount, rent, manage, tol_ratio=0.03, tol_min=1000):
    """engine.py의 correct_unit_by_rent_amount와 같은 방식 — 입금액이 그 호실
    월세+관리비랑 비슷하면(오차 3% 또는 1000원 중 큰 쪽) 그 호실로 신뢰."""
    total = int(rent or 0) + int(manage or 0)
    if total <= 0:
        return False
    tol = max(tol_min, total * tol_ratio)
    return abs(amount - total) <= tol


def _narrow_by_room_hint(text, candidates, amount=None, allow_bare_number=True):
    """적요 텍스트에 '401호'/'1층'/그냥 숫자 같은 호수 힌트가 있으면 후보를 그 1곳으로
    좁힌다. 못 좁히면 원래 후보 그대로 반환.
    
    여러 건물이 섞여 있을 때: 같은 호수가 여러 건물에 있어도, 호수 매칭 후 금액이나
    건물/입주자 정보로 추가 구분할 수 있게 한다. (동명이인 동시에 호수도 같은 경우 제외)
    예: '김호현(401호' → 401호, '김호현(1층임차' → 1층(첫자리 1인 호실),
    '가람606 703호월세관리비' → 703호, '가람501월세' → 501(숫자만 — 그 호실 월세+관리비가
    입금액이랑 비슷할 때만 신뢰, engine.py 방식과 동일)."""
    if len(candidates) <= 1:
        return candidates

    def hosu_of(c):
        return (c.get("hosu") or "").strip().upper()
    
    def building_of(c):
        """건물 식별자 (bunji1, bunji2 튜플)"""
        return (c.get("bunji1") or "", c.get("bunji2") or "")

    # 호수명 힌트('401호', '1층임차' 등) — 가장 강한 신뢰도
    for m in re.finditer(r"(\d{2,4})\s*호", text):
        num = m.group(1).lstrip("0") or "0"
        hits = [c for c in candidates if hosu_of(c).lstrip("0") == num]
        if len(hits) == 1:
            return hits
        # 같은 호수가 여러 건물에 있으면, 금액으로 추가 필터링
        if len(hits) > 1 and amount is not None:
            by_amount = [c for c in hits if _rent_amount_matches(amount, c.get("rent_amt"), c.get("manage_amt"))]
            if len(by_amount) == 1:
                return by_amount
            # 금액도 여러 개면 경고/모두 반환하되, 사용자가 선택하도록
            if len(by_amount) > 1:
                return by_amount

    # '가람501월세' — 호수+월세는 강한 힌트. 금액이 달라도 그 호실로 확정
    # (금액 이상은 매칭 후 확인필요로 표시).
    for m in re.finditer(r"(\d{2,4})\s*월세", text):
        num = m.group(1).lstrip("0") or "0"
        hits = [c for c in candidates if hosu_of(c).lstrip("0") == num]
        if len(hits) == 1:
            return hits
        # 같은 호수가 여러 건물에 있으면, 금액/건물로 추가 필터링
        if len(hits) > 1 and amount is not None:
            by_amount = [c for c in hits if _rent_amount_matches(amount, c.get("rent_amt"), c.get("manage_amt"))]
            if len(by_amount) >= 1:
                return by_amount

    # 층 힌트('1층', '2층')
    m = re.search(r"(\d)\s*층", text)
    if m:
        hits = [c for c in candidates if hosu_of(c)[:1] == m.group(1)]
        if len(hits) == 1:
            return hits
        # 같은 층이 여러 건물에 있으면 금액으로 추가 필터링
        if len(hits) > 1 and amount is not None:
            by_amount = [c for c in hits if _rent_amount_matches(amount, c.get("rent_amt"), c.get("manage_amt"))]
            if len(by_amount) == 1:
                return by_amount
            if len(by_amount) > 1:
                return by_amount

    if allow_bare_number:
        for m in re.finditer(r"\d{3}", text):
            hits = [c for c in candidates if hosu_of(c) == m.group(0)]
            if len(hits) == 1:
                c = hits[0]
                if amount is None or _rent_amount_matches(amount, c.get("rent_amt"), c.get("manage_amt")):
                    return hits
            # 같은 호수가 여러 건물에 있으면 금액으로 필터링
            elif len(hits) > 1 and amount is not None:
                by_amount = [c for c in hits if _rent_amount_matches(amount, c.get("rent_amt"), c.get("manage_amt"))]
                if len(by_amount) >= 1:
                    return by_amount

    return candidates


_EXCLUDE_SCOPE_READY = False


def _ensure_exclude_scope_cols():
    """제외 항목에 주소·계좌 범위를 붙인다. 컬럼이 있으면 ALTER 하지 않는다."""
    global _EXCLUDE_SCOPE_READY
    if _EXCLUDE_SCOPE_READY:
        return
    cols = _table_columns("sukum_import_exclude")
    if "bunji1" not in cols:
        db.execute(
            "ALTER TABLE sukum_import_exclude "
            "ADD COLUMN bunji1 CHAR(4) NOT NULL DEFAULT ''"
        )
    if "bunji2" not in cols:
        db.execute(
            "ALTER TABLE sukum_import_exclude "
            "ADD COLUMN bunji2 CHAR(4) NOT NULL DEFAULT ''"
        )
    if "acct_no" not in cols:
        db.execute(
            "ALTER TABLE sukum_import_exclude "
            "ADD COLUMN acct_no VARCHAR(32) NOT NULL DEFAULT ''"
        )
    _EXCLUDE_SCOPE_READY = True


def list_exclude_keywords():
    _ensure_exclude_scope_cols()
    rows = db.query(
        """
        SELECT e.id, e.keyword, e.bunji1, e.bunji2, e.acct_no, b.juso
        FROM sukum_import_exclude e
        LEFT JOIN bd01 b ON b.bunji1=e.bunji1 AND b.bunji2=e.bunji2
        ORDER BY e.keyword, e.bunji1, e.bunji2, e.id
        """
    ) or []
    for r in rows:
        r["bunji1"] = _pad_bunji(r.get("bunji1"))
        r["bunji2"] = _pad_bunji(r.get("bunji2")) if r.get("bunji1") else ""
        r["acct_no"] = _account_digits(r.get("acct_no") or "")
    return rows


def add_exclude_keyword(keyword, bunji1="", bunji2="", acct_no=""):
    _ensure_exclude_scope_cols()
    keyword = (keyword or "").strip()
    bunji1 = _pad_bunji(bunji1)
    bunji2 = _pad_bunji(bunji2) if bunji1 else ""
    acct_no = _account_digits(acct_no or "")
    if not keyword and not bunji1 and not acct_no:
        return
    db.execute(
        "INSERT INTO sukum_import_exclude "
        "(keyword, bunji1, bunji2, acct_no, sys_dt, uid) "
        "VALUES (%s, %s, %s, %s, NOW(), %s)",
        (keyword, bunji1 or "", bunji2 or "", acct_no, session.get("sabun") or ""),
    )


def update_exclude_keyword(keyword_id, keyword, bunji1="", bunji2="", acct_no=""):
    _ensure_exclude_scope_cols()
    try:
        keyword_id = int(keyword_id or 0)
    except (TypeError, ValueError):
        return
    if keyword_id <= 0:
        return
    keyword = (keyword or "").strip()
    bunji1 = _pad_bunji(bunji1)
    bunji2 = _pad_bunji(bunji2) if bunji1 else ""
    acct_no = _account_digits(acct_no or "")
    if not keyword and not bunji1 and not acct_no:
        return
    db.execute(
        "UPDATE sukum_import_exclude "
        "SET keyword=%s, bunji1=%s, bunji2=%s, acct_no=%s, sys_dt=NOW(), uid=%s "
        "WHERE id=%s",
        (keyword, bunji1 or "", bunji2 or "", acct_no, session.get("sabun") or "", keyword_id),
    )


def delete_exclude_keyword(keyword_id):
    db.execute("DELETE FROM sukum_import_exclude WHERE id=%s", (keyword_id,))


_MATCH_RULE_READY = False


def _ensure_match_rule_table():
    """수동매칭(적요→호실) 저장 테이블. 없으면 생성."""
    global _MATCH_RULE_READY
    if _MATCH_RULE_READY:
        return
    try:
        cols = _table_columns("sukum_import_match")
    except Exception:
        cols = set()
    if not cols:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS sukum_import_match (
              id INT AUTO_INCREMENT PRIMARY KEY,
              keyword VARCHAR(120) NOT NULL DEFAULT '',
              bunji1 CHAR(4) NOT NULL DEFAULT '',
              bunji2 CHAR(4) NOT NULL DEFAULT '',
              hosu VARCHAR(16) NOT NULL DEFAULT '',
              ipju_seq CHAR(2) NOT NULL DEFAULT '',
              acct_no VARCHAR(32) NOT NULL DEFAULT '',
              sys_dt DATETIME NULL,
              uid VARCHAR(20) NOT NULL DEFAULT ''
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    _MATCH_RULE_READY = True


def list_match_rules():
    _ensure_match_rule_table()
    rows = db.query(
        """
        SELECT m.id, m.keyword, m.bunji1, m.bunji2, m.hosu, m.ipju_seq, m.acct_no, b.juso
        FROM sukum_import_match m
        LEFT JOIN bd01 b ON b.bunji1=m.bunji1 AND b.bunji2=m.bunji2
        ORDER BY m.keyword, m.bunji1, m.bunji2, m.hosu, m.id
        """
    ) or []
    for r in rows:
        r["bunji1"] = _pad_bunji(r.get("bunji1"))
        r["bunji2"] = _pad_bunji(r.get("bunji2")) if r.get("bunji1") else ""
        r["hosu"] = (r.get("hosu") or "").strip().upper()
        r["ipju_seq"] = str(r.get("ipju_seq") or "").zfill(2)
        r["acct_no"] = _account_digits(r.get("acct_no") or "")
    return rows


def add_match_rule(keyword, bunji1, bunji2, hosu, ipju_seq, acct_no=""):
    _ensure_match_rule_table()
    keyword = (keyword or "").strip()
    bunji1 = _pad_bunji(bunji1)
    bunji2 = _pad_bunji(bunji2) if bunji1 else ""
    hosu = (hosu or "").strip().upper()
    ipju_seq = str(ipju_seq or "").zfill(2)
    acct_no = _account_digits(acct_no or "")
    if not keyword or not bunji1 or not bunji2 or not hosu:
        return
    # 같은 적요+계좌면 갱신 (중복 방지)
    existing = db.query_one(
        """
        SELECT id FROM sukum_import_match
        WHERE keyword=%s AND acct_no=%s
        LIMIT 1
        """,
        (keyword, acct_no),
    )
    if existing and existing.get("id"):
        db.execute(
            """
            UPDATE sukum_import_match
            SET bunji1=%s, bunji2=%s, hosu=%s, ipju_seq=%s, sys_dt=NOW(), uid=%s
            WHERE id=%s
            """,
            (bunji1, bunji2, hosu, ipju_seq, session.get("sabun") or "", existing["id"]),
        )
        return
    db.execute(
        """
        INSERT INTO sukum_import_match
          (keyword, bunji1, bunji2, hosu, ipju_seq, acct_no, sys_dt, uid)
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
        """,
        (keyword, bunji1, bunji2, hosu, ipju_seq, acct_no, session.get("sabun") or ""),
    )


def update_match_rule(rule_id, keyword, bunji1, bunji2, hosu, ipju_seq, acct_no=""):
    _ensure_match_rule_table()
    try:
        rule_id = int(rule_id or 0)
    except (TypeError, ValueError):
        return
    if rule_id <= 0:
        return
    keyword = (keyword or "").strip()
    bunji1 = _pad_bunji(bunji1)
    bunji2 = _pad_bunji(bunji2) if bunji1 else ""
    hosu = (hosu or "").strip().upper()
    ipju_seq = str(ipju_seq or "").zfill(2)
    acct_no = _account_digits(acct_no or "")
    if not keyword or not bunji1 or not hosu:
        return
    db.execute(
        """
        UPDATE sukum_import_match
        SET keyword=%s, bunji1=%s, bunji2=%s, hosu=%s, ipju_seq=%s, acct_no=%s,
            sys_dt=NOW(), uid=%s
        WHERE id=%s
        """,
        (keyword, bunji1, bunji2, hosu, ipju_seq, acct_no, session.get("sabun") or "", rule_id),
    )


def delete_match_rule(rule_id):
    _ensure_match_rule_table()
    db.execute("DELETE FROM sukum_import_match WHERE id=%s", (rule_id,))


def _find_match_rule(name, account_no, rules):
    """적요 완전일치 우선, 없으면 포함 매칭. 계좌 조건 있으면 일치해야 함."""
    name = (name or "").strip()
    acct = _account_digits(account_no or "")
    if not name:
        return None
    exact = []
    partial = []
    for r in rules or []:
        kw = (r.get("keyword") or "").strip()
        if not kw:
            continue
        racct = _account_digits(r.get("acct_no") or "")
        if racct and racct != acct:
            continue
        if kw == name:
            exact.append(r)
        elif kw in name:
            partial.append(r)
    hits = exact or partial
    if not hits:
        return None
    # 가장 긴 keyword 우선
    hits.sort(key=lambda r: len(r.get("keyword") or ""), reverse=True)
    return hits[0]


def _tenant_by_keys(tenants, bunji1, bunji2, hosu, ipju_seq):
    bunji1 = _pad_bunji(bunji1)
    bunji2 = _pad_bunji(bunji2)
    hosu = (hosu or "").strip().upper()
    ipju_seq = str(ipju_seq or "").zfill(2)
    for trow in tenants or []:
        if _pad_bunji(trow.get("bunji1")) != bunji1:
            continue
        if _pad_bunji(trow.get("bunji2")) != bunji2:
            continue
        if (trow.get("hosu") or "").strip().upper() != hosu:
            continue
        if str(trow.get("ipju_seq") or "").zfill(2) != ipju_seq:
            continue
        return trow
    return None


def _matches_excluded(name, bunji1, bunji2, account_no, rules):
    """적요 글자 + (있으면) 주소 + (있으면) 계좌. 비어 있는 조건은 전체."""
    name = name or ""
    bunji1 = _pad_bunji(bunji1)
    bunji2 = _pad_bunji(bunji2)
    acct = _account_digits(account_no or "")
    for r in rules or []:
        kw = (r.get("keyword") or "").strip()
        rb1 = _pad_bunji(r.get("bunji1"))
        rb2 = _pad_bunji(r.get("bunji2"))
        racct = _account_digits(r.get("acct_no") or "")
        if not kw and not rb1 and not racct:
            continue
        if kw and kw not in name:
            continue
        if rb1 and (rb1 != bunji1 or rb2 != bunji2):
            continue
        if racct and racct != acct:
            continue
        return True
    return False



def _deposit_is_excluded(name, building_list, dep_account_no, rules):
    """입금 1건이 제외 규칙에 걸리는지 — 건물 목록 중 하나라도 매칭되면 제외.
    계좌는 state에 합쳐 둔 joined account_no가 아니라 입금별 account_no를 쓴다."""
    acct = dep_account_no or ""
    if not building_list:
        return _matches_excluded(name, "", "", acct, rules)
    for b1, b2 in building_list:
        if _matches_excluded(name, b1, b2, acct, rules):
            return True
    return False


def buildings_by_account(account_no):
    """계좌번호(숫자만)를 쓰는 건물 목록. 책임관리는 여러 건물이 같은(관리사무소) 통장을
    같이 쓰는 게 정상이라 0곳/1곳/여러 곳 다 나올 수 있음."""
    if not account_no:
        return []
    rows = db.query("SELECT bunji1, bunji2, bank_cd FROM bd01 WHERE bank_cd IS NOT NULL AND bank_cd<>''")
    return [
        (r["bunji1"], r["bunji2"])
        for r in rows
        if _account_digits(r.get("bank_cd")) == account_no
    ]


def detect_building(account_no):
    """계좌번호로 건물 목록을 반환.
    - 1곳 특정: (bunji1, bunji2) 또는 building_list=[(bunji1, bunji2)]로 자동 확정
    - 여러 곳: building_list 전체를 세입자 이름 매칭에 사용 (건물 선택 불필요)
    - 0곳: empty list 반환 (건물을 직접 선택하게 함)
    """
    return buildings_by_account(account_no)


_AMOUNT_FLAG_MULTIPLE = 2  # 월세+관리비 기준액의 이 배수를 넘으면 "확인필요"로 표시


_UTILITY_RE = re.compile(r"수도|전기|가스|공과금|공공요금|난방|온수|한전|열요금")
_MISC_RE = re.compile(r"수도|전기|가스|공과금|공공요금|난방|온수|한전|열요금|대납|기타")
_LUMP_RE = re.compile(r"보증|계약|잔금")
_ETC_CHAR_CACHE = None


def _is_utility_desc(name):
    """수도·전기·가스·공과금 적요는 월세가 아님. '월세'가 같이 있으면 임대료로 본다."""
    s = re.sub(r"\s+", "", name or "")
    if not s or "월세" in s:
        return False
    return bool(_UTILITY_RE.search(s))


def _is_misc_desc(name):
    """전기·대납·기타 등 월세/관리비가 아닌 입금 적요."""
    s = re.sub(r"\s+", "", name or "")
    if not s or "월세" in s:
        return False
    return bool(_MISC_RE.search(s))


def _amount_suggests_etc(amount, contract_due):
    """입금액이 계약액(월세+관리비)보다 훨씬 작으면 기타 후보."""
    try:
        amount = int(amount or 0)
        contract_due = int(contract_due or 0)
    except (TypeError, ValueError):
        return False
    if amount <= 0 or contract_due <= 0:
        return False
    return amount < contract_due * 0.5


def _etc_sukum_char():
    """수금성격 코드 중 이름이 '기타'인 것. 없으면 '04'.
    미수 집계는 sukum_char='01'만 보므로 01이 아니면 월세에 잡히지 않는다."""
    global _ETC_CHAR_CACHE
    if _ETC_CHAR_CACHE is not None:
        return _ETC_CHAR_CACHE
    try:
        row = db.query_one(
            """
            SELECT g_sub_cd FROM gicho_code
            WHERE g_cd='01' AND g_sub_cd <> '00'
              AND g_cd_nm LIKE %s
            ORDER BY g_sub_cd
            LIMIT 1
            """,
            ("%기타%",),
        )
        if row and row.get("g_sub_cd") is not None:
            _ETC_CHAR_CACHE = str(row.get("g_sub_cd")).strip().zfill(2)
            return _ETC_CHAR_CACHE
    except Exception:
        pass
    _ETC_CHAR_CACHE = "04"
    return _ETC_CHAR_CACHE


def _is_lump_desc(name):
    """보증·계약·잔금은 월세가 섞여 있어도 자동 반영하지 않는다. 호실만 고르면 들어간다."""
    t = re.sub(r"\s+", "", name or "")
    return bool(t and _LUMP_RE.search(t))


def _monthly_due(t):
    return int(t.get("rent_amt") or 0) + int(t.get("manage_amt") or 0)


def _alloc_amounts(dues, amount):
    """호실별 월세 기준으로 입금액을 나눔. n개월분이면 각 호 n개월, 아니면
    floor(입금/월세합)개월 + 잔액은 월세 큰 호실. 합은 항상 입금액."""
    amount = int(amount or 0)
    n = len(dues)
    if n <= 0:
        return []
    if n == 1:
        return [amount]
    if any(int(d or 0) <= 0 for d in dues):
        base = amount // n
        alloc = [base] * n
        alloc[0] += amount - base * n
        return alloc
    dues = [int(d or 0) for d in dues]
    total = sum(dues)
    months = 1
    matched = False
    for m in range(1, 25):
        if _rent_amount_matches(amount, m * total, 0):
            months = m
            matched = True
            break
    if not matched:
        months = max(1, amount // total) if total > 0 else 1
    alloc = [d * months for d in dues]
    leftover = amount - sum(alloc)
    if leftover:
        biggest = max(range(n), key=lambda i: dues[i])
        alloc[biggest] += leftover
        if alloc[biggest] < 0:
            rest = amount
            alloc = [0] * n
            for i in range(n - 1):
                take = min(max(dues[i], 0), rest)
                alloc[i] = take
                rest -= take
            alloc[-1] = rest
    return alloc


def _alloc_by_caps(caps, amount):
    """앞 호실부터 한도(미수)만큼 채우고 마지막 호실이 잔액. 합은 항상 입금액."""
    amount = int(amount or 0)
    n = len(caps)
    if n <= 0:
        return []
    if n == 1:
        return [amount]
    alloc = [0] * n
    rest = amount
    for i in range(n - 1):
        take = min(max(int(caps[i] or 0), 0), rest)
        alloc[i] = take
        rest -= take
    alloc[-1] = rest
    return alloc


def _alloc_lump(candidates, amount):
    """복수 호실에 입금액을 나눔. n개월분이면 각 호 n개월, 아니면
    floor(입금/월세합)개월 + 잔액은 월세 큰 호실."""
    if not candidates:
        return []
    if len(candidates) == 1:
        return [(candidates[0], int(amount))]
    ordered = sorted(candidates, key=_hosu_sort_key)
    alloc = _alloc_amounts([_monthly_due(c) for c in ordered], amount)
    return list(zip(ordered, alloc))


def _split_parts_from_options(room_options, amount):
    """2호실 선택 시 호실별 금액. 미수가 있으면 미수부터, 없으면 월세 기준."""
    rooms = [
        o for o in (room_options or [])
        if "|" in (o.get("value") or "")
    ]
    if not rooms:
        return {}
    amount = int(amount or 0)
    misus = [int(o.get("misu") or 0) for o in rooms]
    dues = [int(o.get("due") or 0) for o in rooms]
    if any(m > 0 for m in misus):
        alloc = _alloc_by_caps(misus, amount)
    else:
        alloc = _alloc_amounts(dues, amount)
    return {
        rooms[i]["value"]: alloc[i]
        for i in range(len(rooms))
        if alloc[i] > 0
    }


def _try_split_lump(candidates, amount):
    """같은 입금자 복수 호실 + 입금액이 각 호 월세합(또는 n개월)과 같으면 호실별로 나눔.
    예: 한현승 2,550,000 → 201 2,000,000 + B01 550,000."""
    if len(candidates) < 2:
        return None
    dues = [_monthly_due(c) for c in candidates]
    if any(d <= 0 for d in dues):
        return None
    total = sum(dues)
    if not any(
        _rent_amount_matches(int(amount), n * total, 0) for n in range(1, 25)
    ):
        return None
    return _alloc_lump(candidates, amount)


def _finish_match_row(
    dep, match, amount, candidates, all_room_options,
    existing_set, bunji1, bunji2, needs_pick,
):
    hosu = (match.get("hosu") or "").strip().upper() if match else ""
    ipju_seq = str(match.get("ipju_seq") or "").zfill(2) if match else ""
    
    # 매칭된 세입자의 건물 정보 사용 (여러 건물인 경우)
    matched_bunji1 = match.get("bunji1") if match else bunji1
    matched_bunji2 = match.get("bunji2") if match else bunji2
    
    # 계약 월세+관리비 (매칭된 호실 기준). 미매칭/복수호실 선택 전이면 0
    contract_due = _monthly_due(match) if match else 0
    building_nm = (
        _building_label(matched_bunji1, matched_bunji2)
        if matched_bunji1 and matched_bunji2
        else ""
    )

    row = {
        "date": dep["date"],
        "amount": amount,
        "name": dep["name"],
        "hosu": hosu,
        "ipju_seq": ipju_seq,
        "bunji1": matched_bunji1,  # 매칭된 세입자의 건물
        "bunji2": matched_bunji2,
        "building_nm": building_nm,
        "contract_due": contract_due,
        "tenant_nm": match.get("ipju_nm") if match else "",
        "amount_flag": False,
        "needs_pick": needs_pick,
        "source_file": dep.get("source_file", ""),
        "account_no": dep.get("account_no", ""),
        "dep_id": dep.get("dep_id", ""),
        "room_options": (
            _room_options(
                candidates, with_combo=True,
                bunji1=bunji1, bunji2=bunji2,
                as_of=date.fromisoformat(dep["date"]),
            )
            if needs_pick
            else (all_room_options if not match else [])
        ),
    }
    if needs_pick:
        row["status"] = "matched"
    elif not match:
        row["status"] = "unmatched"
    elif (matched_bunji1, matched_bunji2, hosu, ipju_seq, row["date"], int(amount)) in existing_set:
        row["status"] = "duplicate"
    else:
        row["status"] = "matched"
        expected = _monthly_due(match)
        if expected > 0 and amount > expected * _AMOUNT_FLAG_MULTIPLE:
            misu = _calc_misu_amt(
                matched_bunji1, matched_bunji2, hosu, ipju_seq,
                match.get("rent_amt"), match.get("manage_amt"),
                match.get("ipju_dt"), as_of=date.fromisoformat(dep["date"]),
            )
            if amount > expected + misu:
                row["amount_flag"] = True
                row["room_options"] = all_room_options

    # 수금 성격: 월세(rent) / 기타(etc)
    misc = _is_misc_desc(dep.get("name") or "")
    small = bool(match) and _amount_suggests_etc(amount, contract_due)
    if misc:
        row["pay_kind"] = "etc"
        row["suggest_etc"] = False
        # 기타는 월세 과다입금 확인필요 플래그 대신 기타로 표시
        if row.get("status") == "matched" and not needs_pick:
            row["amount_flag"] = False
    elif small and row.get("status") == "matched":
        row["pay_kind"] = "etc"  # 기본 기타, 화면에서 월세로 바꿀 수 있음
        row["suggest_etc"] = True
        row["amount_flag"] = True  # 확인필요(기타?) — 자동체크 안 함
    else:
        row["pay_kind"] = "rent"
        row["suggest_etc"] = False
    return row


def _match_deposits(deposits, building_list, account_no="", bunji1="", bunji2="", manual_picks=None):
    """세입자 매칭.

    우선순위: 입금 계좌번호 → 건물(bank_cd) 한정 후, 그 안에서 이름/호수 매칭.
    건물이 여러 곳이어도 계좌가 다르면 서로 섞지 않는다.

    다중 파일 처리 시 주의사항:
    - 개선 1: 입금파일 로더에서 (date, amount, name) 중복 제거 → 같은 파일들에서 중복 입금 감지
    - 개선 2: building_list_set으로 중복 제거하고 sorted() → 큰 building_list 방지
    - 개선 3: try/except로 개별 파일 오류 처리 → 1개 파일 깨져도 나머지 계속 진행
    - 개선 4: 대량 deposits 저장(>1000건)은 _import_tmp/*.json 성능 모니터링 권장
    
    Args:
        deposits: 입금 목록 (source_file, account_no 정보 포함)
        building_list: [(bunji1, bunji2), ...] 건물 목록. 빈 리스트면 bunji1/bunji2로 폴백
        account_no: 계좌번호(제외 규칙 적용용)
        bunji1, bunji2: building_list가 빈 경우 대체용 (하위호환성)
    """
    # building_list가 없으면 bunji1/bunji2 폴백
    if not building_list:
        if bunji1 and bunji2:
            building_list = [(bunji1, bunji2)]
        else:
            return []

    # JSON 상태 복원 시 [(b1,b2)]가 [[b1,b2]]로 올 수 있음 → 전부 튜플로 정규화
    building_list = [
        (b[0], b[1]) if not isinstance(b, tuple) else b
        for b in building_list
        if b and len(b) >= 2
    ]
    if not building_list:
        return []
    
    # 모든 건물의 세입자 조회 — WHERE (bunji1, bunji2) IN (...)
    placeholders = ",".join(["(%s, %s)"] * len(building_list))
    params = []
    for b1, b2 in building_list:
        params.extend([b1, b2])
    
    tenants = db.query(
        f"""
        SELECT bunji1, bunji2, hosu, ipju_seq, ipju_nm, rent_amt, manage_amt, ipju_dt
        FROM bd03_det
        WHERE (bunji1, bunji2) IN ({placeholders})
          AND (out_dt IS NULL OR out_dt < '1000-01-01')
        """,
        params,
    )
    
    # 각 건물별로 기존 수금 기록 조회
    # 개선: (bunji1, bunji2)를 key에 포함시킴 → 다른 건물의 같은 호실/날짜/금액도 구분
    existing = db.query(
        f"""
        SELECT bunji1, bunji2, hosu, ipju_seq, DATE(sukum_dt) AS d, su_sil_amt
        FROM sukum01
        WHERE (bunji1, bunji2) IN ({placeholders})
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
        """,
        params,
    )
    existing_set = {
        (
            r.get("bunji1"),
            r.get("bunji2"),
            (r.get("hosu") or "").strip().upper(),
            str(r.get("ipju_seq") or "").zfill(2),
            r["d"].isoformat() if r.get("d") else "",
            int(r.get("su_sil_amt") or 0),
        )
        for r in existing
    }
    exclude_rules = list_exclude_keywords()
    match_rules = list_match_rules()
    manual_picks = manual_picks or {}
    all_room_options = _room_options(tenants)

    # 대표 건물(첫 번째) — UI 표시용
    primary_bunji1, primary_bunji2 = (building_list[0] if building_list else ("", ""))

    # 계좌번호 → 건물 캐시 (입금 건마다 우선 이 건물들로 후보를 좁힘)
    _acct_buildings_cache = {}

    def _scope_tenants_for_deposit(dep):
        """우선순위 1: 입금파일 계좌번호 ↔ 건물 bank_cd 매칭으로 세입자 범위 한정."""
        dep_acct = _account_digits(dep.get("account_no") or "")
        if not dep_acct:
            return tenants, list(building_list)
        if dep_acct not in _acct_buildings_cache:
            _acct_buildings_cache[dep_acct] = set(buildings_by_account(dep_acct))
        acct_buildings = _acct_buildings_cache[dep_acct]
        if not acct_buildings:
            # 계좌는 있는데 등록 건물이 없으면 전체로 두지 않고 빈 범위(오매칭 방지)
            return [], []
        scope_buildings = [b for b in building_list if b in acct_buildings]
        if not scope_buildings:
            # building_list 밖 계좌 건물 — 로드된 세입자 중 계좌 건물만
            scope_buildings = sorted(acct_buildings)
        scope_set = set(scope_buildings)
        scoped = [
            trow for trow in tenants
            if (trow.get("bunji1"), trow.get("bunji2")) in scope_set
        ]
        return scoped, scope_buildings

    results = []
    for dep in deposits:
        scope_tenants, scope_buildings = _scope_tenants_for_deposit(dep)
        scope_primary_b1, scope_primary_b2 = (
            scope_buildings[0] if scope_buildings else (primary_bunji1, primary_bunji2)
        )
        scope_room_options = (
            _room_options(scope_tenants) if scope_tenants is not tenants else all_room_options
        )

        # Prefer per-deposit account_no over joined state account_no ("; "-joined).
        exclude_buildings = scope_buildings or building_list
        if _deposit_is_excluded(dep["name"], exclude_buildings, dep.get("account_no") or "", exclude_rules):
            continue  # 제외 목록에 걸리면 매칭 결과에 아예 표시하지 않음

        # 수동매칭 규칙 / 이번 세션 선택 우선
        forced = None
        pick = manual_picks.get(dep.get("dep_id") or "")
        if isinstance(pick, dict) and pick.get("room") and "|" in pick.get("room"):
            ph, ps = pick["room"].split("|", 1)
            forced = _tenant_by_keys(
                scope_tenants,
                pick.get("bunji1") or scope_primary_b1,
                pick.get("bunji2") or scope_primary_b2,
                ph, ps,
            )
        if forced is None:
            rule = _find_match_rule(dep.get("name") or "", dep.get("account_no") or "", match_rules)
            if rule:
                forced = _tenant_by_keys(
                    scope_tenants if scope_tenants else tenants,
                    rule.get("bunji1"), rule.get("bunji2"),
                    rule.get("hosu"), rule.get("ipju_seq"),
                )
                # 범위에 없으면 전체 tenants에서 재시도
                if forced is None:
                    forced = _tenant_by_keys(
                        tenants,
                        rule.get("bunji1"), rule.get("bunji2"),
                        rule.get("hosu"), rule.get("ipju_seq"),
                    )
        if forced is not None:
            results.append(
                _finish_match_row(
                    dep, forced, dep["amount"], [forced], scope_room_options,
                    existing_set, scope_primary_b1, scope_primary_b2, False,
                )
            )
            continue

        # 전기·대납·기타 적요도 호실 매칭은 하되, _finish_match_row에서 pay_kind=etc로 표시
        # 1) 호수에 붙은 이름('201호한명노')을 최우선 — 단, 계좌 건물 범위 안에서만
        paired = []
        for hosu_num, person in _room_attached_names(dep["name"]):
            paired.extend(_tenants_for_room_name(scope_tenants, hosu_num, person))
        # dedupe by identity
        paired_uniq = []
        seen_ids = set()
        for c in paired:
            key = (
                c.get("bunji1"), c.get("bunji2"),
                (c.get("hosu") or "").strip().upper(),
                str(c.get("ipju_seq") or "").zfill(2),
            )
            if key in seen_ids:
                continue
            seen_ids.add(key)
            paired_uniq.append(c)

        name_candidates = [t for t in scope_tenants if _name_matches(dep["name"], t.get("ipju_nm") or "")]

        if len(paired_uniq) == 1:
            candidates = paired_uniq
        elif len(paired_uniq) > 1:
            candidates = _narrow_by_room_hint(
                dep["name"], paired_uniq, amount=dep["amount"], allow_bare_number=False
            )
            if len(candidates) > 1:
                one_amt = [
                    c for c in candidates
                    if _rent_amount_matches(dep["amount"], c.get("rent_amt"), c.get("manage_amt"))
                ]
                if len(one_amt) == 1:
                    candidates = one_amt
                else:
                    candidates = _prefer_name_near_room(dep["name"], candidates)
        elif len(name_candidates) > 1:
            # 동명이인/복수호실 — 호수 힌트로 좁히되, 호수 옆 이름에 가까운 후보 우선
            candidates = _narrow_by_room_hint(
                dep["name"], name_candidates, amount=dep["amount"], allow_bare_number=True
            )
            candidates = _prefer_name_near_room(dep["name"], candidates)
            if len(candidates) > 1:
                one_amt = [
                    c for c in candidates
                    if _rent_amount_matches(dep["amount"], c.get("rent_amt"), c.get("manage_amt"))
                ]
                if len(one_amt) == 1:
                    candidates = one_amt
        elif not name_candidates:
            # 이름 매칭이 없을 때만 호수 단독 매칭(가람501월세 등).
            # 단, 적요에 '201호OOO' 형태 이름이 있었는데 세입자에 없으면 호수만으로 확정하지 않음.
            attached = _room_attached_names(dep["name"])
            if attached:
                candidates = []  # 호수+이름인데 세입자 불일치 → 미매칭/수동
            else:
                candidates = _narrow_by_room_hint(
                    dep["name"], scope_tenants, amount=dep["amount"], allow_bare_number=True
                )
                if len(candidates) != 1:
                    candidates = name_candidates
        else:
            candidates = name_candidates

        # 보증·계약·잔금(월세 섞인 경우 포함)은 자동 반영하지 않음 — 호실 선택 후 반영
        if _is_lump_desc(dep["name"]):
            match = candidates[0] if len(candidates) == 1 else None
            needs_pick = len(candidates) > 1
            row = _finish_match_row(
                dep, match, dep["amount"], candidates, scope_room_options,
                existing_set, scope_primary_b1, scope_primary_b2, needs_pick=needs_pick,
            )
            if row["status"] == "matched" and not needs_pick:
                row["amount_flag"] = True
                row["room_options"] = scope_room_options
            results.append(row)
            continue

        split = _try_split_lump(candidates, dep["amount"]) if len(candidates) > 1 else None
        if split:
            for match, amt in split:
                results.append(
                    _finish_match_row(
                        dep, match, amt, candidates, scope_room_options,
                        existing_set, scope_primary_b1, scope_primary_b2, needs_pick=False,
                    )
                )
            continue

        match = candidates[0] if len(candidates) == 1 else None
        needs_pick = len(candidates) > 1
        results.append(
            _finish_match_row(
                dep, match, dep["amount"], candidates, scope_room_options,
                existing_set, scope_primary_b1, scope_primary_b2, needs_pick=needs_pick,
            )
        )

    # 표시 순서: 미매칭 → 확인필요(금액 이상/복수호실) → 반영예정 → 날짜중복(이미 등록됨)
    def _sort_key(r):
        if r["status"] == "unmatched":
            return 0
        if r["status"] == "matched" and (r.get("amount_flag") or r.get("needs_pick")):
            return 1
        if r["status"] == "matched":
            return 2
        return 3  # duplicate

    results.sort(key=_sort_key)
    return results


def _hosu_sort_key(t):
    """지하(B) 먼저, 그다음 지상 1층→높은 층."""
    h = (t.get("hosu") if isinstance(t, dict) else t) or ""
    h = str(h).strip().upper()
    if h.startswith("B"):
        tail = h[1:].lstrip("0") or "0"
        try:
            return (0, int(tail), h)
        except ValueError:
            return (0, 0, h)
    digits = "".join(c for c in h if c.isdigit())
    try:
        return (1, int(digits or 0), h)
    except ValueError:
        return (2, 0, h)


def _room_options(tenants, with_combo=False, bunji1="", bunji2="", as_of=None):
    opts = []
    tenants = sorted(tenants, key=_hosu_sort_key)
    for t in tenants:
        h = (t.get("hosu") or "").strip().upper()
        seq = str(t.get("ipju_seq") or "").zfill(2)
        nm = (t.get("ipju_nm") or "").strip()
        if not h or not seq:
            continue
        misu = 0
        if bunji1 and bunji2 and as_of:
            misu = _calc_misu_amt(
                bunji1, bunji2, h, seq,
                t.get("rent_amt"), t.get("manage_amt"),
                t.get("ipju_dt"), as_of=as_of,
            )
        opts.append({
            "value": f"{h}|{seq}",
            "label": f"{h}호 {nm}".strip(),
            "misu": misu,
            "due": _monthly_due(t),
        })
    if with_combo and len(opts) >= 2:
        opts.append({"value": "ALL", "label": f"{len(opts)}호실"})
    return opts


def _insert_sukum(bunji1, bunji2, hosu, ipju_seq, sukum_dt, amount, name, pay_kind="rent"):
    hosu = (hosu or "").strip().upper()
    ipju_seq = str(ipju_seq or "").zfill(2)
    if not (hosu and ipju_seq) or int(amount or 0) <= 0:
        return False
    sukum_seq = _next_sukum_seq(sukum_dt, bunji1, bunji2, hosu)
    is_etc = (pay_kind or "rent") == "etc"
    sukum_char = _etc_sukum_char() if is_etc else "01"
    kind_label = "기타" if is_etc else "월세"
    desc = f"입금파일 자동반영 [{kind_label}] ({name or ''})"
    db.execute(
        """
        INSERT INTO sukum01 (
            sukum_dt, sukum_seq, bunji1, bunji2, hosu, ipju_seq,
            sukum_char, sukum_gb, manage_desc, su_sil_amt, su_dache_amt,
            suri_dt, suri_seq, s_method, del_yn, sys_dt, uid
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, '03', %s, %s, 0,
            NULL, '', '', 'N', NOW(), %s
        )
        """,
        (
            sukum_dt + " 00:00:00",
            sukum_seq,
            bunji1,
            bunji2,
            hosu,
            ipju_seq,
            sukum_char,
            desc,
            int(amount),
            session.get("sabun") or "",
        ),
    )
    return True


def _apply_selected(rows, bunji1, bunji2, selected_idx, manual_overrides, split_map=None, pay_kinds=None):
    """선택한 입금들을 등록. row.bunji1/bunji2가 있으면 그 건물로 등록(여러 건물 지원).
    pay_kinds: {index_str: 'rent'|'etc'}
    """
    split_map = split_map or {}
    pay_kinds = pay_kinds or {}
    saved = 0
    applied = []
    for i, row in enumerate(rows):
        if row.get("status") == "duplicate":
            continue
        if i not in selected_idx:
            continue
        
        # row에 bunji1/bunji2가 있으면 그 건물로 사용 (여러 건물인 경우)
        # 없으면 함수 인자의 기본값 사용 (하위호환성)
        row_bunji1 = row.get("bunji1") or bunji1
        row_bunji2 = row.get("bunji2") or bunji2
        pay_kind = (pay_kinds.get(str(i)) or row.get("pay_kind") or "rent").strip()
        if pay_kind not in ("rent", "etc"):
            pay_kind = "rent"
        
        raw = manual_overrides.get(str(i)) or []
        if isinstance(raw, str):
            raw = [raw] if raw else []
        if any(v == "ALL" for v in raw):
            parts = dict(split_map.get(str(i)) or {})
            room_keys = [
                o.get("value")
                for o in (row.get("room_options") or [])
                if "|" in (o.get("value") or "")
            ]
            filled = {k: int(v) for k, v in parts.items() if int(v) > 0}
            if len(filled) == 1 and len(room_keys) >= 2:
                rest = int(row["amount"]) - next(iter(filled.values()))
                empty = [k for k in room_keys if k not in filled]
                if rest > 0 and len(empty) == 1:
                    parts[empty[0]] = rest
            if sum(int(v) for v in parts.values()) != int(row["amount"]):
                parts = _split_parts_from_options(
                    row.get("room_options"), row.get("amount")
                )
            if sum(int(v) for v in parts.values()) != int(row["amount"]):
                continue
            n = 0
            for key, amt in parts.items():
                if "|" not in key:
                    continue
                hosu, ipju_seq = key.split("|", 1)
                if _insert_sukum(
                    row_bunji1, row_bunji2, hosu, ipju_seq,
                    row["date"], amt, row.get("name"), pay_kind=pay_kind,
                ):
                    n += 1
                    saved += 1
            if n:
                applied.append(i)
            continue
        picks = []
        seen = set()
        for v in raw:
            if v and "|" in v and v not in seen:
                seen.add(v)
                picks.append(v)
        if len(picks) == 1:
            hosu, ipju_seq = picks[0].split("|", 1)
        else:
            hosu, ipju_seq = row.get("hosu"), row.get("ipju_seq")
        if _insert_sukum(
            row_bunji1, row_bunji2, hosu, ipju_seq,
            row["date"], row["amount"], row.get("name"), pay_kind=pay_kind,
        ):
            saved += 1
            applied.append(i)
    return saved, applied


@app.route("/payments/import", methods=["GET", "POST"])
@login_required
@require_write_access
def payments_import():
    """PRG(Post-Redirect-Get) 패턴: 업로드·매칭·반영은 전부 POST 후 GET으로
    리다이렉트한다 — 새로고침해도 파일이 재업로드/재반영되지 않도록 하기 위함.
    매칭 결과(입금 목록)는 쿠키 세션에 담기엔 커서 서버 임시 파일(_import_tmp/)에
    token으로 저장해두고 GET에서 그 token으로 불러와 다시 그린다."""
    buildings, _rooms = _buildings_and_rooms()

    if request.method == "POST" and request.form.get("action") == "apply":
        token = request.form.get("token") or ""
        state = _load_state(token)
        if not state:
            flash("매칭 결과가 만료됐습니다. 파일을 다시 올려주세요.", "err")
            return redirect(url_for("payments_import"))
        
        # 하위호환성: 구 형식(bunji1/bunji2)을 building_list로 변환
        building_list = state.get("building_list") or []
        if not building_list and state.get("bunji1") and state.get("bunji2"):
            building_list = [(state["bunji1"], state["bunji2"])]
        
        if not building_list:
            flash("건물 정보가 없습니다.", "err")
            return redirect(url_for("payments_import"))
        
        # 첫 번째 건물을 "대표" 건물로 사용 (UI 표시/상태저장)
        primary_bunji1, primary_bunji2 = building_list[0]
        # 체크된 행만 반영. 행의 "반영"도 체크가 켜져 있어야 함.
        checked_idx = set()
        for v in request.form.getlist("apply_idx"):
            try:
                checked_idx.add(int(v))
            except ValueError:
                pass
        selected_idx = set()
        one = (request.form.get("apply_one") or "").strip()
        if one != "":
            try:
                idx = int(one)
                if idx in checked_idx:
                    selected_idx.add(idx)
            except ValueError:
                pass
        else:
            selected_idx = set(checked_idx)
        manual_overrides = {}
        for k in request.form:
            if not k.startswith("manual_"):
                continue
            vals = [v for v in request.form.getlist(k) if v]
            if vals:
                manual_overrides[k[len("manual_"):]] = vals
        split_map = {}
        for k in request.form:
            if not k.startswith("split_"):
                continue
            rest = k[len("split_"):]
            idx, sep, room = rest.partition("_")
            if not sep or "|" not in room:
                continue
            amt = _parse_amount(request.form.get(k))
            if amt > 0:
                split_map.setdefault(idx, {})[room] = amt
        manual_picks = dict(state.get("manual_picks") or {})
        rows = _match_deposits(
            state["deposits"], building_list, state.get("account_no") or "",
            manual_picks=manual_picks,
        )
        pay_kinds = {}
        for k in request.form:
            if not k.startswith("pay_kind_"):
                continue
            pay_kinds[k[len("pay_kind_"):]] = (request.form.get(k) or "rent").strip()
        # 폼에 있는 수동 호실 선택을 세션에 남겨, 반영 후에도 풀리지 않게 함
        for k, vals in manual_overrides.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(rows):
                continue
            dep_id = rows[idx].get("dep_id") or ""
            if not dep_id:
                continue
            pick = (vals[0] if vals else "") or ""
            if pick and "|" in pick and pick != "ALL":
                hosu, ipju_seq = pick.split("|", 1)
                manual_picks[dep_id] = {
                    "room": pick,
                    "bunji1": rows[idx].get("bunji1") or primary_bunji1,
                    "bunji2": rows[idx].get("bunji2") or primary_bunji2,
                    "hosu": hosu,
                    "ipju_seq": ipju_seq,
                }
            elif dep_id in manual_picks and not pick:
                manual_picks.pop(dep_id, None)
        state["manual_picks"] = manual_picks
        if not selected_idx:
            _write_state(token, state)
            flash("체크된 입금이 없습니다. 반영할 행을 선택한 뒤 다시 눌러주세요.", "err")
            return redirect(url_for("payments_import", token=token))
        _saved, applied = _apply_selected(
            rows, primary_bunji1, primary_bunji2, selected_idx, manual_overrides, split_map,
            pay_kinds=pay_kinds,
        )
        if applied:
            # 반영된 건만 dep_id로 제거 (다른 매칭 건은 유지)
            drop_ids = {rows[i].get("dep_id") for i in applied if rows[i].get("dep_id")}
            for i in applied:
                row = rows[i]
                did = row.get("dep_id") or ""
                if did:
                    manual_picks.pop(did, None)
                # 수동 호실(또는 확인필요에서 확정한 호실)이면 자동매칭 규칙 저장
                raw = manual_overrides.get(str(i)) or []
                if isinstance(raw, str):
                    raw = [raw] if raw else []
                pick = next((v for v in raw if v and "|" in v and v != "ALL"), "")
                if pick:
                    hosu, ipju_seq = pick.split("|", 1)
                else:
                    hosu, ipju_seq = row.get("hosu") or "", row.get("ipju_seq") or ""
                if hosu and (pick or row.get("needs_pick") or row.get("amount_flag") or row.get("status") == "unmatched"):
                    add_match_rule(
                        row.get("name") or "",
                        row.get("bunji1") or primary_bunji1,
                        row.get("bunji2") or primary_bunji2,
                        hosu,
                        ipju_seq,
                        row.get("account_no") or "",
                    )

            state["deposits"] = [
                d for d in state["deposits"]
                if (d.get("dep_id") or "") not in drop_ids
            ]
            state["manual_picks"] = manual_picks
            _write_state(token, state)
            flash(f"{len(applied)}건 반영했습니다.", "ok")
        elif one != "":
            return redirect(url_for("payments_import", token=token, err=one))
        else:
            flash("반영된 입금이 없습니다. 호실·구분을 확인해주세요.", "err")
        return redirect(url_for("payments_import", token=token))

    if request.method == "POST" and request.form.get("action") == "parse":
        bunji1 = _pad_bunji(request.form.get("bunji1"))
        bunji2 = _pad_bunji(request.form.get("bunji2"))
        files = request.files.getlist("bank_file")
        if not files or all(not (f and f.filename) for f in files):
            old_token = (request.form.get("token") or "").strip()
            if old_token and _load_state(old_token):
                return redirect(
                    url_for(
                        "payments_import",
                        token=old_token,
                        bunji1=bunji1 or None,
                        bunji2=bunji2 or None,
                    )
                )
            return redirect(url_for("payments_import"))
        
        # 여러 파일 처리: 각 파일별로 load_bank_deposits 호출해서 deposits를 하나로 합침
        # 개선 3: 파일 하나 오류 시 나머지는 계속 진행, 경고 메시지만 표시
        all_deposits = []
        building_list_set = set()  # 개선 2: 중복 제거용 set
        file_info = []  # [(filename, account_no), ...]
        file_errors = []  # 부분 오류 기록
        
        # 개선 1, 2: 중복 감지용 (date, amount, name, account_no 조합)
        # → 다른 계좌의 같은 입금도 구분, 제외된 건수 추적
        seen_deposits = set()
        excluded_count = 0  # 파일 간 중복으로 제외된 건수
        
        skipped_non_excel = 0
        for f in files:
            if not (f and f.filename):
                continue
            display_name = _upload_basename(f.filename)
            file_ext = os.path.splitext(display_name)[1].lower()
            # 폴더/다중 선택에 섞인 비엑셀은 오류 대신 건너뜀(요약만 표시)
            if file_ext not in ALLOWED_EXTENSIONS:
                skipped_non_excel += 1
                continue
            # 보안 검증: 파일별 크기/확장자/매직넘버 확인
            is_valid, error_msg = validate_file_upload(f)
            if not is_valid:
                try:
                    from logs_handler import log_security_event
                    log_security_event(
                        'file_upload_invalid',
                        user_id=session.get('sabun'),
                        ip_address=request.remote_addr,
                        details=f"Invalid file upload attempt: {error_msg}",
                    )
                except (ImportError, Exception):
                    pass
                file_errors.append(f"⚠ {display_name}: {error_msg}")
                continue
            try:
                deposits, account_no = load_bank_deposits(display_name, f.read())
            except Exception as e:
                # 개선 3: 이 파일만 건너뛰고 계속 진행
                try:
                    from logs_handler import log_security_event
                    log_security_event(
                        'file_processing_error',
                        user_id=session.get('sabun'),
                        ip_address=request.remote_addr,
                        details=f"Error reading file {f.filename}: {str(e)[:100]}",
                    )
                except (ImportError, Exception):
                    pass
                file_errors.append(f"⚠ {f.filename}: {e}")
                continue
            if not deposits:
                # 개선 3: 입금 내역이 없는 파일도 건너뛰기
                file_errors.append(f"⚠ {f.filename}: 입금 내역 없음")
                continue
            
            # 각 deposit에 source_file, account_no 추가
            for d in deposits:
                d["source_file"] = display_name
                d["account_no"] = account_no or ""
                
                # 개선 1+2: 같은 (date, amount, name, account_no) 조합이 이미 있으면 제외
                # → 다른 계좌의 같은 입금은 구분 / 같은 계좌 중복은 추적
                dep_key = (d["date"], d["amount"], d["name"], d["account_no"])
                if dep_key in seen_deposits:
                    excluded_count += 1
                    continue
                seen_deposits.add(dep_key)
                all_deposits.append(d)
            
            file_info.append((display_name, account_no or ""))
            
            # 개선 2: 각 파일의 계좌번호로 건물 감지, 중복 제거
            if not (bunji1 and bunji2):
                detected = detect_building(account_no)
                for b in detected:
                    building_list_set.add(b)
        
        for i, d in enumerate(all_deposits):
            d["dep_id"] = f"{d.get('date')}|{d.get('amount')}|{d.get('name')}|{d.get('account_no')}|{d.get('source_file')}|{i}"

        # building_list_set → list 변환
        building_list = sorted(list(building_list_set))
        
        # 개선 3: 부분 오류가 있으면 경고 메시지 표시
        if skipped_non_excel:
            flash(
                f"💡 엑셀이 아닌 파일 {skipped_non_excel}개는 건너뛰었습니다. (.xls/.xlsx/.xlsm만 처리)",
                "info",
            )
        if file_errors:
            for err_msg in file_errors:
                flash(err_msg, "warn")
        
        # 개선 2: 파일 간 중복으로 제외된 건수 표시
        if excluded_count > 0:
            flash(f"💡 같은 계좌에서 날짜·금액·이름이 겹친 중복 {excluded_count}건을 제외했습니다. (계좌가 다르면 각각 유지됩니다)", "info")
        
        if not all_deposits:
            flash("입금 내역을 찾지 못했습니다.", "err")
            return redirect(url_for("payments_import"))
        
        auto_detected = False
        if not (bunji1 and bunji2):
            auto_detected = bool(building_list)
            if not auto_detected:
                flash("건물을 자동으로 찾지 못했습니다. 직접 선택하세요.", "err")
            elif len(building_list) > 1:
                # 한 계좌가 여러 건물에 묶이거나, 여러 파일/계좌가 합쳐진 경우
                flash(
                    f"💡 건물 {len(building_list)}곳을 자동 감지해 함께 매칭합니다. "
                    "한 건물만 보려면 위에서 주소를 골라 주세요.",
                    "info",
                )
        else:
            building_list = [(bunji1, bunji2)]
        
        token = _save_state(
            building_list,
            all_deposits,
            auto_detected,
            filename="; ".join(f[0] for f in file_info),
            account_no="; ".join(f[1] for f in file_info),
        )
        return redirect(url_for("payments_import", token=token))

    # GET: 최초 진입(빈 폼) 또는 방금 매칭한 결과 보기(?token=...),
    # 건물을 다시 골랐으면 ?token=...&bunji1=...&bunji2=...
    token = request.args.get("token") or ""
    state = _load_state(token) if token else None
    rows = None
    bunji1 = bunji2 = ""
    auto_detected = False
    uploaded_name = ""
    building_list = []

    if state:
        # 하위호환성: 구 형식(bunji1/bunji2)을 building_list로 변환
        building_list = state.get("building_list") or []
        if not building_list and state.get("bunji1") and state.get("bunji2"):
            building_list = [(state["bunji1"], state["bunji2"])]
        
        # 사용자가 건물을 다시 선택했나?
        req_b1 = _pad_bunji(request.args.get("bunji1"))
        req_b2 = _pad_bunji(request.args.get("bunji2"))
        if req_b1 and req_b2:
            # Intentional: explicit address selection narrows matching to that one
            # building (overrides multi-building auto-detect list).
            # 사용자가 주소를 직접 고르면 그 건물만 매칭한다.
            state = _update_state_building(token, req_b1, req_b2) or state
            building_list = [(req_b1, req_b2)]
        
        # 첫 번째 건물을 "대표"로 사용 (UI 표시용)
        if building_list:
            bunji1, bunji2 = building_list[0]
        
        auto_detected = bool(state.get("auto_detected"))
        uploaded_name = (state.get("filename") or "").strip()
        # 구 토큰 호환: dep_id 없으면 채움
        for i, d in enumerate(state.get("deposits") or []):
            if not d.get("dep_id"):
                d["dep_id"] = (
                    f"{d.get('date')}|{d.get('amount')}|{d.get('name')}|"
                    f"{d.get('account_no')}|{d.get('source_file')}|{i}"
                )
        rows = (
            _match_deposits(
                state["deposits"], building_list, state.get("account_no") or "",
                manual_picks=state.get("manual_picks") or {},
            )
            if building_list
            else []
        )

    building_label = _building_label(bunji1, bunji2) if bunji1 and bunji2 else ""
    # 매칭 결과가 있고 건물이 정해졌으면 전체 드롭다운 대신 한 줄만 표시
    labels = []
    for b1, b2 in (building_list or []):
        lab = _building_label(b1, b2)
        if lab and lab not in labels:
            labels.append(lab)
    if len(labels) == 1:
        building_display = labels[0]
    elif len(labels) > 1:
        building_display = f"{labels[0]} 외 {len(labels) - 1}건물"
    else:
        building_display = building_label
    address_locked = bool(rows is not None and building_list and building_display)
    return render_template(
        "payments_import.html",
        buildings=buildings,
        bunji1=bunji1,
        bunji2=bunji2,
        building_label=building_label,
        building_display=building_display,
        auto_detected=auto_detected,
        address_locked=address_locked,
        rows=rows,
        token=token,
        uploaded_name=uploaded_name,
        apply_err=(request.args.get("err") or "").strip(),
    )


@app.route("/payments/import/exclude", methods=["GET", "POST"])
@login_required
@require_write_access
def payments_import_exclude():
    """제외 목록 관리 — 글자(적요) + 선택 주소·계좌 범위.
    매칭 결과 화면(미매칭 행)에서 바로 제외할 때는 token을 같이 보내서
    등록 후 그 매칭 결과로 돌아가게 한다."""
    return_token = request.form.get("token") or ""

    def _back():
        if return_token:
            return redirect(url_for("payments_import", token=return_token))
        return redirect(url_for("payments_import_exclude"))

    if request.method == "POST" and request.form.get("action") == "exclude_add":
        add_exclude_keyword(
            request.form.get("keyword"),
            request.form.get("bunji1"),
            request.form.get("bunji2"),
            request.form.get("acct_no"),
        )
        return _back()
    if request.method == "POST" and request.form.get("action") == "exclude_edit":
        update_exclude_keyword(
            request.form.get("keyword_id"),
            request.form.get("keyword"),
            request.form.get("bunji1"),
            request.form.get("bunji2"),
            request.form.get("acct_no"),
        )
        return _back()
    if request.method == "POST" and request.form.get("action") == "exclude_del":
        try:
            delete_exclude_keyword(int(request.form.get("keyword_id") or 0))
        except ValueError:
            pass
        return _back()

    buildings, _rooms = _buildings_and_rooms()
    rows = list_exclude_keywords()
    q = (request.args.get("q") or "").strip()
    if q:
        ql = q.lower()
        def _blob(r):
            parts = [r.get("keyword") or "", r.get("juso") or "", r.get("acct_no") or "전체"]
            if r.get("bunji1"):
                parts.append(_fmt_bunji_pair(r.get("bunji1"), r.get("bunji2")))
            else:
                parts.append("전체")
            return " ".join(parts).lower()
        rows = [r for r in rows if ql in _blob(r)]
    pager = _make_pager(len(rows), _parse_page())
    page_rows = rows[pager["offset"] : pager["offset"] + pager["per_page"]]
    edit = None
    try:
        edit_id = int(request.args.get("edit_id") or 0)
    except ValueError:
        edit_id = 0
    if edit_id:
        edit = next((r for r in list_exclude_keywords() if int(r.get("id") or 0) == edit_id), None)
    return render_template(
        "payments_import_exclude.html",
        exclude_keywords=page_rows,
        buildings=buildings,
        edit=edit,
        pager=pager,
        q=q,
    )


@app.route("/payments/import/matches", methods=["GET", "POST"])
@login_required
@require_write_access
def payments_import_matches():
    """수동매칭 규칙 관리 — 적요 글자 → 건물·호실. 다음 자동반영에 사용."""
    return_token = request.form.get("token") or ""

    def _back():
        if return_token:
            return redirect(url_for("payments_import", token=return_token))
        return redirect(url_for("payments_import_matches"))

    if request.method == "POST" and request.form.get("action") == "match_add":
        add_match_rule(
            request.form.get("keyword"),
            request.form.get("bunji1"),
            request.form.get("bunji2"),
            request.form.get("hosu"),
            request.form.get("ipju_seq"),
            request.form.get("acct_no"),
        )
        return _back()
    if request.method == "POST" and request.form.get("action") == "match_edit":
        update_match_rule(
            request.form.get("rule_id"),
            request.form.get("keyword"),
            request.form.get("bunji1"),
            request.form.get("bunji2"),
            request.form.get("hosu"),
            request.form.get("ipju_seq"),
            request.form.get("acct_no"),
        )
        return _back()
    if request.method == "POST" and request.form.get("action") == "match_del":
        try:
            delete_match_rule(int(request.form.get("rule_id") or 0))
        except ValueError:
            pass
        return _back()

    buildings, rooms = _buildings_and_rooms()
    rows = list_match_rules()
    q = (request.args.get("q") or "").strip()
    if q:
        ql = q.lower()
        def _blob(r):
            return " ".join([
                r.get("keyword") or "",
                r.get("juso") or "",
                r.get("hosu") or "",
                r.get("acct_no") or "전체",
                _fmt_bunji_pair(r.get("bunji1"), r.get("bunji2")) if r.get("bunji1") else "",
            ]).lower()
        rows = [r for r in rows if ql in _blob(r)]
    pager = _make_pager(len(rows), _parse_page())
    page_rows = rows[pager["offset"] : pager["offset"] + pager["per_page"]]
    edit = None
    try:
        edit_id = int(request.args.get("edit_id") or 0)
    except ValueError:
        edit_id = 0
    if edit_id:
        edit = next((r for r in list_match_rules() if int(r.get("id") or 0) == edit_id), None)
    return render_template(
        "payments_import_matches.html",
        match_rules=page_rows,
        buildings=buildings,
        edit=edit,
        pager=pager,
        q=q,
    )
