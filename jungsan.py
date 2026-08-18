"""월정기보고(정산) 화면.

주소별 정산서 작성/미리보기, 결산현황 인쇄, 월별 정산서 목록 조회
라우트와 그 전용 도우미 함수들을 모아둔 모듈입니다.
"""
import math
from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    building_label as _building_label,
    calc_misu_amt as _calc_misu_amt,
    calc_month_misu_amt as _calc_month_misu_amt,
    fmt_bunji,
    fmt_date,
    fmt_ipju_short as _fmt_ipju_short,
    login_required,
    money,
    next_sukum_seq as _next_sukum_seq,
    pad_bunji as _pad_bunji,
    paginate as _paginate,
    require_write_access,
    to_int_amt as _to_int_amt,
)


def _month_bounds(as_of):
    """기준일이 속한 달의 시작·끝 (date)."""
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    start = as_of.replace(day=1)
    end = date(as_of.year, as_of.month, monthrange(as_of.year, as_of.month)[1])
    return start, end


def _as_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _valid_out_dt(v):
    d = _as_date(v)
    if not d or d.year < 1000:
        return None
    return d


def _ceil_100(v):
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    return int(math.ceil(n / 100.0) * 100)


def _prorate_amt(amt, days, month_days):
    amt = _to_int_amt(amt)
    if amt <= 0 or days <= 0:
        return 0
    if month_days > 0 and days >= month_days:
        return amt
    return _ceil_100(amt * days / float(month_days))


def _jungsan_month_rent_split(napbu, rent, ipju_dt, out_dt, month_start, month_end):
    """당월 임대료 계산분·선불 청구(미적용)분.
    후불+당월퇴실: 퇴실일까지 일할.
    선불+당월퇴실: 퇴실 다음날~월말 일할(대체금이 있을 때만 청구로 씀).
    그 외: 전액, 청구 0.
    """
    rent = _to_int_amt(rent)
    out_d = _valid_out_dt(out_dt)
    month_days = (month_end - month_start).days + 1
    left_this_month = bool(out_d and month_start <= out_d <= month_end)
    if not left_this_month or rent <= 0 or month_days <= 0:
        return rent, 0
    nap = str(napbu or "B").strip().upper()
    if nap == "A":
        unused = (month_end - out_d).days
        return rent, _prorate_amt(rent, unused, month_days)
    ipju = _as_date(ipju_dt) or month_start
    start = max(ipju, month_start)
    end = min(out_d, month_end)
    occ = (end - start).days + 1 if end >= start else 0
    return _prorate_amt(rent, occ, month_days), 0


def _jungsan_month_tenants(b1, b2, month_start, month_end):
    """당월에 거주한 호실·입주 (현재 입주 + 당월 퇴실)."""
    return db.query(
        """
        SELECT m.hosu,
               d.ipju_seq, d.ipju_nm, d.ipju_dt, d.out_dt,
               d.bojung_amt, d.rent_amt, d.manage_amt, d.napbu_gb
        FROM bd03_m m
        LEFT JOIN bd03_det d
          ON d.bunji1=m.bunji1 AND d.bunji2=m.bunji2
         AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu))
         AND (d.del_yn IS NULL OR d.del_yn='N' OR d.del_yn='')
         AND d.ipju_dt IS NOT NULL
         AND d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY)
         AND (
            d.out_dt IS NULL OR d.out_dt < '1000-01-01'
            OR d.out_dt >= %s
         )
        WHERE m.bunji1=%s AND m.bunji2=%s
        ORDER BY m.hosu, d.ipju_dt
        """,
        (month_end.isoformat(), month_start.isoformat(), b1, b2),
    )


def _fmt_man_int(v):
    """보증금 인쇄: 원→만원 정수, 0이면 빈칸"""
    n = _to_int_amt(v)
    if n <= 0:
        return ""
    return str(int(round(n / 10000)))


def _fmt_man_dec(v):
    """관리비 인쇄: 7.0"""
    n = _to_int_amt(v)
    man = n / 10000.0
    if abs(man - round(man)) < 1e-9:
        return f"{int(round(man))}.0"
    return f"{man:.1f}"


def _fmt_wolse_cell(napbu_gb, rent_amt):
    """월세 인쇄: '선 26' / '후 29' (만원)"""
    n = _to_int_amt(rent_amt)
    if n <= 0 and not napbu_gb:
        return ""
    man = int(round(n / 10000)) if n else 0
    tag = "선" if str(napbu_gb or "").upper() == "A" else "후"
    return f"{tag} {man}"


def _jungsan_decorate_rows(rows):
    """인쇄·화면용 표시 문자열 채우기"""
    for r in rows:
        empty = r.get("is_empty") or (r.get("ipju_nm") or "").replace(" ", "") in (
            "",
            "공실",
        )
        r["is_empty"] = empty
        if empty:
            r["ipju_nm_disp"] = "공 실"
            r["ipju_dt_disp"] = ""
            r["bojung_disp"] = ""
            r["wolse_disp"] = ""
            r["manage_disp"] = ""
            r["ipkum_disp"] = ""
            r["misu_disp"] = ""
            r["jisi_disp"] = ""
        else:
            r["ipju_nm_disp"] = (r.get("ipju_nm") or "").strip()
            r["ipju_dt_disp"] = _fmt_ipju_short(r.get("ipju_dt"))
            r["bojung_disp"] = _fmt_man_int(r.get("bojung_amt"))
            r["wolse_disp"] = _fmt_wolse_cell(r.get("napbu_gb"), r.get("rent_amt"))
            r["manage_disp"] = _fmt_man_dec(r.get("manage_amt")) if _to_int_amt(r.get("manage_amt")) or _to_int_amt(r.get("rent_amt")) else ""
            r["ipkum_disp"] = money(r.get("ipkum_amt")) if _to_int_amt(r.get("ipkum_amt")) else ""
            r["misu_disp"] = money(r.get("misu_amt")) if _to_int_amt(r.get("misu_amt")) else ""
            jisi = (r.get("manage_desc") or "").strip()
            dache = str(r.get("dache_gb") or "").strip()
            is_dache = ("대체" in dache) or (dache.upper() in ("Y", "1"))
            if jisi == "청구":
                r["jisi_disp"] = "청구"
            elif is_dache:
                r["manage_desc"] = ""
                r["jisi_disp"] = "(대체)"
            else:
                r["jisi_disp"] = jisi
    return rows


def _jungsan_build_preview(bunji1, bunji2, as_of):
    """
    주소별 정산서 조회 미리보기 (화면 표시용).
    저장된 jungsan 이 있으면 그 데이터, 없으면 현재 호·입주·수금으로 계산.
    """
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    if not b1 or not b2:
        return None
    if isinstance(as_of, str):
        as_of = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
    month_start, month_end = _month_bounds(as_of)
    # 조회 키: 해당 월 말일 기준 저장분 우선
    as_of_s = as_of.isoformat()
    month_end_s = month_end.isoformat()

    building = db.query_one(
        "SELECT bunji1, bunji2, juso, owner_nm, first_amt, man_cost, mgmt_gb FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (b1, b2),
    )
    if not building:
        return {"error": "미등록 주소입니다.", "building": None}

    # 관리형태(mgmt_gb): 책임관리(R)/일반관리(G).
    # 책임관리 미입금은 자동 대체하지 않음 — 화면「일괄대체」로 sukum01 에 넣음.
    # 옛 건물 중 관리형태 미지정분은 관리수수료 유무로 추정(과거 로직 호환).
    mgmt_gb = (building.get("mgmt_gb") or "").strip().upper()
    if mgmt_gb in ("R", "G"):
        is_resp = mgmt_gb == "R"
    else:
        is_resp = _to_int_amt(building.get("man_cost")) > 0

    # 저장된 정산서 (기준일 또는 그 달 말일)
    saved = db.query_one(
        """
        SELECT * FROM jungsan_m
        WHERE bunji1=%s AND bunji2=%s
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
          AND (
            DATE(jungsan_dt)=%s
            OR (jungsan_dt >= %s AND jungsan_dt < DATE_ADD(%s, INTERVAL 1 DAY))
          )
        ORDER BY jungsan_dt DESC, jungsan_seq DESC
        LIMIT 1
        """,
        (b1, b2, as_of_s, month_start.isoformat(), month_end_s),
    )

    source = "live"
    rows = []
    summary = {}

    if saved:
        source = "saved"
        det = db.query(
            """
            SELECT * FROM jungsan_det
            WHERE bunji1=%s AND bunji2=%s
              AND jungsan_dt=%s AND jungsan_seq=%s
              AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
            ORDER BY hosu
            """,
            (b1, b2, saved["jungsan_dt"], saved["jungsan_seq"]),
        )
        for d in det:
            nm = (d.get("ipju_nm") or "").strip()
            empty = (not nm) or nm.replace(" ", "") == "공실"
            # napbu: 입주 이력에서 보강
            nap = ""
            if not empty and d.get("ipju_seq"):
                trow = db.query_one(
                    """
                    SELECT napbu_gb FROM bd03_det
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                    """,
                    (
                        b1,
                        b2,
                        (d.get("hosu") or "").strip().upper(),
                        str(d.get("ipju_seq") or "").zfill(2),
                    ),
                )
                nap = (trow or {}).get("napbu_gb") or ""
            rows.append(
                {
                    "hosu": d.get("hosu"),
                    "ipju_nm": "공실" if empty else nm,
                    "ipju_dt": d.get("ipju_dt"),
                    "ipju_seq": d.get("ipju_seq"),
                    "napbu_gb": nap,
                    "bojung_amt": _to_int_amt(d.get("bojung_amt")),
                    "rent_amt": _to_int_amt(d.get("rent_amt")),
                    "manage_amt": _to_int_amt(d.get("manage_amt")),
                    "ipkum_amt": 0,
                    "manage_desc": d.get("manage_desc") or "",
                    "dache_gb": d.get("dache_gb") or "",
                    "dache_rent": 0,
                    "dache_amt": 0,
                    "rent_calc": _to_int_amt(d.get("rent_amt")),
                    "claim_amt": 0,
                    "misu_amt": _to_int_amt(d.get("misu_amt")),
                    "is_empty": empty,
                }
            )
        for r in rows:
            if r["is_empty"]:
                continue
            paid = db.query_one(
                """
                SELECT COALESCE(SUM(COALESCE(su_sil_amt,0)+COALESCE(su_dache_amt,0)),0) AS paid
                FROM sukum01
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                  AND sukum_char='01'
                  AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
                  AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
                """,
                (
                    b1,
                    b2,
                    (r["hosu"] or "").strip().upper(),
                    str(r.get("ipju_seq") or "").zfill(2),
                    month_start.isoformat(),
                    month_end_s,
                ),
            )
            r["ipkum_amt"] = _to_int_amt((paid or {}).get("paid"))
        summary = {
            "first_amt": _to_int_amt(saved.get("first_amt")),
            "man_cost": _to_int_amt(saved.get("man_cost")),
            "owner_suri": _to_int_amt(saved.get("owner_suri")),
            "jungke_cost": _to_int_amt(saved.get("jungke_cost")),
            "pay_amt": _to_int_amt(saved.get("pay_amt")),
            "ipkum_tot": _to_int_amt(saved.get("ipkum_tot")),
            "rent_tot": _to_int_amt(saved.get("rent_tot")),
            "manage_tot": _to_int_amt(saved.get("manage_tot")),
            "bojung_tot": _to_int_amt(saved.get("bojung_tot")),
            "misu_tot": _to_int_amt(saved.get("misu_tot")),
            "imdae_dache": _to_int_amt(saved.get("misu_tot")),  # 인쇄: 임대료대체 ≈ 미수합
            "bojung_dache": 0,
            "note": (saved.get("jungke_desc") or "").strip(),
            "jungsan_dt": fmt_date(saved.get("jungsan_dt")),
            "jungsan_seq": saved.get("jungsan_seq"),
        }
    else:
        # 라이브: 호수 + 당월 거주자(현재 입주 · 당월 퇴실)
        rooms = _jungsan_month_tenants(b1, b2, month_start, month_end)
        sum_bojung = sum_rent = sum_manage = sum_ipkum = sum_misu = 0
        tenant_cnt = 0
        for m in rooms:
            hosu = (m.get("hosu") or "").strip()
            nm = (m.get("ipju_nm") or "").strip()
            if not nm:
                rows.append(
                    {
                        "hosu": hosu,
                        "ipju_nm": "공실",
                        "ipju_dt": None,
                        "ipju_seq": "",
                        "napbu_gb": "",
                        "bojung_amt": 0,
                        "rent_amt": 0,
                        "manage_amt": 0,
                        "ipkum_amt": 0,
                        "manage_desc": "",
                        "dache_gb": "",
                        "dache_rent": 0,
                        "dache_amt": 0,
                        "rent_calc": 0,
                        "claim_amt": 0,
                        "misu_amt": 0,
                        "is_empty": True,
                    }
                )
                continue
            tenant_cnt += 1
            seq = str(m.get("ipju_seq") or "").zfill(2)
            rent = _to_int_amt(m.get("rent_amt"))
            manage = _to_int_amt(m.get("manage_amt"))
            bojung = _to_int_amt(m.get("bojung_amt"))
            paid_row = db.query_one(
                """
                SELECT COALESCE(SUM(COALESCE(su_sil_amt,0)+COALESCE(su_dache_amt,0)),0) AS paid
                FROM sukum01
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                  AND sukum_char='01'
                  AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
                  AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
                """,
                (b1, b2, hosu.upper(), seq, month_start.isoformat(), month_end_s),
            )
            ipkum = _to_int_amt((paid_row or {}).get("paid"))
            misu = _calc_misu_amt(
                b1, b2, hosu, seq, rent, manage, m.get("ipju_dt"), as_of=as_of
            )
            dache_row = db.query_one(
                """
                SELECT COALESCE(SUM(COALESCE(su_dache_amt,0)),0) AS d
                FROM sukum01
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                  AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
                  AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
                  AND COALESCE(su_dache_amt,0) > 0
                """,
                (b1, b2, hosu.upper(), seq, month_start.isoformat(), month_end_s),
            )
            dache_amt = _to_int_amt((dache_row or {}).get("d"))
            rent_calc, claim_raw = _jungsan_month_rent_split(
                m.get("napbu_gb"), rent, m.get("ipju_dt"), m.get("out_dt"),
                month_start, month_end,
            )
            # 선불 퇴실 청구는 대체금이 있을 때만. 대체 없으면 청구 없음.
            claim_amt = min(claim_raw, dache_amt) if (claim_raw > 0 and dache_amt > 0) else 0
            if claim_amt > 0:
                jisi = "청구"
            elif dache_amt > 0:
                jisi = ""
            elif rent_calc > 0 and ipkum <= 0:
                jisi = "미납"
            elif rent_calc > 0 and ipkum < rent_calc:
                jisi = "부족"
            else:
                jisi = ""
            rows.append(
                {
                    "hosu": hosu,
                    "ipju_nm": nm,
                    "ipju_dt": m.get("ipju_dt"),
                    "ipju_seq": seq,
                    "napbu_gb": m.get("napbu_gb") or "B",
                    "bojung_amt": bojung,
                    "rent_amt": rent,
                    "manage_amt": manage,
                    "ipkum_amt": ipkum,
                    "manage_desc": jisi,
                    "dache_gb": "대체" if dache_amt > 0 else "",
                    "dache_rent": 0,
                    "dache_amt": dache_amt,
                    "rent_calc": rent_calc,
                    "claim_amt": claim_amt,
                    "misu_amt": misu,
                    "is_empty": False,
                }
            )
            sum_bojung += bojung
            sum_rent += rent
            sum_manage += manage
            sum_ipkum += ipkum
            sum_misu += misu

        suri = db.query_one(
            """
            SELECT COALESCE(SUM(COALESCE(owner_budam,0)),0) AS a
            FROM bd05_suri
            WHERE bunji1=%s AND bunji2=%s
              AND suri_dt >= %s AND suri_dt < DATE_ADD(%s, INTERVAL 1 DAY)
            """,
            (b1, b2, month_start.isoformat(), month_end_s),
        )
        jungke = db.query_one(
            """
            SELECT COALESCE(SUM(COALESCE(jungke_amt,0)),0) AS a
            FROM sjungke01
            WHERE bunji1=%s AND bunji2=%s
              AND jungke_dt >= %s AND jungke_dt < DATE_ADD(%s, INTERVAL 1 DAY)
            """,
            (b1, b2, month_start.isoformat(), month_end_s),
        )
        man_cost = _to_int_amt(building.get("man_cost"))
        owner_suri = _to_int_amt((suri or {}).get("a"))
        jungke_cost = _to_int_amt((jungke or {}).get("a"))
        # 실입(+이미 넣은 대체) − 수수료 − 선불청구. 미입금 가상대체는 넣지 않음.
        dache_sum = sum(_to_int_amt(r.get("dache_amt")) for r in rows)
        claim_sum = sum(_to_int_amt(r.get("claim_amt")) for r in rows)
        pay_amt = max(0, sum_ipkum - man_cost - owner_suri - jungke_cost - claim_sum)
        summary = {
            "first_amt": _to_int_amt(building.get("first_amt")),
            "man_cost": man_cost,
            "owner_suri": owner_suri,
            "jungke_cost": jungke_cost,
            "pay_amt": pay_amt,
            "ipkum_tot": sum_ipkum,
            "rent_tot": sum_rent,
            "manage_tot": sum_manage,
            "bojung_tot": sum_bojung,
            "misu_tot": sum_misu,
            "imdae_dache": dache_sum,
            "bojung_dache": 0,
            "claim_tot": claim_sum,
            "note": "",
            "jungsan_dt": None,
            "jungsan_seq": None,
            "tenant_cnt": tenant_cnt,
        }

    # 수리 내역 줄 (인쇄 하단)
    suri_lines = db.query(
        """
        SELECT suri_dt, suri_won_amt, owner_budam, suri_desc, hosu
        FROM bd05_suri
        WHERE bunji1=%s AND bunji2=%s
          AND suri_dt >= %s AND suri_dt < DATE_ADD(%s, INTERVAL 1 DAY)
        ORDER BY suri_dt, suri_seq
        """,
        (b1, b2, month_start.isoformat(), month_end_s),
    )
    suri_detail = []
    for s in suri_lines or []:
        amt = _to_int_amt(s.get("owner_budam")) or _to_int_amt(s.get("suri_won_amt"))
        if amt <= 0:
            continue
        suri_detail.append(
            {
                "dt": _fmt_ipju_short(s.get("suri_dt")),
                "amt": amt,
                "amt_disp": money(amt),
                "desc": (
                    f"{(s.get('hosu') or '').strip()}호 {(s.get('suri_desc') or '').strip()}"
                ).strip(),
            }
        )

    ipkum_sum = sum(_to_int_amt(r.get("ipkum_amt")) for r in rows)
    dache_sum = sum(_to_int_amt(r.get("dache_amt")) for r in rows)
    claim_sum = sum(_to_int_amt(r.get("claim_amt")) for r in rows)
    summary["ipkum_tot"] = ipkum_sum
    summary["claim_tot"] = claim_sum
    if source != "saved":
        summary["imdae_dache"] = dache_sum
    if source != "saved" or _to_int_amt(summary.get("pay_amt")) <= 0:
        summary["pay_amt"] = max(
            0,
            ipkum_sum
            - _to_int_amt(summary.get("man_cost"))
            - _to_int_amt(summary.get("owner_suri"))
            - _to_int_amt(summary.get("jungke_cost"))
            - claim_sum,
        )
    if is_resp:
        # 책임관리: 송금수수료 등으로 실제 송금은 천원 단위 버림 (예: 3,323,333 → 3,323,000)
        summary["pay_amt"] = (_to_int_amt(summary.get("pay_amt")) // 1000) * 1000

    cost_sum = (
        _to_int_amt(summary.get("man_cost"))
        + _to_int_amt(summary.get("owner_suri"))
        + _to_int_amt(summary.get("jungke_cost"))
    )
    summary["cost_sum"] = cost_sum
    summary["as_of_kr"] = (
        f"{as_of.year}년{as_of.month:02d}월{as_of.day:02d}일"
    )

    rows = _jungsan_decorate_rows(rows)

    dache_target_cnt = 0
    dache_target_amt = 0
    for r in rows:
        if r.get("is_empty"):
            continue
        if _to_int_amt(r.get("claim_amt")) > 0:
            rem = 0
        else:
            rem = max(
                0,
                _to_int_amt(r.get("rent_calc") if r.get("rent_calc") is not None else r.get("rent_amt"))
                - _to_int_amt(r.get("ipkum_amt")),
            )
        r["dache_remain"] = rem
        if rem > 0:
            dache_target_cnt += 1
            dache_target_amt += rem

    totals = {
        "tenant_cnt": sum(1 for r in rows if not r.get("is_empty")),
        "bojung_amt": sum(r["bojung_amt"] for r in rows),
        "rent_amt": sum(r["rent_amt"] for r in rows),
        "manage_amt": sum(r["manage_amt"] for r in rows),
        "ipkum_amt": sum(r["ipkum_amt"] for r in rows),
        "misu_amt": sum(r["misu_amt"] for r in rows),
        # 인쇄 합계 행: 보증 만원 합 3,900 / 월세 만원 451 / 관리 81.0
        "bojung_man": sum(int(round(_to_int_amt(r["bojung_amt"]) / 10000)) for r in rows if not r.get("is_empty")),
        "rent_man": sum(int(round(_to_int_amt(r["rent_amt"]) / 10000)) for r in rows if not r.get("is_empty")),
        "manage_man_disp": _fmt_man_dec(sum(r["manage_amt"] for r in rows)),
    }

    undo_rows = _jungsan_dache_rows(b1, b2, as_of)
    dache_undo_cnt = len(undo_rows)
    dache_undo_amt = sum(_to_int_amt(r.get("su_dache_amt")) for r in undo_rows)

    bunji_label = f"{fmt_bunji(b1)}-{fmt_bunji(b2)}"

    return {
        "error": None,
        "source": source,
        "building": building,
        "bunji_label": bunji_label,
        "as_of": as_of_s,
        "month_start": month_start.isoformat(),
        "month_end": month_end_s,
        "summary": summary,
        "rows": rows,
        "totals": totals,
        "suri_detail": suri_detail,
        "is_resp": is_resp,
        "dache_target_cnt": dache_target_cnt,
        "dache_target_amt": dache_target_amt,
        "dache_undo_cnt": dache_undo_cnt,
        "dache_undo_amt": dache_undo_amt,
    }


def _jungsan_request_common():
    today = date.today()
    # 월정기보고: 기본은 전월 말일 (당월은 수금이 아직 없어 지급액 0이 됨)
    default_as_of = (date(today.year, today.month, 1) - timedelta(days=1)).isoformat()
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    as_of_s = (request.args.get("as_of") or "").strip() or default_as_of
    ran = "q" in request.args or (
        bunji1 and bunji2 and ("as_of" in request.args or "bunji1" in request.args)
    )
    data = None
    if ran and bunji1 and bunji2:
        try:
            data = _jungsan_build_preview(bunji1, bunji2, as_of_s)
        except Exception as e:
            data = {"error": f"조회 실패: {e}", "building": None}
    elif ran and (not bunji1 or not bunji2):
        data = {"error": "주소를 입력하세요.", "building": None}
    filters = {"bunji1": bunji1, "bunji2": bunji2, "as_of": as_of_s}
    return filters, ran, data, bunji1, bunji2


def _jungsan_dache_targets(bunji1, bunji2, as_of):
    """당월 임대료 미입금이 남은 입주 (일괄대체 대상). 관리비·선불청구분은 넣지 않음."""
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    if not b1 or not b2:
        return []
    if isinstance(as_of, str):
        as_of = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
    month_start, month_end = _month_bounds(as_of)
    rooms = _jungsan_month_tenants(b1, b2, month_start, month_end)
    targets = []
    for m in rooms or []:
        nm = (m.get("ipju_nm") or "").strip()
        if not nm:
            continue
        hosu = (m.get("hosu") or "").strip()
        seq = str(m.get("ipju_seq") or "").zfill(2)
        out_d = _valid_out_dt(m.get("out_dt"))
        left_this_month = bool(out_d and month_start <= out_d <= month_end)
        nap = str(m.get("napbu_gb") or "B").strip().upper()
        # 선불 당월퇴실: 신규 대체 없음. 기존 대체금이 있으면 화면에서만 청구.
        if left_this_month and nap == "A":
            continue
        rent_calc, claim_amt = _jungsan_month_rent_split(
            m.get("napbu_gb"), m.get("rent_amt"), m.get("ipju_dt"), m.get("out_dt"),
            month_start, month_end,
        )
        if claim_amt > 0 or rent_calc <= 0:
            continue
        remain = _calc_month_misu_amt(
            b1, b2, hosu, seq, rent_calc, 0, as_of, include_dache=True
        )
        if remain <= 0:
            continue
        targets.append(
            {
                "hosu": hosu,
                "ipju_seq": seq,
                "ipju_nm": nm,
                "amt": remain,
            }
        )
    return targets


def _jungsan_dache_rows(bunji1, bunji2, as_of):
    """당월 순수 대체전표(실입 0, 대체금액>0, 수금종류 02)."""
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    if not b1 or not b2:
        return []
    if isinstance(as_of, str):
        as_of = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
    month_start, month_end = _month_bounds(as_of)
    return db.query(
        """
        SELECT sukum_dt, sukum_seq, hosu, su_dache_amt
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s
          AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
          AND sukum_gb='02'
          AND COALESCE(su_dache_amt,0) > 0
          AND COALESCE(su_sil_amt,0) = 0
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
        ORDER BY hosu, sukum_dt, sukum_seq
        """,
        (b1, b2, month_start.isoformat(), month_end.isoformat()),
    ) or []


def _jungsan_redirect(bunji1, bunji2, as_of_s):
    return redirect(
        url_for(
            "jungsan",
            q=1,
            bunji1=fmt_bunji(bunji1) if bunji1 else None,
            bunji2=fmt_bunji(bunji2) if bunji2 else None,
            as_of=as_of_s,
        )
    )


@app.route("/jungsan")
@login_required
def jungsan():
    """
    주소별 정산서 — 조회 시 화면 전체 표시 (인쇄 없이 확인).
    인쇄 양식은 /jungsan/print (508-88.pdf 기준).
    """
    filters, ran, data, bunji1, bunji2 = _jungsan_request_common()
    return render_template(
        "jungsan.html",
        filters=filters,
        ran=ran,
        data=data,
        building_label=_building_label(bunji1, bunji2) if bunji1 and bunji2 else "",
    )


@app.route("/jungsan/dache", methods=["POST"])
@login_required
@require_write_access
def jungsan_dache():
    """미입금 임대료만 일괄 대체처리(sukum01, 수금종류 02). 관리비는 넣지 않음."""
    bunji1 = _pad_bunji(request.form.get("bunji1"))
    bunji2 = _pad_bunji(request.form.get("bunji2"))
    as_of_s = (request.form.get("as_of") or "").strip()
    if not (bunji1 and bunji2 and as_of_s):
        return _jungsan_redirect(bunji1, bunji2, as_of_s)
    try:
        as_of = datetime.strptime(as_of_s[:10], "%Y-%m-%d").date()
    except ValueError:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)

    targets = _jungsan_dache_targets(bunji1, bunji2, as_of)
    if not targets:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)

    uid = session.get("sabun") or ""
    done = 0
    total = 0
    try:
        for t in targets:
            amt = _to_int_amt(t.get("amt"))
            if amt <= 0:
                continue
            sukum_seq = _next_sukum_seq(as_of_s, bunji1, bunji2, t["hosu"])
            db.execute(
                """
                INSERT INTO sukum01 (
                    sukum_dt, sukum_seq, bunji1, bunji2, hosu, ipju_seq,
                    sukum_char, sukum_gb, manage_desc, su_sil_amt, su_dache_amt,
                    suri_dt, suri_seq, s_method, del_yn, sys_dt, uid
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    NULL, '', '', 'N', NOW(), %s
                )
                """,
                (
                    as_of_s + " 00:00:00",
                    sukum_seq,
                    bunji1,
                    bunji2,
                    t["hosu"],
                    t["ipju_seq"],
                    "01",
                    "02",
                    "",
                    0,
                    amt,
                    uid,
                ),
            )
            done += 1
            total += amt
    except Exception:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)

    return _jungsan_redirect(bunji1, bunji2, as_of_s)


@app.route("/jungsan/dache/undo", methods=["POST"])
@login_required
@require_write_access
def jungsan_dache_undo():
    """당월 대체전표를 삭제(del_yn='Y')해서 미수로 되돌림."""
    bunji1 = _pad_bunji(request.form.get("bunji1"))
    bunji2 = _pad_bunji(request.form.get("bunji2"))
    as_of_s = (request.form.get("as_of") or "").strip()
    if not (bunji1 and bunji2 and as_of_s):
        return _jungsan_redirect(bunji1, bunji2, as_of_s)
    try:
        as_of = datetime.strptime(as_of_s[:10], "%Y-%m-%d").date()
    except ValueError:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)

    rows = _jungsan_dache_rows(bunji1, bunji2, as_of)
    if not rows:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)

    uid = session.get("sabun") or ""
    done = 0
    total = 0
    try:
        for r in rows:
            dt = fmt_date(r.get("sukum_dt")) or as_of_s
            seq = str(r.get("sukum_seq") or "").strip()
            hosu = (r.get("hosu") or "").strip()
            if not (dt and seq and hosu):
                continue
            n = db.execute(
                """
                UPDATE sukum01
                   SET del_yn='Y', uid=%s, sys_dt=NOW()
                 WHERE sukum_dt >= %s AND sukum_dt < %s + INTERVAL 1 DAY
                   AND sukum_seq=%s
                   AND bunji1=%s AND bunji2=%s
                   AND UPPER(TRIM(hosu))=%s
                   AND sukum_gb='02'
                   AND COALESCE(su_dache_amt,0) > 0
                   AND COALESCE(su_sil_amt,0) = 0
                   AND (del_yn IS NULL OR del_yn='' OR del_yn='N')
                """,
                (
                    uid,
                    dt + " 00:00:00",
                    dt,
                    seq,
                    bunji1,
                    bunji2,
                    hosu.upper(),
                ),
            )
            if n:
                done += int(n)
                total += _to_int_amt(r.get("su_dache_amt"))
    except Exception:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)

    return _jungsan_redirect(bunji1, bunji2, as_of_s)


@app.route("/jungsan/print")
@login_required
def jungsan_print():
    """
    결산현황 인쇄 양식 — Downloads/508-88.pdf 와 동일 구조.
    제목: {주소} 결산현황 / 표·하단 수수료·당월지급액·수리내역
    """
    filters, ran, data, bunji1, bunji2 = _jungsan_request_common()
    if not ran or not data or data.get("error") or not data.get("building"):
        # 조건 부족 시 조회로 유도
        return redirect(
            url_for(
                "jungsan",
                q=1 if bunji1 and bunji2 else None,
                bunji1=fmt_bunji(bunji1) if bunji1 else None,
                bunji2=fmt_bunji(bunji2) if bunji2 else None,
                as_of=filters["as_of"],
            )
        )
    return render_template(
        "jungsan_print.html",
        filters=filters,
        data=data,
    )


@app.route("/jungsan/list")
@login_required
def jungsan_list():
    """
    월별 정산서 조회 (XP「정산 현황 조회」).
    기준년월(+주소)로 jungsan_m 목록 → 행 클릭 시 결산 상세/인쇄.
    """
    today = date.today()
    prev = date(today.year, today.month, 1) - timedelta(days=1)
    try:
        y = int(request.args.get("year") or prev.year)
    except ValueError:
        y = prev.year
    try:
        m = int(request.args.get("month") or prev.month)
    except ValueError:
        m = prev.month
    if m < 1 or m > 12:
        m = prev.month
    if y < 1990 or y > 2100:
        y = prev.year

    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    ran = True

    month_start = date(y, m, 1)
    month_end = date(y, m, monthrange(y, m)[1])
    results = []
    sum_pay = 0

    if ran:
        b_where, b_args = [], []
        if bunji1:
            b_where.append("bunji1=%s")
            b_args.append(bunji1)
        if bunji2:
            b_where.append("bunji2=%s")
            b_args.append(bunji2)
        bsql = "SELECT bunji1, bunji2, juso, owner_nm, mgmt_gb, man_cost FROM bd01"
        if b_where:
            bsql += " WHERE " + " AND ".join(b_where)
        bsql += " ORDER BY bunji1, bunji2"
        buildings = db.query(bsql, b_args)

        saved_rows = db.query(
            """
            SELECT j.*, b.juso, b.owner_nm, b.mgmt_gb, b.man_cost AS b_man_cost
            FROM jungsan_m j
            LEFT JOIN bd01 b ON b.bunji1=j.bunji1 AND b.bunji2=j.bunji2
            WHERE (j.del_yn IS NULL OR j.del_yn='N' OR j.del_yn='')
              AND j.jungsan_dt >= %s AND j.jungsan_dt < DATE_ADD(%s, INTERVAL 1 DAY)
            ORDER BY j.bunji1, j.bunji2, j.jungsan_seq
            """,
            (month_start.isoformat(), month_end.isoformat()),
        )
        saved_by = {}
        for r in saved_rows or []:
            saved_by[(r.get("bunji1"), r.get("bunji2"))] = r

        for b in buildings or []:
            key = (b.get("bunji1"), b.get("bunji2"))
            r = saved_by.get(key)
            if r:
                pay = _to_int_amt(r.get("pay_amt"))
                mgmt_gb = (r.get("mgmt_gb") or "").strip().upper()
                is_resp = (
                    mgmt_gb == "R" if mgmt_gb in ("R", "G")
                    else _to_int_amt(r.get("b_man_cost")) > 0
                )
                if is_resp:
                    pay = (pay // 1000) * 1000
                sum_pay += pay
                results.append(
                    {
                        "bunji1": r.get("bunji1"),
                        "bunji2": r.get("bunji2"),
                        "jungsan_dt": r.get("jungsan_dt"),
                        "jungsan_seq": r.get("jungsan_seq"),
                        "juso": r.get("juso") or "",
                        "owner_nm": r.get("owner_nm") or "",
                        "pay_amt": pay,
                        "first_amt": r.get("first_amt"),
                        "bojung_tot": r.get("bojung_tot"),
                        "rent_tot": r.get("rent_tot"),
                        "manage_tot": r.get("manage_tot"),
                        "ipkum_tot": r.get("ipkum_tot"),
                        "owner_suri": r.get("owner_suri"),
                        "jungke_cost": r.get("jungke_cost"),
                        "misu_tot": r.get("misu_tot"),
                        "man_cost": r.get("man_cost"),
                        "as_of": fmt_date(r.get("jungsan_dt")) or month_end.isoformat(),
                    }
                )
                continue
            live = _jungsan_build_preview(
                b.get("bunji1"), b.get("bunji2"), month_end.isoformat()
            )
            s = (live or {}).get("summary") or {}
            pay = _to_int_amt(s.get("pay_amt"))
            sum_pay += pay
            results.append(
                {
                    "bunji1": b.get("bunji1"),
                    "bunji2": b.get("bunji2"),
                    "jungsan_dt": month_end,
                    "jungsan_seq": None,
                    "juso": b.get("juso") or "",
                    "owner_nm": b.get("owner_nm") or "",
                    "pay_amt": pay,
                    "first_amt": s.get("first_amt"),
                    "bojung_tot": s.get("bojung_tot"),
                    "rent_tot": s.get("rent_tot"),
                    "manage_tot": s.get("manage_tot"),
                    "ipkum_tot": s.get("ipkum_tot"),
                    "owner_suri": s.get("owner_suri"),
                    "jungke_cost": s.get("jungke_cost"),
                    "misu_tot": s.get("misu_tot"),
                    "man_cost": s.get("man_cost"),
                    "as_of": month_end.isoformat(),
                }
            )

    years = list(range(today.year, today.year - 15, -1))
    pager = None
    if ran and results:
        results, pager = _paginate(results)
    return render_template(
        "jungsan_list.html",
        filters={
            "year": y,
            "month": m,
            "bunji1": bunji1,
            "bunji2": bunji2,
        },
        years=years,
        results=results,
        ran=ran,
        sum_pay=sum_pay,
        month_label=f"{y}-{m:02d}",
        pager=pager,
    )
