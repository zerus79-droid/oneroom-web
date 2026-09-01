"""월정기보고(정산) 화면.

주소별 정산서 작성/미리보기, 결산현황 인쇄, 월별 정산서 목록 조회
라우트와 그 전용 도우미 함수들을 모아둔 모듈입니다.
"""
import math
import re
from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    building_label as _building_label,
    calc_misu_amt as _calc_misu_amt,
    calc_contract_period_charge as _calc_contract_period_charge,
    fmt_bunji,
    fmt_date,
    fmt_ipju_short as _fmt_ipju_short,
    is_common_hosu as _is_common_hosu,
    login_required,
    money,
    next_sukum_seq as _next_sukum_seq,
    pad_bunji as _pad_bunji,
    paginate as _paginate,
    require_write_access,
    to_int_amt as _to_int_amt,
)


_G_COST_COLS_READY = False
_MONTH_ADJUSTMENT_TABLE_READY = False
_SJUNGKE_HOSU_READY = False


def _is_resp_building(building):
    if not building:
        return False
    mgmt_gb = (building.get("mgmt_gb") or "").strip().upper()
    if mgmt_gb in ("R", "G"):
        return mgmt_gb == "R"
    return _to_int_amt(building.get("man_cost")) > 0


def _is_manager_account(building):
    if not building:
        return False
    key = (building.get("sukum_acct_gb") or "").strip().upper()
    if key in ("M", "O"):
        return key == "M"
    return _is_resp_building(building)


def _is_item_manager_account(building, item):
    """항목별 수금통장 주체. 기존 자료는 일반 수금통장으로 대체한다."""
    key = (building.get(f"sukum_{item}_acct_gb") or building.get("sukum_acct_gb") or "").strip().upper()
    return key == "M"


def _exit_settlement_misu(b1, b2, hosu, seq, tenant, out_d):
    """퇴실일 기준 미수. 확정 자료가 있으면 그 스냅숏을 그대로 사용한다."""
    if not out_d:
        return None
    # 확정 퇴실정산은 bd07_out의 계산 기준·수금합계를 우선한다.
    snap = db.query_one(
        """SELECT ipkum_gijun, sukum_tot
             FROM bd07_out
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
              AND ipju_seq=%s AND out_dt=%s
            ORDER BY CAST(out_seq AS UNSIGNED) DESC
            LIMIT 1""",
        (b1, b2, (hosu or "").strip().upper(), str(seq or "").zfill(2), out_d),
    )
    if snap and snap.get("ipkum_gijun") is not None:
        return _to_int_amt(snap.get("ipkum_gijun")) - _to_int_amt(snap.get("sukum_tot"))

    ipju_d = tenant.get("ipju_dt") if tenant else None
    rent = _to_int_amt((tenant or {}).get("rent_amt"))
    manage = _to_int_amt((tenant or {}).get("manage_amt"))
    charge = _calc_contract_period_charge(
        b1, b2, hosu, str(seq or "").zfill(2), ipju_d, out_d, rent, manage
    )
    # 퇴실정산 조정(월세·관리비 감면)은 계약 누적 청구액에서 차감한다.
    try:
        adj = db.query_one(
            """SELECT COALESCE(SUM(COALESCE(adj_amt,0)),0) AS amt
                 FROM jungsan_adjustment
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                  AND ipju_seq=%s AND adj_month >= %s AND adj_month <= %s
                  AND adj_kind IN ('RENT_DISCOUNT','RENT_WAIVE',
                                   'MANAGE_DISCOUNT','MANAGE_WAIVE')
                  AND del_yn='N'""",
            (
                b1, b2, (hosu or "").strip().upper(), str(seq or "").zfill(2),
                _to_date(ipju_d).replace(day=1).isoformat() if _to_date(ipju_d) else out_d,
                out_d.replace(day=1).isoformat(),
            ),
        )
        charge -= max(0, _to_int_amt((adj or {}).get("amt")))
    except Exception:
        pass
    paid = db.query_one(
        """SELECT COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS sil,
                  COALESCE(SUM(COALESCE(su_dache_amt,0)),0) AS dache
             FROM sukum01
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
              AND ipju_seq=%s AND sukum_char='01'
              AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
              AND (del_yn IS NULL OR del_yn='N' OR del_yn='')""",
        (b1, b2, (hosu or "").strip().upper(), str(seq or "").zfill(2), out_d),
    )
    return charge - _to_int_amt((paid or {}).get("sil")) - _to_int_amt((paid or {}).get("dache"))


def _ensure_g_cost_cols():
    global _G_COST_COLS_READY
    if _G_COST_COLS_READY:
        return
    for col in ("stair_cost", "inet_cost", "option_cost"):
        try:
            db.execute(
                f"ALTER TABLE bd01 ADD COLUMN {col} decimal(18,0) NULL DEFAULT 0"
            )
        except Exception:
            pass
    for col in (
        "sukum_acct_gb",
        "sukum_bojung_acct_gb",
        "sukum_rent_acct_gb",
        "sukum_manage_acct_gb",
    ):
        try:
            db.execute(f"ALTER TABLE bd01 ADD COLUMN {col} char(1) NULL DEFAULT NULL")
        except Exception:
            pass
    # 신규 항목별 값이 비어 있는 기존 건물은 레거시 일반 수금통장을 상속한다.
    for col in (
        "sukum_bojung_acct_gb",
        "sukum_rent_acct_gb",
        "sukum_manage_acct_gb",
    ):
        try:
            db.execute(
                f"UPDATE bd01 SET {col}=sukum_acct_gb "
                f"WHERE ({col} IS NULL OR TRIM({col})='') "
                "AND sukum_acct_gb IS NOT NULL"
            )
        except Exception:
            pass
    db.execute(
        """UPDATE bd01 SET sukum_acct_gb=CASE WHEN COALESCE(mgmt_gb,'R')='G' THEN 'O' ELSE 'M' END
           WHERE sukum_acct_gb IS NULL OR TRIM(sukum_acct_gb)=''"""
    )
    _G_COST_COLS_READY = True


def _g_extra_costs(building, is_resp):
    """일반관리 기초관리 추가비용. 책임관리는 0."""
    if is_resp or not building:
        return 0, 0, 0
    return (
        _to_int_amt(building.get("stair_cost")),
        _to_int_amt(building.get("inet_cost")),
        _to_int_amt(building.get("option_cost")),
    )


def _ensure_month_adjustment_table():
    global _MONTH_ADJUSTMENT_TABLE_READY
    if _MONTH_ADJUSTMENT_TABLE_READY:
        return
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS jungsan_adjustment (
          adj_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
          adj_month DATE NOT NULL,
          bunji1 CHAR(4) NOT NULL, bunji2 CHAR(4) NOT NULL,
          hosu CHAR(3) NOT NULL, ipju_seq CHAR(2) NOT NULL,
          adj_kind VARCHAR(24) NOT NULL,
          adj_amt DECIMAL(18,0) NOT NULL DEFAULT 0,
          burden_gb CHAR(1) NOT NULL DEFAULT 'O',
          reason VARCHAR(200) NOT NULL DEFAULT '',
          del_yn CHAR(1) NOT NULL DEFAULT 'N',
          uid CHAR(5) NOT NULL DEFAULT '', sys_dt DATETIME NOT NULL,
          PRIMARY KEY (adj_id),
          KEY ix_js_adj_month_tenant (adj_month,bunji1,bunji2,hosu,ipju_seq)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    try:
        db.execute("ALTER TABLE jungsan_adjustment DROP INDEX ux_js_adj_month_tenant")
    except Exception:
        pass
    try:
        db.execute(
            "ALTER TABLE jungsan_adjustment ADD INDEX ix_js_adj_month_tenant (adj_month,bunji1,bunji2,hosu,ipju_seq)"
        )
    except Exception:
        pass
    _MONTH_ADJUSTMENT_TABLE_READY = True


_ADJ_LABELS = {
    "RENT_DISCOUNT": "월세감면", "RENT_WAIVE": "월세면제",
    "MANAGE_DISCOUNT": "관리비감면", "MANAGE_WAIVE": "관리비면제",
    "OTHER": "기타조정",
}


def _month_adjustment_map(b1, b2, month_start):
    _ensure_month_adjustment_table()
    rows = db.query(
        """SELECT adj_id,hosu,ipju_seq,adj_kind,adj_amt,burden_gb,reason,sys_dt
           FROM jungsan_adjustment
           WHERE adj_month=%s AND bunji1=%s AND bunji2=%s AND del_yn='N'
           ORDER BY hosu,ipju_seq,adj_id""",
        (month_start.isoformat(), b1, b2),
    )
    result = {}
    for r in rows or []:
        key = ((r.get("hosu") or "").strip().upper(), str(r.get("ipju_seq") or "").zfill(2))
        result.setdefault(key, []).append(r)
    return result


def _apply_month_adjustments(rows, b1, b2, month_start):
    amap = _month_adjustment_map(b1, b2, month_start)
    for r in rows:
        if r.get("is_empty"):
            continue
        adjs = amap.get(((r.get("hosu") or "").strip().upper(), str(r.get("ipju_seq") or "").zfill(2))) or []
        if isinstance(adjs, dict):
            adjs = [adjs]
        r["adjustment_items"] = adjs
        r["adjustment_kind"] = "MULTI" if len(adjs) > 1 else ((adjs[0].get("adj_kind") or "") if adjs else "")
        r["adjustment_amt"] = sum(_to_int_amt(a.get("adj_amt")) for a in adjs)
        r["adjustment_rent_amt"] = sum(
            _to_int_amt(a.get("adj_amt")) for a in adjs
            if str(a.get("adj_kind") or "").startswith("RENT_")
        )
        r["company_pay_amt"] = 0
        r["company_comp_amt"] = 0
        if not adjs or r["adjustment_amt"] <= 0:
            continue
        r["misu_amt"] = max(0, _to_int_amt(r.get("misu_amt")) - r["adjustment_amt"])
        rent_adj = r["adjustment_rent_amt"]
        if rent_adj > 0:
            rent = _to_int_amt(
                r.get("rent_calc") if r.get("rent_calc") is not None else r.get("rent_amt")
            )
            rent_due = max(0, rent - rent_adj)
            sil = _to_int_amt(r.get("sil_amt"))
            raw_dache = _to_int_amt(r.get("dache_amt"))
            r["dache_amt_raw"] = raw_dache
            if raw_dache > 0:
                r["dache_amt"] = min(raw_dache, max(0, rent_due - sil))
            r["dache_gb"] = _dache_flag(sil, r.get("dache_amt"), rent_due)
        r["company_pay_amt"] = sum(
            _to_int_amt(a.get("adj_amt")) for a in adjs
            if (a.get("burden_gb") or "O") == "C" and str(a.get("adj_kind") or "").startswith("RENT_")
        )
        r["company_comp_amt"] = sum(
            _to_int_amt(a.get("adj_amt")) for a in adjs if (a.get("burden_gb") or "O") == "C"
        )
    return rows


def _bojung_disp_amt(bojung_amt, yechi_amt, is_resp):
    """보증금 칸 금액. 일반관리는 보증이 없으면 예치금을 쓴다."""
    bojung = _to_int_amt(bojung_amt)
    yechi = _to_int_amt(yechi_amt)
    if is_resp:
        return bojung
    return bojung if bojung > 0 else yechi


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
            r["wolse_disp"] = (
                "-" if _to_int_amt(r.get("rent_amt")) <= 0
                else _fmt_wolse_cell(r.get("napbu_gb"), r.get("rent_amt"))
            )
            r["manage_disp"] = _fmt_man_dec(r.get("manage_amt")) if _to_int_amt(r.get("manage_amt")) or _to_int_amt(r.get("rent_amt")) else ""
            if r.get("is_exit"):
                r["bojung_disp"] = ""
                r["wolse_disp"] = ""
                r["manage_disp"] = ""
            r["misu_disp"] = money(r.get("misu_amt")) if _to_int_amt(r.get("misu_amt")) else ""
            jisi = (r.get("manage_desc") or "").strip()
            dache = str(r.get("dache_gb") or "").strip()
            sil = _to_int_amt(r.get("sil_amt"))
            dache_amt = _to_int_amt(r.get("dache_amt"))
            rent = _to_int_amt(
                r.get("rent_calc") if r.get("rent_calc") is not None else r.get("rent_amt")
            )
            is_dache = ("대체" in dache) or (dache.upper() in ("Y", "1"))
            if is_dache and rent > 0 and sil >= rent:
                is_dache = False
            r["dache_gb"] = "대체" if is_dache else ""
            sil_in = _to_int_amt(r.get("ipkum_amt"))
            if is_dache:
                put = _rent_ipkum_for_pay(sil, dache_amt, rent)
                if put <= 0:
                    put = rent
                r["ipkum_disp"] = money(put) if put else ""
            elif sil_in != 0:
                r["ipkum_disp"] = money(sil_in)
            else:
                r["ipkum_disp"] = ""
            if jisi.startswith("퇴실("):
                r["manage_desc"] = jisi
                r["jisi_disp"] = jisi
            elif any(jisi.startswith(label) for label in _ADJ_LABELS.values()):
                rent_due = rent
                if str(r.get("adjustment_kind") or "").startswith("RENT_"):
                    rent_due = max(0, rent - _to_int_amt(r.get("adjustment_amt")))
                status = ""
                if is_dache:
                    status = "대체"
                elif rent_due > 0 and sil <= 0:
                    status = "미납"
                elif rent_due > 0 and sil < rent_due:
                    status = "부족"
                detail = f"{jisi} ({status})" if status else jisi
                r["manage_desc"] = detail
                r["jisi_disp"] = detail
            elif jisi == "청구":
                r["manage_desc"] = "청구"
                r["jisi_disp"] = "청구"
            elif is_dache:
                # 임대료가 대체됐으면 관리지시의 미납/부족 표시는 지운다.
                # 관리비는 대체 대상이 아니므로 대체금 계산에는 포함하지 않는다.
                r["manage_desc"] = "(대체)"
                r["jisi_disp"] = "(대체)"
            elif sil <= 0 and rent > 0:
                r["manage_desc"] = "미납"
                r["jisi_disp"] = "미납"
            elif rent > 0 and sil < rent:
                r["manage_desc"] = "부족"
                r["jisi_disp"] = "부족"
            else:
                r["manage_desc"] = ""
                r["jisi_disp"] = ""
    return rows


def _jungsan_build_preview(bunji1, bunji2, as_of, *, list_mode=False):
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

    _ensure_g_cost_cols()
    building = db.query_one(
        """
        SELECT bunji1, bunji2, juso, owner_nm, first_amt, man_cost, mgmt_gb, sukum_acct_gb,
               sukum_bojung_acct_gb, sukum_rent_acct_gb, sukum_manage_acct_gb,
               stair_cost, inet_cost, option_cost
        FROM bd01 WHERE bunji1=%s AND bunji2=%s
        """,
        (b1, b2),
    )
    if not building:
        return {"error": "미등록 주소입니다.", "building": None}

    # 관리형태(mgmt_gb): 책임관리(R)/일반관리(G).
    # 대체는 실입 없는 호의 임대료만. 일반관리도 예외 선택 가능.
    # 옛 건물 중 관리형태 미지정분은 관리수수료 유무로 추정(과거 로직 호환).
    is_resp = _is_resp_building(building)
    # 월세 수금통장이 월정산의 기본 방향을 결정한다.
    manager_account = _is_item_manager_account(building, "rent")

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
            trow = None
            if not empty and d.get("ipju_seq"):
                trow = db.query_one(
                    """
                    SELECT napbu_gb, yechi_amt, bojung_amt, out_dt, out_jungsan_end FROM bd03_det
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
            yechi = _to_int_amt((trow or {}).get("yechi_amt")) if not empty else 0
            rows.append(
                {
                    "hosu": d.get("hosu"),
                    "ipju_nm": "공실" if empty else nm,
                    "ipju_dt": d.get("ipju_dt"),
                    "out_dt": (trow or {}).get("out_dt") if not empty else None,
                    "ipju_seq": d.get("ipju_seq"),
                    "napbu_gb": nap,
                    "yechi_amt": yechi,
                    "bojung_amt": _bojung_disp_amt(d.get("bojung_amt"), yechi, is_resp),
                    "rent_amt": _to_int_amt(d.get("rent_amt")),
                    "manage_amt": _to_int_amt(d.get("manage_amt")),
                    "ipkum_amt": 0,
                    "sil_amt": 0,
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
            sil_amt, dache_amt = _month_sukum_sil_dache(
                b1, b2, r.get("hosu"), r.get("ipju_seq"), month_start, month_end_s
            )
            out_d = _valid_out_dt(r.get("out_dt"))
            out_adj_exists = False
            out_adj_amt = 0
            out_adj_desc = ""
            if out_d and month_start <= out_d <= month_end:
                out_adj_exists, out_adj_amt, out_adj_desc = _month_out_adjustment(
                    b1, b2, r.get("hosu"), r.get("ipju_seq"), month_start, month_end_s
                )
            r["sil_amt"] = sil_amt
            r["dache_amt"] = dache_amt
            r["out_dt"] = out_d
            r["is_exit"] = bool(out_d and month_start <= out_d <= month_end)
            # 퇴실정산 금액을 월정산 입금액에 다시 더하지 않는다.
            r["out_settle_amt"] = None
            r["ipkum_amt"] = sil_amt + dache_amt
            if out_d and month_start <= out_d <= month_end:
                exit_tenant = db.query_one(
                    """SELECT ipju_dt, rent_amt, manage_amt, napbu_gb
                         FROM bd03_det
                        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                          AND ipju_seq=%s""",
                    (
                        b1, b2, (r.get("hosu") or "").strip().upper(),
                        str(r.get("ipju_seq") or "").zfill(2),
                    ),
                ) or {
                    "ipju_dt": r.get("ipju_dt"),
                    "rent_amt": r.get("rent_amt"),
                    "manage_amt": r.get("manage_amt"),
                    "napbu_gb": r.get("napbu_gb"),
                }
                exit_misu = _exit_settlement_misu(
                    b1, b2, r.get("hosu"), r.get("ipju_seq"), exit_tenant, out_d
                )
                if exit_misu is not None or out_adj_exists:
                    # 퇴실자는 누적 미수가 아니라 당월 퇴실정산 수금(종류 06)을
                    # 입금액으로 표시한다. 기존 누적 미수는 퇴실정산에서 정리된다.
                    r["ipkum_amt"] = _to_int_amt(out_adj_amt) if out_adj_exists else 0
                    r["misu_amt"] = 0
            if out_d:
                r["manage_desc"] = out_adj_desc or f"퇴실({out_d.strftime('%m-%d')})"
            r["dache_gb"] = _dache_flag(
                sil_amt, dache_amt, r.get("rent_calc") if r.get("rent_calc") is not None else r.get("rent_amt")
            )
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
            "rent_manager_account": _is_item_manager_account(building, "rent"),
            "manage_manager_account": _is_item_manager_account(building, "manage"),
            "bojung_manager_account": _is_item_manager_account(building, "bojung"),
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
                        "sil_amt": 0,
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
            bojung = _bojung_disp_amt(m.get("bojung_amt"), m.get("yechi_amt"), is_resp)
            yechi = _to_int_amt(m.get("yechi_amt"))
            pay_parts = _month_sukum_breakdown(
                b1, b2, hosu, seq, month_start, month_end_s
            )
            rent_part = pay_parts.get("01", {"sil": 0, "dache": 0})
            rent_sil = _to_int_amt(rent_part.get("sil"))
            rent_dache = _to_int_amt(rent_part.get("dache"))
            combined_paid = rent_sil + rent_dache
            # 기존 수금 입력은 01(월세+관리비)을 한 금액으로 저장하므로,
            # 계약 월세를 먼저 충당하고 남은 금액을 관리비로 배분한다.
            rent_due_for_split = rent
            rent_paid = min(combined_paid, rent_due_for_split) if rent_due_for_split > 0 else 0
            manage_paid = min(
                max(0, combined_paid - rent_paid), manage
            ) if manage > 0 else 0
            sil_amt, dache_amt = rent_sil, rent_dache
            out_d = _valid_out_dt(m.get("out_dt"))
            out_adj_exists = False
            out_adj_desc = ""
            if out_d and month_start <= out_d <= month_end:
                out_adj_exists, out_adj_amt, out_adj_desc = _month_out_adjustment(
                    b1, b2, hosu, seq, month_start, month_end_s
                )
            # 퇴실월에는 별도의 79,000원 추정 조정액을 입금액으로 넣지 않는다.
            # 퇴실정산 확정액(또는 누적 미수)은 아래 exit_misu로 반영한다.
            out_settle_amt = None
            ipkum = sil_amt + dache_amt
            is_exit = bool(out_d and month_start <= out_d <= month_end)
            misu = _calc_misu_amt(
                b1, b2, hosu, seq, rent, manage, m.get("ipju_dt"), as_of=as_of
            )
            exit_misu = None
            if out_d and month_start <= out_d <= month_end:
                exit_misu = _exit_settlement_misu(b1, b2, hosu, seq, m, out_d)
                if exit_misu is not None or out_adj_exists:
                    # 누적 미수는 표시하지 않고 당월 퇴실정산 수금(종류 06)만 표시한다.
                    ipkum = _to_int_amt(out_adj_amt) if out_adj_exists else 0
                    misu = 0
            rent_calc, claim_raw = _jungsan_month_rent_split(
                m.get("napbu_gb"), rent, m.get("ipju_dt"), m.get("out_dt"),
                month_start, month_end,
            )
            # 선불 퇴실 청구는 대체금이 있을 때만. 대체 없으면 청구 없음.
            claim_amt = min(claim_raw, dache_amt) if (claim_raw > 0 and dache_amt > 0 and sil_amt <= 0) else 0
            dache_gb = _dache_flag(sil_amt, dache_amt, rent_calc)
            if out_d and month_start <= out_d <= month_end:
                claim_amt = 0
                jisi = out_adj_desc or f"퇴실({out_d.strftime('%m-%d')})"
            elif claim_amt > 0:
                jisi = "청구"
            elif sil_amt <= 0 and rent_calc > 0:
                jisi = "미납"
            elif rent_calc > 0 and sil_amt < rent_calc:
                jisi = "부족"
            else:
                jisi = ""
            rows.append(
                {
                    "hosu": hosu,
                    "ipju_nm": nm,
                    "ipju_dt": m.get("ipju_dt"),
                    "out_dt": m.get("out_dt"),
                    "is_exit": is_exit,
                    "ipju_seq": seq,
                    "napbu_gb": m.get("napbu_gb") or "B",
                    "yechi_amt": yechi,
                    "bojung_amt": bojung,
                    "rent_amt": rent,
                    "manage_amt": manage,
                    "ipkum_amt": ipkum,
                    "sil_amt": sil_amt,
                    "rent_sil_amt": rent_paid,
                    "manage_sil_amt": manage_paid,
                    "deposit_sil_amt": _to_int_amt(pay_parts.get("02", {}).get("sil"))
                    + _to_int_amt(pay_parts.get("03", {}).get("sil")),
                    "deposit_dache_amt": _to_int_amt(pay_parts.get("02", {}).get("dache"))
                    + _to_int_amt(pay_parts.get("03", {}).get("dache")),
                    "repair_sil_amt": _to_int_amt(pay_parts.get("04", {}).get("sil")),
                    "brokerage_sil_amt": _to_int_amt(pay_parts.get("05", {}).get("sil")),
                    "exit_rent_sil_amt": _to_int_amt(pay_parts.get("06", {}).get("sil")),
                    "exit_manage_sil_amt": _to_int_amt(pay_parts.get("07", {}).get("sil")),
                    "manage_desc": jisi,
                    "dache_gb": dache_gb,
                    "dache_rent": 0,
                    "dache_amt": dache_amt,
                    "rent_calc": rent_calc,
                    "claim_amt": claim_amt,
                    "out_settle_amt": out_settle_amt,
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
        # 지급액은 아래 공통 구간에서 월세분으로 다시 계산.
        dache_sum = sum(_to_int_amt(r.get("dache_amt")) for r in rows)
        claim_sum = sum(_to_int_amt(r.get("claim_amt")) for r in rows)
        pay_amt = 0
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
            "rent_manager_account": _is_item_manager_account(building, "rent"),
            "manage_manager_account": _is_item_manager_account(building, "manage"),
            "bojung_manager_account": _is_item_manager_account(building, "bojung"),
            "claim_tot": claim_sum,
            "note": "",
            "jungsan_dt": None,
            "jungsan_seq": None,
            "tenant_cnt": tenant_cnt,
        }

        # 수금 성격별 집계(01 월세+관리비를 계약금액 기준으로 분배).
        summary["rent_ipkum_tot"] = sum(_to_int_amt(r.get("rent_sil_amt")) for r in rows)
        summary["manage_ipkum_tot"] = sum(_to_int_amt(r.get("manage_sil_amt")) for r in rows)
        summary["rent_dache_tot"] = sum(
            min(_to_int_amt(r.get("dache_amt")), _to_int_amt(r.get("rent_amt")))
            for r in rows if not r.get("is_empty")
        )
        summary["deposit_ipkum_tot"] = sum(_to_int_amt(r.get("deposit_sil_amt")) for r in rows)
        summary["deposit_dache_tot"] = sum(_to_int_amt(r.get("deposit_dache_amt")) for r in rows)

    def _suri_amt_disp(s):
        won = _to_int_amt(s.get("suri_won_amt"))
        owner = _to_int_amt(s.get("owner_budam"))
        gwan = _to_int_amt(s.get("manage_budam"))
        se = _to_int_amt(s.get("ipjuja_budam"))
        total = won or (owner + gwan + se)
        bits = []
        if owner:
            bits.append(f"건({money(owner)})")
        if gwan:
            bits.append(f"관({money(gwan)})")
        if se:
            bits.append(f"세({money(se)})")
        if not total and not bits:
            return 0, ""
        # 건물주만 있으면 총액만. 관/세가 있을 때만 [건(),관(),세()]
        if not bits or (len(bits) == 1 and owner and not gwan and not se):
            return total, money(total)
        return total, f"{money(total)} [{','.join(bits)}]"

    # 수리 내역 줄 (인쇄 하단) — 건물주 부담 있거나 「정산서에 출력」인 건
    if not list_mode:
        try:
            from repair import _ensure_js_print_col
            _ensure_js_print_col()
        except Exception:
            pass
    suri_lines = (
        []
        if list_mode
        else db.query(
            """
            SELECT suri_dt, suri_won_amt, owner_budam, manage_budam, ipjuja_budam,
                   suri_desc, hosu, js_print_yn
            FROM bd05_suri
            WHERE bunji1=%s AND bunji2=%s
              AND suri_dt >= %s AND suri_dt < DATE_ADD(%s, INTERVAL 1 DAY)
            ORDER BY suri_dt, suri_seq
            """,
            (b1, b2, month_start.isoformat(), month_end_s),
        )
    )
    suri_detail = []
    for s in suri_lines or []:
        owner_amt = _to_int_amt(s.get("owner_budam"))
        force_print = str(s.get("js_print_yn") or "").strip().upper() == "Y"
        if owner_amt <= 0 and not force_print:
            continue
        amt, amt_disp = _suri_amt_disp(s)
        if amt <= 0 and not amt_disp:
            continue
        hosu = (s.get("hosu") or "").strip()
        if _is_common_hosu(hosu):
            hosu_lab = "공용"
        elif hosu:
            hosu_lab = f"{hosu}호"
        else:
            hosu_lab = ""
        suri_detail.append(
            {
                "dt": _fmt_ipju_short(s.get("suri_dt")),
                "amt": amt,
                "amt_disp": amt_disp,
                "desc": f"{hosu_lab} {(s.get('suri_desc') or '').strip()}".strip(),
            }
        )

    # 중개보수 내역 줄 (인쇄 하단, 수리 다음)
    if not list_mode:
        global _SJUNGKE_HOSU_READY
        if not _SJUNGKE_HOSU_READY:
            try:
                db.execute(
                    "ALTER TABLE sjungke01 ADD COLUMN hosu char(3) NOT NULL DEFAULT ''"
                )
            except Exception:
                pass
            _SJUNGKE_HOSU_READY = True
    jungke_lines = (
        []
        if list_mode
        else db.query(
            """
            SELECT jungke_dt, jungke_desc, jungke_amt, hosu
            FROM sjungke01
            WHERE bunji1=%s AND bunji2=%s
              AND jungke_dt >= %s AND jungke_dt < DATE_ADD(%s, INTERVAL 1 DAY)
            ORDER BY jungke_dt, jungke_seq
            """,
            (b1, b2, month_start.isoformat(), month_end_s),
        )
    )
    jungke_detail = []
    for j in jungke_lines or []:
        amt = _to_int_amt(j.get("jungke_amt"))
        hosu = (j.get("hosu") or "").strip()
        desc = (j.get("jungke_desc") or "").strip()
        if not hosu:
            m = re.match(r"^\s*([Bb]?\d+)\s*호", desc)
            hosu = m.group(1).upper() if m else ""
        if _is_common_hosu(hosu):
            desc = "공용"
        elif hosu:
            desc = f"{hosu}호"
        elif not desc:
            desc = ""
        if amt <= 0 and not desc:
            continue
        jungke_detail.append(
            {
                "dt": _fmt_ipju_short(j.get("jungke_dt")),
                "amt": amt,
                "amt_disp": money(amt) if amt else "",
                "desc": desc,
            }
        )

    _apply_month_adjustments(rows, b1, b2, month_start)
    ipkum_sum = sum(_to_int_amt(r.get("ipkum_amt")) for r in rows)
    dache_sum = sum(
        _to_int_amt(r.get("dache_amt"))
        for r in rows
        if str(r.get("dache_gb") or "").strip()
    )
    claim_sum = sum(_to_int_amt(r.get("claim_amt")) for r in rows)
    summary["claim_tot"] = claim_sum
    if source != "saved":
        summary["imdae_dache"] = dache_sum
    # 보증금은 건물 계약의 최초 보증금과 현재 계약 보증금의 차이만 대체로 표시한다.
    # 건물주 통장(O)이면 보증금대체를 만들지 않는다.
    if _is_item_manager_account(building, "bojung"):
        summary["bojung_dache"] = (
            _to_int_amt(summary.get("bojung_tot"))
            - _to_int_amt(summary.get("first_amt"))
        )
    else:
        summary["bojung_dache"] = 0
    rent_manager = _is_item_manager_account(building, "rent")
    manage_manager = _is_item_manager_account(building, "manage")
    bojung_manager = _is_item_manager_account(building, "bojung")
    if manager_account:
        pay_base = _to_int_amt(summary.get("bojung_dache")) if bojung_manager else 0
        for r in rows:
            if r.get("is_empty"):
                continue
            rent_calc = r.get("rent_calc") if r.get("rent_calc") is not None else r.get("rent_amt")
            sil_amt = _to_int_amt(r.get("sil_amt"))
            dache_amt = _to_int_amt(r.get("dache_amt"))
            out_settle_amt = r.get("out_settle_amt")
            if out_settle_amt is not None:
                rent_ipkum = int(out_settle_amt)
            else:
                # 화면·인쇄 입금액 모두 실입+대체의 임대료분만 표시(관리비 제외).
                rent_ipkum = _rent_ipkum_for_pay(sil_amt, dache_amt, rent_calc)
            rent_ipkum += _to_int_amt(r.get("company_pay_amt"))
            # 월세와 관리비가 같은 01 수금행에 저장된 레거시 자료는
            # 계약 월세를 먼저 충당한 잔액을 관리비로 본다.
            manage_ipkum = _to_int_amt(r.get("manage_sil_amt"))
            if manage_manager:
                rent_ipkum += manage_ipkum
            pay_base += rent_ipkum
            r["ipkum_amt"] = rent_ipkum
            r["ipkum_disp"] = money(rent_ipkum) if rent_ipkum else ""
        summary["ipkum_tot"] = pay_base
    else:
        pay_base = ipkum_sum
        summary["ipkum_tot"] = ipkum_sum
    stair_cost, inet_cost, option_cost = _g_extra_costs(building, is_resp)
    summary["stair_cost"] = stair_cost
    summary["inet_cost"] = inet_cost
    summary["option_cost"] = option_cost
    cost_sum = (
        _to_int_amt(summary.get("man_cost"))
        + _to_int_amt(summary.get("owner_suri"))
        + _to_int_amt(summary.get("jungke_cost"))
    )
    summary["cost_sum"] = cost_sum
    summary["misu_tot"] = sum(_to_int_amt(r.get("misu_amt")) for r in rows)
    company_comp_tot = sum(_to_int_amt(r.get("company_comp_amt")) for r in rows)
    summary["company_comp_tot"] = company_comp_tot
    if manager_account:
        if source != "saved" or _to_int_amt(summary.get("pay_amt")) <= 0:
            summary["pay_amt"] = (
                pay_base
                - _to_int_amt(summary.get("man_cost"))
                - _to_int_amt(summary.get("owner_suri"))
                - _to_int_amt(summary.get("jungke_cost"))
                - claim_sum
            )
        # 책임관리: 송금수수료 등으로 실제 송금은 천원 단위 버림 (예: 3,323,333 → 3,323,000)
        # 음수 초과금도 보존하되, 기존 표시 단위(1,000원)에 맞춰 0 방향 절삭한다.
        summary["pay_amt"] = int(_to_int_amt(summary.get("pay_amt")) / 1000) * 1000
        summary["pay_label"] = "당월지급액"
    else:
        # 관리실 통장으로 들어온 관리비는 건물주 청구액에서 차감한다.
        manager_income = (
            _to_int_amt(summary.get("manage_ipkum_tot")) if manage_manager else 0
        )
        deposit_adjustment = (
            _to_int_amt(summary.get("bojung_dache")) if bojung_manager else 0
        )
        net_charge = cost_sum - company_comp_tot - manager_income - deposit_adjustment
        summary["pay_amt"] = net_charge
        summary["pay_label"] = "당월청구액" if net_charge >= 0 else "당월지급액"
    summary["as_of_kr"] = (
        f"{as_of.year}년{as_of.month:02d}월{as_of.day:02d}일"
    )

    rows = _jungsan_decorate_rows(rows)
    manage_detail = []
    for r in rows:
        r["print_jisi_disp"] = (r.get("jisi_disp") or "").strip()
        adjs = r.get("adjustment_items") or []
        if r.get("is_empty") or not adjs:
            continue
        rent = _to_int_amt(
            r.get("rent_calc") if r.get("rent_calc") is not None else r.get("rent_amt")
        )
        rent_due = rent
        rent_due = max(0, rent - _to_int_amt(r.get("adjustment_rent_amt")))
        sil = _to_int_amt(r.get("sil_amt"))
        if str(r.get("dache_gb") or "").strip():
            r["print_jisi_disp"] = "(대체)"
        elif rent_due > 0 and sil <= 0:
            r["print_jisi_disp"] = "미납"
        elif rent_due > 0 and sil < rent_due:
            r["print_jisi_disp"] = "부족"
        else:
            r["print_jisi_disp"] = ""
        for adj in adjs:
            kind = str(adj.get("adj_kind") or "")
            detail = f"{_ADJ_LABELS.get(kind, '기타조정')} {money(adj.get('adj_amt'))}"
            reason = (adj.get("reason") or "").strip()
            if reason:
                detail += f" ({reason})"
            manage_detail.append(
                {
                    "adj_id": adj.get("adj_id"),
                    "hosu": (r.get("hosu") or "").strip(),
                    "ipju_seq": str(r.get("ipju_seq") or "").zfill(2),
                    "ipju_nm": (r.get("ipju_nm") or "").strip(),
                    "kind": kind,
                    "kind_label": _ADJ_LABELS.get(kind, "기타조정"),
                    "amt": _to_int_amt(adj.get("adj_amt")),
                    "burden_gb": (adj.get("burden_gb") or "O").strip(),
                    "burden_label": "관리주체" if (adj.get("burden_gb") or "O") == "C" else "건물주",
                    "reason": reason,
                    "desc": detail,
                }
            )

    dache_target_cnt = 0
    dache_target_amt = 0
    for r in rows:
        rem = 0
        if not r.get("is_empty") and _to_int_amt(r.get("claim_amt")) <= 0:
            rent_target = _to_int_amt(
                r.get("rent_calc") if r.get("rent_calc") is not None else r.get("rent_amt")
            )
            rent_target = max(0, rent_target - _to_int_amt(r.get("adjustment_rent_amt")))
            rem = _dache_rent_remain(
                rent_target,
                r.get("sil_amt"),
                r.get("dache_amt"),
            )
        r["dache_remain"] = rem
        r["can_dache"] = rem > 0
        r["can_undo"] = (not r.get("is_empty")) and bool(str(r.get("dache_gb") or "").strip())
        r["dache_key"] = (
            f"{(r.get('hosu') or '').strip()}|{str(r.get('ipju_seq') or '').zfill(2)}"
        )
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

    dache_undo_cnt = sum(1 for r in rows if r.get("can_undo"))
    dache_undo_amt = sum(_to_int_amt(r.get("dache_amt")) for r in rows if r.get("can_undo"))

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
        "manage_detail": manage_detail,
        "suri_detail": suri_detail,
        "jungke_detail": jungke_detail,
        "is_resp": is_resp,
        "manager_account": manager_account,
        "dache_target_cnt": dache_target_cnt,
        "dache_target_amt": dache_target_amt,
        "dache_undo_cnt": dache_undo_cnt,
        "dache_undo_amt": dache_undo_amt,
    }


from jungsan_engine import (
    _as_date, _ceil_100, _dache_flag, _dache_rent_remain,
    _fmt_man_dec, _fmt_man_int, _fmt_wolse_cell, _jungsan_month_rent_split,
    _jungsan_out_settle_amt, _month_bounds, _prorate_amt,
    _rent_ipkum_for_pay, _valid_out_dt, _month_sukum_sil_dache,
    _month_sukum_breakdown,
    _month_out_adjustment, _jungsan_month_tenants,
)


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
    """당월 임대료 미납·부족분 (대체 대상). 대체금은 임대료만(관리비·선불청구분 제외)."""
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    if not b1 or not b2:
        return []
    if isinstance(as_of, str):
        as_of = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
    month_start, month_end = _month_bounds(as_of)
    month_end_s = month_end.isoformat()
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
        sil_amt, dache_amt = _month_sukum_sil_dache(
            b1, b2, hosu, seq, month_start, month_end_s
        )
        remain = _dache_rent_remain(rent_calc, sil_amt, dache_amt)
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
    """당월 순수 대체전표. 그 호에 실입이 있으면 제외(대체 칸에 안 나오는 전표)."""
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    if not b1 or not b2:
        return []
    if isinstance(as_of, str):
        as_of = datetime.strptime(as_of[:10], "%Y-%m-%d").date()
    month_start, month_end = _month_bounds(as_of)
    month_end_s = month_end.isoformat()
    rows = db.query(
        """
        SELECT sukum_dt, sukum_seq, hosu, ipju_seq, su_dache_amt
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s
          AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
          AND sukum_gb='02'
          AND COALESCE(su_dache_amt,0) > 0
          AND COALESCE(su_sil_amt,0) = 0
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
        ORDER BY hosu, sukum_dt, sukum_seq
        """,
        (b1, b2, month_start.isoformat(), month_end_s),
    ) or []
    kept = []
    for r in rows:
        sil_amt, _dache = _month_sukum_sil_dache(
            b1, b2, r.get("hosu"), r.get("ipju_seq"), month_start, month_end_s
        )
        trow = db.query_one(
            """
            SELECT rent_amt FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
              AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (
                b1,
                b2,
                (r.get("hosu") or "").strip().upper(),
                str(r.get("ipju_seq") or "").zfill(2),
            ),
        )
        rent = _to_int_amt((trow or {}).get("rent_amt"))
        if rent > 0 and sil_amt >= rent:
            continue
        kept.append(r)
    return kept


def _selected_dache_keys(name="dache_sel"):
    keys = set()
    for v in request.form.getlist(name):
        s = str(v or "").strip()
        if "|" not in s:
            continue
        hosu, seq = s.split("|", 1)
        hosu = hosu.strip().upper()
        seq = str(seq or "").strip().zfill(2)
        if hosu:
            keys.add((hosu, seq))
    return keys


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


@app.route("/jungsan/adjustment", methods=["POST"])
@login_required
@require_write_access
def jungsan_adjustment():
    """월별 임대료·관리비 감면/면제. 입금자료와 분리해 관리지시에 반영한다."""
    bunji1 = _pad_bunji(request.form.get("bunji1"))
    bunji2 = _pad_bunji(request.form.get("bunji2"))
    hosu = (request.form.get("hosu") or "").strip().upper()
    ipju_seq = str(request.form.get("ipju_seq") or "").strip().zfill(2)
    as_of_s = (request.form.get("as_of") or "").strip()
    kind = (request.form.get("adj_kind") or "").strip().upper()
    burden = (request.form.get("burden_gb") or "O").strip().upper()
    reason = (request.form.get("reason") or "").strip()[:200]
    adj_id = _to_int_amt(request.form.get("adj_id"))
    try:
        as_of = datetime.strptime(as_of_s[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return _jungsan_redirect(bunji1, bunji2, as_of_s)
    if not (bunji1 and bunji2 and hosu and ipju_seq):
        return _jungsan_redirect(bunji1, bunji2, as_of_s)

    month_start, _ = _month_bounds(as_of)
    _ensure_month_adjustment_table()
    if kind not in _ADJ_LABELS:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)
    tenant = db.query_one(
        """SELECT rent_amt,manage_amt FROM bd03_det
           WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s""",
        (bunji1, bunji2, hosu, ipju_seq),
    ) or {}
    limit_amt = _to_int_amt(tenant.get("rent_amt")) if kind.startswith("RENT_") else _to_int_amt(tenant.get("manage_amt"))
    family_like = "RENT_%" if kind.startswith("RENT_") else "MANAGE_%"
    used_row = db.query_one(
        """SELECT COALESCE(SUM(adj_amt),0) AS amt FROM jungsan_adjustment
           WHERE adj_month=%s AND bunji1=%s AND bunji2=%s AND hosu=%s AND ipju_seq=%s
             AND adj_kind LIKE %s AND del_yn='N' AND adj_id<>%s""",
        (month_start.isoformat(), bunji1, bunji2, hosu, ipju_seq, family_like, adj_id),
    ) if kind != "OTHER" else {"amt": 0}
    used_amt = _to_int_amt((used_row or {}).get("amt"))
    available_amt = max(0, limit_amt - used_amt)
    amt = _to_int_amt(request.form.get("adj_amt"))
    if kind.endswith("_WAIVE") and amt <= 0:
        amt = available_amt
    if kind != "OTHER":
        amt = min(max(0, amt), available_amt)
    else:
        amt = max(0, amt)
    if amt <= 0:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)
    burden = burden if burden in ("O", "C") else "O"
    uid = session.get("sabun") or ""
    if adj_id > 0:
        db.execute(
            """UPDATE jungsan_adjustment SET adj_kind=%s,adj_amt=%s,burden_gb=%s,
                     reason=%s,uid=%s,sys_dt=NOW()
               WHERE adj_id=%s AND adj_month=%s AND bunji1=%s AND bunji2=%s
                 AND hosu=%s AND ipju_seq=%s AND del_yn='N'""",
            (kind, amt, burden, reason, uid, adj_id, month_start.isoformat(),
             bunji1, bunji2, hosu, ipju_seq),
        )
    else:
        db.execute(
            """INSERT INTO jungsan_adjustment
                 (adj_month,bunji1,bunji2,hosu,ipju_seq,adj_kind,adj_amt,burden_gb,reason,del_yn,uid,sys_dt)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'N',%s,NOW())""",
            (month_start.isoformat(), bunji1, bunji2, hosu, ipju_seq, kind, amt,
             burden, reason, uid),
        )
    return _jungsan_redirect(bunji1, bunji2, as_of_s)


@app.route("/jungsan/adjustment/delete", methods=["POST"])
@login_required
@require_write_access
def jungsan_adjustment_delete():
    bunji1 = _pad_bunji(request.form.get("bunji1"))
    bunji2 = _pad_bunji(request.form.get("bunji2"))
    as_of_s = (request.form.get("as_of") or "").strip()
    adj_id = _to_int_amt(request.form.get("adj_id"))
    if adj_id > 0 and bunji1 and bunji2:
        db.execute(
            """UPDATE jungsan_adjustment SET del_yn='Y',uid=%s,sys_dt=NOW()
               WHERE adj_id=%s AND bunji1=%s AND bunji2=%s""",
            (session.get("sabun") or "", adj_id, bunji1, bunji2),
        )
    return _jungsan_redirect(bunji1, bunji2, as_of_s)


@app.route("/jungsan/dache", methods=["POST"])
@login_required
@require_write_access
def jungsan_dache():
    """선택한 호만 임대료 대체처리(sukum01, 수금종류 02). 관리비는 넣지 않음."""
    bunji1 = _pad_bunji(request.form.get("bunji1"))
    bunji2 = _pad_bunji(request.form.get("bunji2"))
    as_of_s = (request.form.get("as_of") or "").strip()
    if not (bunji1 and bunji2 and as_of_s):
        return _jungsan_redirect(bunji1, bunji2, as_of_s)
    try:
        as_of = datetime.strptime(as_of_s[:10], "%Y-%m-%d").date()
    except ValueError:
        return _jungsan_redirect(bunji1, bunji2, as_of_s)

    want = _selected_dache_keys("dache_sel")
    targets = _jungsan_dache_targets(bunji1, bunji2, as_of)
    if want:
        targets = [
            t for t in targets
            if ((t.get("hosu") or "").strip().upper(), str(t.get("ipju_seq") or "").zfill(2)) in want
        ]
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

    want = _selected_dache_keys("dache_undo")
    rows = []
    for r in _jungsan_dache_rows(bunji1, bunji2, as_of):
        hosu = (r.get("hosu") or "").strip().upper()
        seq = str(r.get("ipju_seq") or "").strip().zfill(2)
        if not want or (hosu, seq) in want or (hosu, "00") in want:
            rows.append(r)
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
        _ensure_g_cost_cols()
        bsql = (
            "SELECT bunji1, bunji2, juso, owner_nm, mgmt_gb, sukum_acct_gb,"
            " sukum_bojung_acct_gb, sukum_rent_acct_gb, sukum_manage_acct_gb, man_cost,"
            " stair_cost, inet_cost, option_cost FROM bd01"
        )
        if b_where:
            bsql += " WHERE " + " AND ".join(b_where)
        bsql += " ORDER BY bunji1, bunji2"
        buildings = db.query(bsql, b_args)

        saved_rows = db.query(
            """
            SELECT j.*, b.juso, b.owner_nm, b.mgmt_gb, b.sukum_acct_gb,
                   b.sukum_bojung_acct_gb, b.sukum_rent_acct_gb, b.sukum_manage_acct_gb,
                   b.man_cost AS b_man_cost,
                   b.stair_cost, b.inet_cost, b.option_cost
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
                mgmt_gb = (r.get("mgmt_gb") or "").strip().upper()
                is_resp = (
                    mgmt_gb == "R" if mgmt_gb in ("R", "G")
                    else _to_int_amt(r.get("b_man_cost")) > 0
                )
                manager_account = _is_item_manager_account(r, "rent")
                if manager_account:
                    pay = _to_int_amt(r.get("pay_amt"))
                    pay = (pay // 1000) * 1000
                else:
                    stair, inet, option = _g_extra_costs(r, is_resp)
                    pay = (
                        _to_int_amt(r.get("man_cost"))
                        + stair
                        + inet
                        + option
                        + _to_int_amt(r.get("owner_suri"))
                        + _to_int_amt(r.get("jungke_cost"))
                    )
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
                b.get("bunji1"), b.get("bunji2"), month_end.isoformat(), list_mode=True
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
