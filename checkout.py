"""퇴실 정산 관리 화면.

퇴실 정산 작성/저장, 퇴실(예정)자 조회, 계약 해지 인쇄
라우트와 그 전용 도우미 함수들을 모아둔 모듈입니다.
"""
import math
from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import flash, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    CURRENT_TENANT_SQL as _CURRENT_TENANT_SQL,
    building_label as _building_label,
    calc_contract_period_charge,
    make_pager as _make_pager,
    paginate as _paginate,
    fmt_bunji,
    fmt_date,
    fmt_ipju_short as _fmt_ipju_short,
    mask_jumin,
    login_required,
    money,
    pad_bunji as _pad_bunji,
    require_write_access,
    tenant_is_past_out as _tenant_is_past_out,
    to_int_amt as _to_int_amt,
)


def _ceil_100(v):
    """10원 이하 올림. 백원 단위만 남김."""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    return int(math.ceil(n / 100.0) * 100)


def _to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _checkout_tenant_adjustment_total(b1, b2, hosu, seq, ipju_dt, out_dt):
    """거주기간 중 월정산에서 확정한 월세·관리비 감면/면제 합계."""
    ipju_d = _to_date(ipju_dt)
    out_d = _to_date(out_dt)
    if not ipju_d or not out_d or out_d < ipju_d:
        return 0
    start_month = ipju_d.replace(day=1).isoformat()
    end_month = out_d.replace(day=1).isoformat()
    try:
        row = db.query_one(
            """SELECT COALESCE(SUM(COALESCE(adj_amt,0)),0) AS amt
               FROM jungsan_adjustment
               WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                 AND adj_month >= %s AND adj_month <= %s
                 AND adj_kind IN (
                   'RENT_DISCOUNT','RENT_WAIVE','MANAGE_DISCOUNT','MANAGE_WAIVE'
                 )
                 AND del_yn='N'""",
            (b1, b2, hosu, seq, start_month, end_month),
        )
    except Exception:
        return 0
    return max(0, _to_int_amt((row or {}).get("amt")))


def _add_months(d, months):
    d = _to_date(d)
    if not d:
        return None
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def _cycle_bounds(ipju_dt, pay_dt, napbu="B"):
    """입주일 기념일 주기. 선납(A)은 입금한 달의 기념일, 후납은 입금일이 속한 주기."""
    ipju = _to_date(ipju_dt)
    pay = _to_date(pay_dt)
    if not ipju or not pay:
        return None, None
    if str(napbu or "B").strip().upper() == "A":
        # 입금한 달의 기념일 주기. 3/16 입금 → 03-15~04-14 (다음 달로 건너뛰지 않음)
        n = (pay.year - ipju.year) * 12 + (pay.month - ipju.month)
        if n < 0:
            n = 0
        start = _add_months(ipju, n)
    else:
        n = (pay.year - ipju.year) * 12 + (pay.month - ipju.month)
        if pay.day < ipju.day:
            n -= 1
        n = max(0, n)
        while True:
            nxt = _add_months(ipju, n + 1)
            if nxt is None or pay < nxt:
                break
            n += 1
        start = _add_months(ipju, n)
    # Keep both boundaries anchored to the original move-in date. Adding a
    # month to a clamped boundary (e.g. 2/28 -> 3/28 for a 1/31 move-in)
    # would make the payment period drift over time.
    end = _add_months(ipju, n + 1) if start else None
    if not start or not end:
        return None, None
    return start, end - timedelta(days=1)


def _months_between(a, b):
    if not a or not b:
        return 0
    return (b.year - a.year) * 12 + (b.month - a.month)


def _cycle_label(start, end):
    if not start or not end:
        return ""
    return f"{start.strftime('%y-%m-%d')}~{end.strftime('%m-%d')}"


def _pay_kind(char):
    c = str(char or "").strip().zfill(2)
    return "임대" if c == "01" else "보증"


def _pay_label(char):
    c = str(char or "").strip().zfill(2)
    return {"01": "월세", "02": "보증금", "03": "예치금"}.get(c, "기타")


def _period_mm_dd(ipju_dt, out_dt):
    """입주~퇴실 개월·일 (시작일·퇴실일 양끝 포함)."""
    start = _to_date(ipju_dt)
    end = _to_date(out_dt)
    if not start or not end or end < start:
        return 0, 0
    # The checkout date is billable too.  Use the day after checkout as the
    # exclusive boundary so full anniversary cycles and the residual day count
    # stay aligned with calc_contract_period_charge (e.g. 8/17~8/31 = 15일).
    end_exclusive = end + timedelta(days=1)
    # Every anniversary is anchored to the original move-in date.  This makes
    # 1/31 -> 2/28 one complete cycle plus the included 2/28 day.
    months = max(
        0, (end_exclusive.year - start.year) * 12 + (end_exclusive.month - start.month)
    )
    while months > 0 and _add_months(start, months) > end_exclusive:
        months -= 1
    while _add_months(start, months + 1) <= end_exclusive:
        months += 1
    cycle_start = _add_months(start, months)
    days = (end_exclusive - cycle_start).days if cycle_start else 0
    return months, max(0, days)


def _checkout_build(bunji1, bunji2, hosu, ipju_seq, out_dt, extra=None):
    """
    퇴실 정산 미리보기 데이터.
    extra: 화면에서 입력한 공과·수리 등 {elec, water, restore, gas, etc, suri}
    """
    extra = extra or {}
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    hosu = (hosu or "").strip().upper()
    seq = (ipju_seq or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    out_d = _to_date(out_dt) or date.today()

    if not (b1 and b2 and hosu):
        return {"error": "주소·호수를 입력하세요."}

    def _missing_addr_or_room():
        bld = db.query_one(
            "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
            (b1, b2),
        )
        if not bld:
            return "등록되지 않은 주소입니다. 주소를 확인하세요."
        room = db.query_one(
            """
            SELECT hosu FROM bd03_m
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
            """,
            (b1, b2, hosu),
        )
        if not room:
            return "등록되지 않은 호수입니다. 호수를 확인하세요."
        return None

    tenant = None
    # 순번 지정(퇴실자 목록→정산 링크): 해당 이력 1건
    if seq:
        tenant = db.query_one(
            """
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (b1, b2, hosu, seq),
        )
        if not tenant:
            miss = _missing_addr_or_room()
            if miss:
                return {"error": miss}
            return {"error": f"순번 {seq} 입주 이력이 없습니다."}
    else:
        # 일반 조회: 반드시 현재 입주자 (과거 퇴실자로 자동 폴백 금지)
        tenant = db.query_one(
            f"""
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
              AND {_CURRENT_TENANT_SQL.replace('d.', '')}
            ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
            LIMIT 1
            """,
            (b1, b2, hosu),
        )
        if not tenant:
            miss = _missing_addr_or_room()
            if miss:
                return {"error": miss}
            past_rows = db.query(
                """
                SELECT ipju_nm, ipju_seq, ipju_dt, out_dt, rent_amt, manage_amt FROM bd03_det
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                  AND out_dt IS NOT NULL AND out_dt >= '1000-01-01'
                ORDER BY out_dt DESC, CAST(ipju_seq AS UNSIGNED) DESC
                """,
                (b1, b2, hosu),
            )
            if past_rows:
                return {
                    "select_required": True,
                    "past_tenants": past_rows,
                    "addr": f"{fmt_bunji(b1)}-{fmt_bunji(b2)}",
                    "hosu": hosu,
                }
            else:
                return {"error": "해당 호수의 입주 이력이 없습니다."}

    seq = str(tenant.get("ipju_seq") or "").zfill(2)
    ipju_d = _to_date(tenant.get("ipju_dt"))
    bojung = _to_int_amt(tenant.get("bojung_amt"))
    yechi = _to_int_amt(tenant.get("yechi_amt"))
    rent = _to_int_amt(tenant.get("rent_amt"))
    manage = _to_int_amt(tenant.get("manage_amt"))
    napbu = (tenant.get("napbu_gb") or "B").strip().upper()
    nm = (tenant.get("ipju_nm") or "").strip()
    tel = (
        (tenant.get("ipju_tel1") or tenant.get("ipju_tel3") or tenant.get("ipju_tel2") or "")
        .strip()
    )
    jumin = (tenant.get("ipju_jumin_no") or "").strip()

    mm, dd = _period_mm_dd(ipju_d, out_d)
    monthly = rent + manage
    # 미수 기준 청구총액: (임+관)×개월 + 일할(임+관) + 수리
    suri = _to_int_amt(extra.get("suri"))
    if suri <= 0:
        suri_row = db.query_one(
            """
            SELECT COALESCE(SUM(COALESCE(ipjuja_budam,0)),0) AS a
            FROM bd05_suri
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (b1, b2, hosu, seq),
        )
        suri = _to_int_amt((suri_row or {}).get("a"))

    # XP/기존 장부 기준: 전체 계약월은 월액 그대로, 마지막 잔여일만 30일로 일할한다.
    # 따라서 2월·31일 달도 완전한 계약 주기는 1개월분이고, 15일은 월액의 절반이다.
    day_amt = 0
    if dd and monthly:
        day_amt = min(monthly, _ceil_100(monthly * dd / 30.0))
    # ③거주기간(총액) = (임+관)×개월 + 일할. 보증/예치·수리는 넣지 않음.
    stay_amt_gross = calc_contract_period_charge(
        b1, b2, hosu, seq, ipju_d, out_d, rent, manage
    )
    tenant_adjustment = _checkout_tenant_adjustment_total(
        b1, b2, hosu, seq, ipju_d, out_d
    )
    stay_amt = max(0, stay_amt_gross - tenant_adjustment)
    # 보증/예치는 환불성 수금이므로 임대료 미수 계산에서 제외한다.
    base = max(0, stay_amt + suri)

    pays = db.query(
        """
        SELECT sukum_dt, sukum_seq, sukum_char, sukum_gb,
               su_sil_amt, su_dache_amt
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
        ORDER BY sukum_dt, sukum_seq
        """,
        (b1, b2, hosu, seq),
    )
    pay_list = []
    sukum_tot = 0
    for p in pays or []:
        sil = _to_int_amt(p.get("su_sil_amt"))
        dac = _to_int_amt(p.get("su_dache_amt"))
        tot = sil + dac
        kind = _pay_kind(p.get("sukum_char"))
        # 수금총액은 임대 전표의 실입만. 보증/예치와 대체는 합산하지 않음.
        if kind == "임대":
            sukum_tot += sil
        gb = str(p.get("sukum_gb") or "").strip()
        # 납부현황은 실입만. 대체전표(종류 02 또는 실입 0·대체>0)는 안 그림.
        if gb == "02" or (dac > 0 and sil == 0):
            continue
        cyc_s, cyc_e = _cycle_bounds(ipju_d, p.get("sukum_dt"), napbu)
        period = _cycle_label(cyc_s, cyc_e) if kind == "임대" else ""
        dt_s = _fmt_ipju_short(p.get("sukum_dt"))
        amt_s = money(tot)
        pay_list.append(
            {
                "dt": dt_s,
                "dt_full": fmt_date(p.get("sukum_dt")),
                "amt": tot,
                "amt_disp": amt_s,
                "sil": sil,
                "dache": dac,
                "char": p.get("sukum_char") or "",
                "kind": kind,
                "label": _pay_label(p.get("sukum_char")),
                "period": period,
                "cycle_start": cyc_s.isoformat() if cyc_s else "",
                "cycle_end": cyc_e.isoformat() if cyc_e else "",
            }
        )

    h_amt = bojung + yechi  # 환불성수금
    misu = max(0, base - sukum_tot)
    elec = _to_int_amt(extra.get("elec"))
    water = _to_int_amt(extra.get("water"))
    restore = _to_int_amt(extra.get("restore"))
    gas = _to_int_amt(extra.get("gas"))
    gita = _to_int_amt(extra.get("etc"))
    util_tot = elec + water + restore + gas + gita
    jungsan = h_amt - misu - util_tot

    bldg = db.query_one(
        "SELECT juso FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (b1, b2),
    )
    juso = ((bldg or {}).get("juso") or "").strip()
    addr = f"{fmt_bunji(b1)}-{fmt_bunji(b2)}"
    nap_label = "선" if napbu == "A" else "후"
    rent_man_label = f"{int(round(rent/10000)) if rent else 0}/{int(round(manage/10000)) if manage else 0} {nap_label}"
    is_current = not _tenant_is_past_out(tenant.get("out_dt"))

    return {
        "error": None,
        "bunji1": b1,
        "bunji2": b2,
        "hosu": hosu,
        "ipju_seq": seq,
        "out_dt": out_d.isoformat(),
        "gijun_dt": out_d.isoformat(),
        "ipju_dt": fmt_date(ipju_d),
        "ipju_dt_raw": ipju_d.isoformat() if ipju_d else "",
        "tenant_out_dt": fmt_date(tenant.get("out_dt")) if not is_current else "",
        "is_current": is_current,
        "ipju_nm": nm,
        "tel": tel,
        "jumin": jumin,
        "bojung_amt": bojung,
        "yechi_amt": yechi,
        "rent_amt": rent,
        "manage_amt": manage,
        "napbu_gb": napbu,
        "nap_label": nap_label,
        "rent_manage_label": rent_man_label,
        "addr": addr,
        "juso": juso,
        "mm": mm,
        "dd": dd,
        "period_label": f"{mm}개월 {dd}일",
        "day_amt": day_amt,
        "suri_amt": suri,
        "ipkum_gijun_gross": stay_amt_gross,
        "tenant_adjustment_amt": tenant_adjustment,
        "ipkum_gijun": stay_amt,
        "sukum_tot": sukum_tot,
        "h_amt": h_amt,
        "misu_amt": misu,
        "elec_amt": elec,
        "sudo_amt": water,
        "sisul_amt": restore,
        "gas_amt": gas,
        "gita_amt": gita,
        "util_tot": util_tot,
        "jungsan_amt": jungsan,
        "payments": pay_list,
        "building_label": juso or addr,
    }


def _paginate_pay_items(payments, has_day_amt):
    """계약 해지 인쇄: 납부현황을 A4 한 페이지(3칸)씩 잘라서 반환.

    한 페이지에 몇 줄이 들어가는지는 인쇄 CSS(7pt, 줄간격 1.22, 칸 헤더 포함)로
    실제 렌더링해 픽셀 단위로 측정한 값을 바탕으로 계산한다(여유를 두어 넘치지
    않는 쪽으로 보수적으로 잡음). 반환값: [[col1, col2, col3], [col1, col2, col3], ...]
    페이지마다 3칸으로 고르게 나뉘고, 마지막 항목이 일할계산 행이면
    {"is_day_amt": True} 로 표시해 템플릿에서 따로 렌더링한다.
    """
    items = list(payments)
    if has_day_amt:
        items.append({"is_day_amt": True})
    if not items:
        return []

    mm_to_px = 96 / 25.4
    page_usable_px = 277 * mm_to_px  # @page margin 10mm+10mm 뺀 인쇄 가능 높이
    head_px = 230  # 반복 헤더(제목+해지상황+①②), 주소 2줄 대비 여유 포함
    pay_header_row_px = 16.5  # 칸별 "년도/기간/입금일/입금액" 제목행
    data_row_px = 12.0  # 데이터 행 평균(년도 구분선·일할행 굵게 포함 여유)
    safety_rows = 1

    rows_per_col = max(
        1,
        int((page_usable_px - head_px - pay_header_row_px) / data_row_px) - safety_rows,
    )
    per_page = rows_per_col * 3

    pages = []
    for start in range(0, len(items), per_page):
        page_items = items[start:start + per_page]
        col_n = 3
        row_n = (len(page_items) + col_n - 1) // col_n
        cols = [page_items[i * row_n:(i + 1) * row_n] for i in range(col_n)]
        # 짧은 칸은 빈 줄로 채워 세 칸 높이를 맞춤 → 칸 구분선을 진짜 border로
        # 그려도(배경 그라디언트 안 씀) 항상 끝까지 이어짐
        for col in cols:
            col.extend({"is_filler": True} for _ in range(row_n - len(col)))
        pages.append(cols)
    return pages


def _checkout_to_print(data):
    """계약 해지 인쇄 템플릿용 dict."""
    util_tot = _to_int_amt(data.get("util_tot"))
    h = _to_int_amt(data.get("h_amt"))
    misu = _to_int_amt(data.get("misu_amt"))
    jungsan = _to_int_amt(data.get("jungsan_amt"))
    doc = {
        "name": data.get("ipju_nm") or "",
        "addr": data.get("addr") or "",
        "hosu": data.get("hosu") or "",
        "key_note": "",
        "broker_note": "",
        "move_after": "",
        "tel": data.get("tel") or "",
        "bojung_disp": money(
            data.get("bojung_amt") if _to_int_amt(data.get("bojung_amt"))
            else data.get("yechi_amt")
        ),
        "rent_manage_label": data.get("rent_manage_label") or "",
        "ipju_dt": data.get("ipju_dt") or "",
        "out_dt": data.get("out_dt") or "",
        "etc_note": "",
    }
    fac = {k: "" for k in (
        "door", "kitchen", "wallpaper", "floor", "light", "window",
        "bath", "boiler", "key2", "contract", "clean", "convert_amt",
    )}
    rent = {
        "period": data.get("period_label") or "",
        "dd": data.get("dd") or 0,
        "day_amt": money(data.get("day_amt")) if _to_int_amt(data.get("day_amt")) else "",
        "repair_amt": money(data.get("suri_amt")) if _to_int_amt(data.get("suri_amt")) else "",
        "base_total": money(data.get("ipkum_gijun")),
        "paid_total": money(data.get("sukum_tot")),
        "refundable": money(data.get("h_amt")),
        "unpaid": money(data.get("misu_amt")),
    }
    pay_src = list(data.get("payments") or [])
    pay_src.sort(key=lambda p: (0 if p.get("kind") == "보증" else 1))
    monthly = _to_int_amt(data.get("rent_amt")) + _to_int_amt(data.get("manage_amt"))
    payments = []
    month_left = monthly
    for p in pay_src:
        amt_n = _to_int_amt(p.get("amt"))
        amt_s = p.get("amt_disp") or money(p.get("amt"))
        kind = p.get("kind") or ""
        item = {
            "dt": p.get("dt") or "",
            "dt_full": p.get("dt_full") or "",
            "amt": amt_s,
            "kind": kind,
            "is_rent": True,
            "yy1": "",
            "start_md": "",
            "end_md": "",
            "pay_md": "",
            "year_break": False,
            "tone": "",
            "amt_n": amt_n,
            "cycle_start": p.get("cycle_start") or "",
            "fill_row": False,
        }
        if kind == "보증":
            pay_d = _to_date(p.get("dt_full") or p.get("dt"))
            item["yy1"] = "보증"
            item["pay_md"] = pay_d.strftime("%y-%m-%d") if pay_d else (p.get("dt") or "")
        else:
            pay_n = amt_n
            if monthly > 0 and pay_n > 0:
                if pay_n < month_left:
                    item["tone"] = "short"
                    month_left -= pay_n
                elif pay_n > monthly:
                    item["tone"] = "over"
                    extra = (pay_n - month_left) % monthly
                    month_left = monthly if extra == 0 else monthly - extra
                else:
                    extra = (pay_n - month_left) % monthly
                    month_left = monthly if extra == 0 else monthly - extra
        payments.append(item)

    cursor = None
    slot_left = monthly
    last_pay_year = None
    rows = []
    out_d = _to_date(data.get("out_dt") or data.get("gijun_dt"))
    for item in payments:
        if item.get("kind") != "임대":
            rows.append(item)
            continue
        nat_s = _to_date(item.get("cycle_start"))
        pay_d = _to_date(item.get("dt_full") or item.get("dt"))
        amt = item.get("amt_n") or 0
        if cursor is None:
            assigned = nat_s
        else:
            assigned = cursor
        if not assigned:
            rows.append(item)
            continue
        n_periods = 1
        if monthly > 0 and amt > monthly:
            n_periods = max(1, amt // monthly)
        one_end = _add_months(assigned, 1)
        item["start_md"] = assigned.strftime("%m/%d")
        item["end_md"] = (one_end - timedelta(days=1)).strftime("%m/%d") if one_end else ""
        item["period_year"] = assigned.year
        item["period_sort"] = assigned.isoformat()
        if pay_d:
            pay_y = pay_d.year
            show_pay_yy = last_pay_year is None or pay_y != last_pay_year
            item["pay_md"] = (
                pay_d.strftime("%y-%m-%d") if show_pay_yy else pay_d.strftime("%m-%d")
            )
            last_pay_year = pay_y
        rows.append(item)
        extra_from = _add_months(assigned, 1)
        n_extras = n_periods - 1
        last_fill = None
        for i in range(n_extras):
            extra_s = _add_months(extra_from, i)
            extra_e = _add_months(extra_from, i + 1)
            if not extra_s or not extra_e:
                break
            if out_d and extra_s > out_d:
                break
            rows.append(
                {
                    "dt": "",
                    "amt": "",
                    "kind": "임대",
                    "is_rent": True,
                    "yy1": "",
                    "start_md": extra_s.strftime("%m/%d"),
                    "end_md": (extra_e - timedelta(days=1)).strftime("%m/%d"),
                    "pay_md": "",
                    "year_break": False,
                    "tone": "",
                    "amt_n": 0,
                    "fill_row": True,
                    "period_year": extra_s.year,
                    "period_sort": extra_s.isoformat(),
                }
            )
            last_fill = extra_s
        if last_fill:
            cursor = _add_months(last_fill, 1)
            rem = amt % monthly if monthly else 0
            slot_left = monthly - rem if rem else monthly
        elif monthly > 0 and amt < slot_left:
            cursor = assigned
            slot_left -= amt
        else:
            cursor = _add_months(assigned, 1)
            slot_left = monthly

    head = [r for r in rows if r.get("kind") != "임대"]
    rent_rows = [r for r in rows if r.get("kind") == "임대"]
    rent_rows.sort(
        key=lambda r: (
            r.get("period_sort") or "9999-99-99",
            1 if r.get("fill_row") else 0,
            r.get("dt_full") or r.get("dt") or "",
        )
    )
    rows = head + rent_rows
    last_period_year = None
    seen_rent = False
    for item in rows:
        if item.get("kind") != "임대" or not item.get("start_md"):
            if item.get("yy1") == "보증":
                seen_rent = True
            continue
        period_y = item.get("period_year")
        show_yy = period_y is not None and period_y != last_period_year
        item["year_break"] = bool(show_yy and seen_rent)
        item["yy1"] = f"{period_y % 100:02d}" if show_yy else ""
        if period_y is not None:
            last_period_year = period_y
        seen_rent = True

    payments = rows
    last_period = None
    kept = []
    for item in payments:
        if item.get("kind") != "임대":
            last_period = None
            kept.append(item)
            continue
        if not item.get("start_md"):
            kept.append(item)
            continue
        key = (item.get("period_year"), item.get("start_md"), item.get("end_md"))
        if key == last_period:
            if item.get("fill_row"):
                continue
            item["start_md"] = ""
            item["end_md"] = ""
        else:
            last_period = key
        kept.append(item)
    payments = kept
    rent["pay_pages"] = _paginate_pay_items(payments, bool(rent.get("day_amt")))
    util = {
        "elec": money(data.get("elec_amt")) if _to_int_amt(data.get("elec_amt")) else "",
        "water": money(data.get("sudo_amt")) if _to_int_amt(data.get("sudo_amt")) else "",
        "restore": money(data.get("sisul_amt")) if _to_int_amt(data.get("sisul_amt")) else "",
        "gas": money(data.get("gas_amt")) if _to_int_amt(data.get("gas_amt")) else "",
        "etc": money(data.get("gita_amt")) if _to_int_amt(data.get("gita_amt")) else "",
        "total": money(util_tot) if util_tot else "",
    }
    settle = {
        "amount": money(jungsan),
        "formula_nums": f"({money(h)} - {money(misu)} - {money(util_tot) or '0'})",
        "confirm_year": str(date.today().year),
        "confirm_month": "",
        "confirm_day": "",
        "confirmer": "",
    }
    return doc, fac, rent, payments, util, settle


def _checkout_form_from(src, today=None):
    if today is None:
        today = date.today().isoformat()
    return {
        "out_dt": (src.get("out_dt") or today).strip()[:10],
        "bunji1": _pad_bunji(src.get("bunji1")),
        "bunji2": _pad_bunji(src.get("bunji2")),
        "hosu": (src.get("hosu") or "").strip().upper(),
        "ipju_seq": (src.get("ipju_seq") or "").strip(),
        "edit": "1" if (src.get("edit") or "").strip() == "1" else "",
        "suri": (src.get("suri") or "0").strip(),
        "elec": (src.get("elec") or "0").strip(),
        "water": (src.get("water") or "0").strip(),
        "restore": (src.get("restore") or "0").strip(),
        "gas": (src.get("gas") or "0").strip(),
        "etc": (src.get("etc") or "0").strip(),
    }


def _checkout_extra(form):
    return {
        "suri": form["suri"],
        "elec": form["elec"],
        "water": form["water"],
        "restore": form["restore"],
        "gas": form["gas"],
        "etc": form["etc"],
    }


def _save_checkout(data, uid):
    """퇴실 정산 저장 + 입주 이력 퇴실일 반영. 성공 시 None, 실패 시 메시지."""
    out_d = data["out_dt"]
    mx = db.query_one(
        """
        SELECT MAX(CAST(out_seq AS UNSIGNED)) AS m FROM bd07_out
        WHERE out_dt=%s AND bunji1=%s AND bunji2=%s AND hosu=%s AND ipju_seq=%s
        """,
        (out_d, data["bunji1"], data["bunji2"], data["hosu"], data["ipju_seq"]),
    )
    out_seq = str(int((mx or {}).get("m") or 0) + 1).zfill(2)
    try:
        exists = db.query_one(
            """
            SELECT out_seq FROM bd07_out
            WHERE bunji1=%s AND bunji2=%s AND hosu=%s AND ipju_seq=%s
              AND out_dt=%s
            ORDER BY out_seq DESC LIMIT 1
            """,
            (
                data["bunji1"],
                data["bunji2"],
                data["hosu"],
                data["ipju_seq"],
                out_d,
            ),
        )
        if exists:
            out_seq = exists["out_seq"]
            db.execute(
                """
                UPDATE bd07_out SET
                  gijun_dt=%s, ipju_mm_cnt=%s, ipju_dd_cnt=%s,
                  ipkum_gijun=%s, sukum_tot=%s, h_amt=%s,
                  elec_amt=%s, sudo_amt=%s, sisul_amt=%s, gas_amt=%s, gita_amt=%s,
                  jungsan_amt=%s, g_suri_tot=%s,
                  g_napbu_gb=%s, g_ipju_dt=%s, g_ipju_nm=%s,
                  g_ipju_jumin_no=%s, g_ipju_tel=%s,
                  g_bojung_amt=%s, g_yechi_amt=%s, g_rent_amt=%s, g_manage_amt=%s,
                  uid=%s, sys_dt=NOW()
                WHERE out_dt=%s AND out_seq=%s AND bunji1=%s AND bunji2=%s
                  AND hosu=%s AND ipju_seq=%s
                """,
                (
                    out_d,
                    data["mm"],
                    data["dd"],
                    data["ipkum_gijun"],
                    data["sukum_tot"],
                    data["h_amt"],
                    data["elec_amt"],
                    data["sudo_amt"],
                    data["sisul_amt"],
                    data["gas_amt"],
                    data["gita_amt"],
                    data["jungsan_amt"],
                    data["suri_amt"],
                    data["napbu_gb"],
                    data["ipju_dt"] or None,
                    data["ipju_nm"],
                    data["jumin"][:15],
                    data["tel"][:50],
                    data["bojung_amt"],
                    data["yechi_amt"],
                    data["rent_amt"],
                    data["manage_amt"],
                    uid,
                    out_d,
                    out_seq,
                    data["bunji1"],
                    data["bunji2"],
                    data["hosu"],
                    data["ipju_seq"],
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO bd07_out (
                  out_dt, out_seq, gijun_dt, bunji1, bunji2, hosu, ipju_seq,
                  ipju_mm_cnt, ipju_dd_cnt, ipkum_gijun, sukum_tot, h_amt,
                  elec_amt, sudo_amt, sisul_amt, gas_amt, gita_amt, jungsan_amt,
                  g_suri_tot, g_napbu_gb, g_ipju_dt, g_ipju_nm, g_ipju_jumin_no,
                  g_ipju_tel, g_bojung_amt, g_yechi_amt, g_rent_amt, g_manage_amt,
                  uid, sys_dt
                ) VALUES (
                  %s,%s,%s,%s,%s,%s,%s,
                  %s,%s,%s,%s,%s,
                  %s,%s,%s,%s,%s,%s,
                  %s,%s,%s,%s,%s,
                  %s,%s,%s,%s,%s,
                  %s,NOW()
                )
                """,
                (
                    out_d,
                    out_seq,
                    out_d,
                    data["bunji1"],
                    data["bunji2"],
                    data["hosu"],
                    data["ipju_seq"],
                    data["mm"],
                    data["dd"],
                    data["ipkum_gijun"],
                    data["sukum_tot"],
                    data["h_amt"],
                    data["elec_amt"],
                    data["sudo_amt"],
                    data["sisul_amt"],
                    data["gas_amt"],
                    data["gita_amt"],
                    data["jungsan_amt"],
                    data["suri_amt"],
                    data["napbu_gb"],
                    data["ipju_dt"] or None,
                    data["ipju_nm"],
                    data["jumin"][:15],
                    data["tel"][:50],
                    data["bojung_amt"],
                    data["yechi_amt"],
                    data["rent_amt"],
                    data["manage_amt"],
                    uid,
                ),
            )
        db.execute(
            """
            UPDATE bd03_det
            SET out_dt=%s, out_seq=%s, out_jungsan_end=%s, sys_dt=NOW(), uid=%s
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (
                out_d,
                out_seq,
                "Y",
                uid,
                data["bunji1"],
                data["bunji2"],
                data["hosu"],
                data["ipju_seq"],
            ),
        )
    except Exception:
        app.logger.exception("퇴실 정산 저장 실패")
        return "퇴실 정산을 저장하지 못했습니다. 입력 내용을 확인한 뒤 다시 시도하세요."
    return None


@app.route("/checkout", methods=["GET", "POST"])
@login_required
@require_write_access
def checkout():
    """퇴실 정산 관리 (XP). 조회·저장·인쇄."""
    today = date.today().isoformat()

    if request.method == "POST":
        action = (request.form.get("action") or "calc").strip()
        f = _checkout_form_from(request.form, today)
        extra = _checkout_extra(f)

        if action == "new":
            return redirect(url_for("checkout"))

        data = _checkout_build(
            f["bunji1"], f["bunji2"], f["hosu"], f["ipju_seq"], f["out_dt"], extra
        )
        if data.get("error"):
            flash(data["error"], "err")
            return redirect(url_for("checkout", **{k: v for k, v in f.items() if v}))

        if action == "plan":
            plan_date = _to_date(data.get("out_dt"))
            if not plan_date or plan_date <= date.today():
                flash("퇴실예정일은 오늘보다 이후 날짜로 입력해 주세요.", "err")
            elif not data.get("is_current"):
                flash("현재 입주자만 퇴실예정을 등록할 수 있습니다.", "err")
            else:
                try:
                    db.execute(
                        """
                        UPDATE bd03_det
                        SET plan_out_dt=%s, sys_dt=NOW(), uid=%s
                        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                          AND ipju_seq=%s
                          AND (out_dt IS NULL OR out_dt < '1000-01-01')
                        """,
                        (
                            plan_date.isoformat(),
                            session.get("sabun") or "",
                            data["bunji1"],
                            data["bunji2"],
                            data["hosu"],
                            data["ipju_seq"],
                        ),
                    )
                    flash(
                        f"퇴실예정이 등록되었습니다. ({data['ipju_nm']} · {plan_date.isoformat()})",
                        "ok",
                    )
                except Exception:
                    app.logger.exception("퇴실예정 등록 실패")
                    flash("퇴실예정을 등록하지 못했습니다. 다시 시도해 주세요.", "err")
            return redirect(
                url_for(
                    "checkout",
                    bunji1=fmt_bunji(data["bunji1"]),
                    bunji2=fmt_bunji(data["bunji2"]),
                    hosu=data["hosu"],
                    ipju_seq=data["ipju_seq"],
                    out_dt=data["out_dt"],
                )
            )

        if action == "save":
            out_date = _to_date(data.get("out_dt"))
            if out_date and out_date > date.today():
                flash(
                    "퇴실일자가 미래 날짜입니다. 퇴실 확정은 오늘 또는 이전 날짜로 입력해 주세요.",
                    "err",
                )
                return redirect(
                    url_for(
                        "checkout",
                        bunji1=fmt_bunji(data["bunji1"]),
                        bunji2=fmt_bunji(data["bunji2"]),
                        hosu=data["hosu"],
                        ipju_seq=data["ipju_seq"],
                        out_dt=data["out_dt"],
                        suri=f["suri"],
                        elec=f["elec"],
                        water=f["water"],
                        restore=f["restore"],
                        gas=f["gas"],
                        etc=f["etc"],
                        edit=f["edit"] or None,
                    )
                )
            err = _save_checkout(data, session.get("sabun") or "")
            if err:
                flash(err, "err")
            else:
                flash(
                    f"퇴실 정산을 저장했습니다. ({data['ipju_nm']} · 정산 {money(data['jungsan_amt'])}원)",
                    "ok",
                )
            return redirect(
                url_for(
                    "checkout",
                    bunji1=fmt_bunji(data["bunji1"]),
                    bunji2=fmt_bunji(data["bunji2"]),
                    hosu=data["hosu"],
                    ipju_seq=data["ipju_seq"],
                    out_dt=data["out_dt"],
                    edit=f["edit"] or None,
                )
            )

        # action=calc → 같은 화면 재표시 (GET으로)
        return redirect(
            url_for(
                "checkout",
                bunji1=fmt_bunji(f["bunji1"]) if f["bunji1"] else "",
                bunji2=fmt_bunji(f["bunji2"]) if f["bunji2"] else "",
                hosu=f["hosu"],
                ipju_seq=f["ipju_seq"],
                out_dt=f["out_dt"],
                suri=f["suri"],
                elec=f["elec"],
                water=f["water"],
                restore=f["restore"],
                gas=f["gas"],
                etc=f["etc"],
                edit=f["edit"] or None,
            )
        )

    # GET
    f = _checkout_form_from(request.args, today)
    if not f["out_dt"]:
        f["out_dt"] = today
    data = None
    if f["bunji1"] and f["bunji2"] and f["hosu"]:
        if f["ipju_seq"]:
            has_extra_args = any(
                name in request.args
                for name in ("suri", "elec", "water", "restore", "gas", "etc")
            )
            saved = db.query_one(
                """SELECT out_dt,elec_amt,sudo_amt,sisul_amt,gas_amt,gita_amt,g_suri_tot
                   FROM bd07_out WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                     AND ipju_seq=%s ORDER BY out_dt DESC,out_seq DESC LIMIT 1""",
                (f["bunji1"], f["bunji2"], f["hosu"], f["ipju_seq"]),
            )
            if saved:
                # URL에 명시된 날짜/공과금은 다시 계산 결과이므로 유지한다.
                # 값이 없는 최초 진입에서만 저장된 정산 자료를 불러온다.
                if not request.args.get("out_dt"):
                    f["out_dt"] = fmt_date(saved.get("out_dt")) or f["out_dt"]
                if not has_extra_args:
                    f["suri"] = str(_to_int_amt(saved.get("g_suri_tot")))
                    f["elec"] = str(_to_int_amt(saved.get("elec_amt")))
                    f["water"] = str(_to_int_amt(saved.get("sudo_amt")))
                    f["restore"] = str(_to_int_amt(saved.get("sisul_amt")))
                    f["gas"] = str(_to_int_amt(saved.get("gas_amt")))
                    f["etc"] = str(_to_int_amt(saved.get("gita_amt")))
            else:
                tenant_date = db.query_one(
                    """SELECT out_dt FROM bd03_det WHERE bunji1=%s AND bunji2=%s
                       AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s""",
                    (f["bunji1"], f["bunji2"], f["hosu"], f["ipju_seq"]),
                )
                if (
                    not request.args.get("out_dt")
                    and tenant_date
                    and tenant_date.get("out_dt")
                ):
                    f["out_dt"] = fmt_date(tenant_date["out_dt"])
        extra = _checkout_extra(f)
        data = _checkout_build(
            f["bunji1"], f["bunji2"], f["hosu"], f["ipju_seq"], f["out_dt"], extra
        )
        if data.get("error"):
            flash(data["error"], "err")
            data = None
        elif data:
            f["ipju_seq"] = data.get("ipju_seq") or f["ipju_seq"]

    pager = None
    if data and data.get("payments"):
        data["payments"], pager = _paginate(data["payments"])

    if (request.args.get("partial") or "").strip() == "pays":
        return render_template(
            "checkout_pays.html",
            form=f,
            data=data or {"payments": []},
            pager=pager,
        )

    return render_template(
        "checkout.html",
        form=f,
        data=data,
        pager=pager,
        building_label=_building_label(f["bunji1"], f["bunji2"])
        if f["bunji1"] and f["bunji2"]
        else "",
    )


@app.route("/checkout/list", methods=["GET", "POST"])
@login_required
@require_write_access
def checkout_list():
    """
    퇴실(예정)자 조회 및 변경처리 (XP「호수 퇴실자 내역 조회」).
    목록 조회 + 현입주자로 변환(퇴실일 취소).
    """
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        b1 = _pad_bunji(request.form.get("bunji1"))
        b2 = _pad_bunji(request.form.get("bunji2"))
        hosu = (request.form.get("hosu") or "").strip().upper()
        seq = (request.form.get("ipju_seq") or "").strip()
        if seq.isdigit():
            seq = seq.zfill(2)
        keep = {
            "bunji1": fmt_bunji(b1) if b1 else request.form.get("q_bunji1") or "",
            "bunji2": fmt_bunji(b2) if b2 else request.form.get("q_bunji2") or "",
            "hosu": (request.form.get("q_hosu") or request.form.get("hosu") or "").strip().upper(),
            "name": (request.form.get("q_name") or request.form.get("name") or "").strip(),
            "mode": request.form.get("mode") or "all",
        }
        if action == "restore":
            if not (b1 and b2 and hosu and seq):
                flash("변환할 행을 선택하세요.", "err")
                return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))
            # 같은 호에 이미 현거주가 있으면 차단
            other = db.query_one(
                f"""
                SELECT ipju_seq, ipju_nm FROM bd03_det
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                  AND ipju_seq<>%s
                  AND {_CURRENT_TENANT_SQL.replace('d.', '')}
                LIMIT 1
                """,
                (b1, b2, hosu, seq),
            )
            if other:
                flash(
                    f"같은 호에 현거주자({other.get('ipju_nm') or other.get('ipju_seq')})가 있어 "
                    "현입주자로 변환할 수 없습니다.",
                    "err",
                )
                return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))
            try:
                n = db.execute(
                    """
                    UPDATE bd03_det
                    SET out_dt=NULL, out_seq=NULL, plan_out_dt=NULL,
                        sys_dt=NOW(), uid=%s
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                    """,
                    (session.get("sabun") or "", b1, b2, hosu, seq),
                )
                if n:
                    flash("현입주자로 변환했습니다. (퇴실·예정일 취소)", "ok")
                else:
                    flash("대상 이력을 찾지 못했습니다.", "err")
            except Exception as e:
                flash(f"변환 실패: {e}", "err")
            return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))

        return redirect(url_for("checkout_list"))

    # GET
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    name = (request.args.get("name") or "").strip()
    mode = (request.args.get("mode") or "out").strip().lower()
    if mode not in ("all", "out", "plan"):
        mode = "all"
    # 호수만으로는 건물을 특정할 수 없으므로 주소 또는 이름이 필요하다.
    has_min_filter = bool(bunji1 or name)
    want_query = "q" in request.args
    ran = want_query and has_min_filter
    empty_msg = ""
    if want_query and not has_min_filter:
        empty_msg = "주소 또는 이름 중 하나를 입력한 뒤 조회하세요."

    results = []
    total = 0
    total_pages = 1
    if ran:
        where = ["1=1"]
        args = []
        if mode == "out":
            where.append("(out_dt IS NOT NULL AND out_dt >= '1000-01-01')")
        elif mode == "plan":
            where.append(
                "(plan_out_dt IS NOT NULL AND plan_out_dt >= '1000-01-01') "
                "AND (out_dt IS NULL OR out_dt < '1000-01-01')"
            )
        else:
            where.append(
                "((out_dt IS NOT NULL AND out_dt >= '1000-01-01') "
                "OR (plan_out_dt IS NOT NULL AND plan_out_dt >= '1000-01-01'))"
            )
        if bunji1:
            where.append("bunji1=%s")
            args.append(bunji1)
        if bunji2:
            where.append("bunji2=%s")
            args.append(bunji2)
        if hosu:
            where.append("UPPER(TRIM(hosu))=%s")
            args.append(hosu)
        if name:
            where.append("ipju_nm LIKE %s")
            args.append(f"%{name}%")
        where_sql = " AND ".join(where)

        cnt = db.query_one(
            f"SELECT COUNT(*) AS c FROM bd03_det WHERE {where_sql}",
            args,
        )
        total = int((cnt or {}).get("c") or 0)
        pager = _make_pager(total)
        page = pager["page"]

        rows = db.query(
            f"""
            SELECT bunji1, bunji2, hosu, ipju_seq, ipju_nm, ipju_jumin_no,
                   ipju_dt, out_dt, plan_out_dt, bojung_amt, rent_amt, manage_amt, napbu_gb
            FROM bd03_det
            WHERE {where_sql}
            ORDER BY COALESCE(out_dt, plan_out_dt) DESC, bunji1, bunji2, hosu
            LIMIT %s OFFSET %s
            """,
            args + [pager["per_page"], pager["offset"]],
        )
        for r in rows:
            has_out = r.get("out_dt") and (
                not isinstance(r["out_dt"], datetime) or r["out_dt"].year >= 1000
            )
            has_plan = r.get("plan_out_dt") and (
                not isinstance(r["plan_out_dt"], datetime) or r["plan_out_dt"].year >= 1000
            )
            if has_out:
                kind = "퇴실"
                kind_dt = r.get("out_dt")
            elif has_plan:
                kind = "퇴실예정"
                kind_dt = r.get("plan_out_dt")
            else:
                kind = "—"
                kind_dt = None
            results.append(
                {
                    **r,
                    "kind": kind,
                    "kind_dt": kind_dt,
                    # 퇴실일 / 퇴실예정일 분리 표시 (한 칸에 합치지 않음)
                    "out_dt_disp": fmt_date(r.get("out_dt")) if has_out else "",
                    "plan_out_dt_disp": fmt_date(r.get("plan_out_dt"))
                    if has_plan
                    else "",
                    "jumin_disp": mask_jumin(r.get("ipju_jumin_no")),
                }
            )

    building_label = (
        _building_label(bunji1, bunji2) if bunji1 and bunji2 else ""
    )

    # 결과 0건일 때 원인 구분 (주소/호수/이력)
    if ran and total == 0:
        mode_lab = {"all": "퇴실·예정", "out": "퇴실", "plan": "퇴실예정"}.get(
            mode, "퇴실·예정"
        )
        if bunji1 and bunji2:
            bld = db.query_one(
                "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
                (bunji1, bunji2),
            )
            if not bld:
                empty_msg = "등록되지 않은 주소입니다. 주소를 확인하세요."
            elif hosu:
                room = db.query_one(
                    """
                    SELECT hosu FROM bd03_m
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                    """,
                    (bunji1, bunji2, hosu),
                )
                if not room:
                    empty_msg = "등록되지 않은 호수입니다. 호수를 확인하세요."
                else:
                    empty_msg = f"해당 호수의 {mode_lab} 이력이 없습니다."
            else:
                empty_msg = f"해당 주소의 {mode_lab} 이력이 없습니다."
        elif bunji1 and not bunji2:
            empty_msg = f"주소 조건에 맞는 {mode_lab} 이력이 없습니다."
        elif hosu:
            empty_msg = f"호수 조건에 맞는 {mode_lab} 이력이 없습니다."
        elif name:
            empty_msg = f"「{name}」 이름에 맞는 {mode_lab} 이력이 없습니다."
        else:
            empty_msg = f"조건에 맞는 {mode_lab} 이력이 없습니다."

    if not ran:
        pager = _make_pager(0)

    return render_template(
        "checkout_list.html",
        filters={
            "bunji1": bunji1,
            "bunji2": bunji2,
            "hosu": hosu,
            "name": name,
            "mode": mode,
            "page": pager["page"],
        },
        results=results,
        ran=ran,
        want_query=want_query,
        building_label=building_label,
        empty_msg=empty_msg,
        pager=pager,
    )


@app.route("/checkout/print")
@login_required
def checkout_print():
    """계약 해지 인쇄 — 퇴실 정산 데이터 바인딩."""
    f = {
        "out_dt": (request.args.get("out_dt") or date.today().isoformat())[:10],
        "bunji1": _pad_bunji(request.args.get("bunji1")),
        "bunji2": _pad_bunji(request.args.get("bunji2")),
        "hosu": (request.args.get("hosu") or "").strip().upper(),
        "ipju_seq": (request.args.get("ipju_seq") or "").strip(),
        "suri": request.args.get("suri") or "0",
        "elec": request.args.get("elec") or "0",
        "water": request.args.get("water") or "0",
        "restore": request.args.get("restore") or "0",
        "gas": request.args.get("gas") or "0",
        "etc": request.args.get("etc") or "0",
    }
    data = _checkout_build(
        f["bunji1"],
        f["bunji2"],
        f["hosu"],
        f["ipju_seq"],
        f["out_dt"],
        {
            "suri": f["suri"],
            "elec": f["elec"],
            "water": f["water"],
            "restore": f["restore"],
            "gas": f["gas"],
            "etc": f["etc"],
        },
    )
    if not data or data.get("error"):
        flash(data.get("error") if data else "인쇄할 데이터가 없습니다.", "err")
        return redirect(url_for("checkout"))
    doc, fac, rent, payments, util, settle = _checkout_to_print(data)
    return render_template(
        "contract_cancel_print.html",
        data=data,
        doc=doc,
        fac=fac,
        rent=rent,
        payments=payments,
        util=util,
        settle=settle,
        form=f,
    )


@app.route("/print/contract-cancel")
@app.route("/print/contract-cancel/sample")
@login_required
def contract_cancel_print_sample():
    """샘플 계약 해지 인쇄 (강현숙 PDF 수치)."""
    return redirect(
        url_for(
            "checkout_print",
            bunji1="1139",
            bunji2="4",
            hosu="707",
            ipju_seq="12",
            out_dt="2026-08-11",
        )
    )
