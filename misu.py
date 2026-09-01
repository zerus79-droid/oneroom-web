"""미수금 현황 조회 화면.

기준일 시점 거주 입주자 기준으로 누적 미수 목록을 계산해서 보여주는
읽기 전용(조회) 화면입니다.
"""
from calendar import monthrange
from datetime import date, datetime

from flask import render_template, request

import db
from app_instance import app
from utils import (
    CURRENT_TENANT_SQL as _CURRENT_TENANT_SQL,
    building_label as _building_label,
    fmt_bunji_pair as _fmt_bunji_pair,
    login_required,
    months_elapsed as _months_elapsed,
    make_pager as _make_pager,
    pad_bunji as _pad_bunji,
    to_int_amt as _to_int_amt,
)

# 인쇄 한 장(A4)에 담을 줄 수 — 페이지 번호는 이 값으로 직접 나눠서 계산
# (브라우저 인쇄 엔진의 실제 쪽수 계산 CSS는 크롬이 지원 안 함)
_PRINT_ROWS_PER_PAGE = 30


def _months_sql(dt_col="d.ipju_dt"):
    """입주일~기준일 경과연월. as_of 바인딩 1개(%s)."""
    return (
        "GREATEST(0, IFNULL(IF("
        f"{dt_col} IS NULL OR {dt_col} < '1000-01-01', 0, "
        f"PERIOD_DIFF(DATE_FORMAT(%s, '%%Y%%m'), DATE_FORMAT({dt_col}, '%%Y%%m'))"
        "), 0))"
    )


def _paid_join_sql(as_of_s, dache_from=None, bunji1=None, bunji2=None):
    """수금 합계 조인. 건물 키가 있으면 그 건물만 집계해서 전체 수금 스캔을 피한다."""
    extra = []
    if dache_from:
        dache_sum = (
            "SUM(CASE WHEN sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY) "
            "THEN COALESCE(su_dache_amt,0) ELSE 0 END)"
        )
        extra.extend([dache_from, as_of_s])
    else:
        dache_sum = "SUM(COALESCE(su_dache_amt,0))"
    extra.append(as_of_s)
    building_sql = ""
    if bunji1 and bunji2:
        building_sql = "AND bunji1=%s AND bunji2=%s"
        extra.extend([bunji1, bunji2])
    sql = f"""
        LEFT JOIN (
            SELECT bunji1, bunji2, hosu_norm, ipju_seq,
                   SUM(COALESCE(su_sil_amt,0)) AS paid,
                   {dache_sum} AS paid_dache
            FROM sukum01
            WHERE sukum_char='01'
              AND (del_yn IS NULL OR del_yn='' OR del_yn='N')
              AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
              {building_sql}
            GROUP BY bunji1, bunji2, hosu_norm, ipju_seq
        ) p ON p.bunji1=d.bunji1 AND p.bunji2=d.bunji2
           AND p.hosu_norm=d.hosu_norm AND p.ipju_seq=d.ipju_seq
    """
    return sql, extra


def _misu_amt_sql():
    """미수 = (임+관)×연월 − 실입. as_of 바인딩 1개(%s). 세입자 없으면 0."""
    months = _months_sql("d.ipju_dt")
    return (
        "CASE WHEN d.ipju_seq IS NULL OR d.ipju_seq='' THEN 0 ELSE "
        f"GREATEST(0, (COALESCE(d.rent_amt,0)+COALESCE(d.manage_amt,0))*({months})"
        " - COALESCE(p.paid,0)) END"
    )


def _building_inner_sql(bunji1, bunji2, as_of_s, dache_from=None):
    paid_sql, paid_args = _paid_join_sql(as_of_s, dache_from, bunji1, bunji2)
    months = _months_sql("d.ipju_dt")
    misu = _misu_amt_sql()
    sql = f"""
        SELECT m.hosu AS room_hosu,
               COALESCE(d.bunji1, m.bunji1) AS bunji1,
               COALESCE(d.bunji2, m.bunji2) AS bunji2,
               COALESCE(d.hosu, m.hosu) AS hosu,
               d.ipju_seq, d.ipju_nm, d.ipju_dt,
               d.rent_amt, d.manage_amt, d.bojung_amt,
               {months} AS months,
               (COALESCE(d.rent_amt,0)+COALESCE(d.manage_amt,0))*({months}) AS expected,
               {misu} AS misu_amt,
               COALESCE(p.paid, 0) AS paid,
               COALESCE(p.paid_dache, 0) AS paid_dache
        FROM bd03_m m
        LEFT JOIN bd03_det d
          ON d.bunji1=m.bunji1 AND d.bunji2=m.bunji2 AND d.hosu_norm=m.hosu_norm
         AND {_CURRENT_TENANT_SQL}
         AND (d.ipju_dt IS NULL OR d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY))
        {paid_sql}
        WHERE m.bunji1=%s AND m.bunji2=%s
    """
    # SELECT months/expected/misu + 입주 기준일 + 수금조인 + 번지
    args = [as_of_s, as_of_s, as_of_s, as_of_s, *paid_args, bunji1, bunji2]
    return sql, args


def _tenant_inner_sql(bunji1, bunji2, hosu, name, as_of_s, dache_from=None):
    paid_sql, paid_args = _paid_join_sql(as_of_s, dache_from, bunji1, bunji2)
    months = _months_sql("d.ipju_dt")
    misu = _misu_amt_sql()
    where = [_CURRENT_TENANT_SQL]
    args = []
    if bunji1:
        where.append("d.bunji1=%s")
        args.append(bunji1)
    if bunji2:
        where.append("d.bunji2=%s")
        args.append(bunji2)
    if hosu:
        where.append("d.hosu_norm=%s")
        args.append(hosu)
    if name:
        where.append("d.ipju_nm LIKE %s")
        args.append(f"%{name}%")
    sql = f"""
        SELECT d.bunji1, d.bunji2, d.hosu, d.ipju_seq, d.ipju_nm, d.ipju_dt,
               d.rent_amt, d.manage_amt, d.bojung_amt,
               {months} AS months,
               (COALESCE(d.rent_amt,0)+COALESCE(d.manage_amt,0))*({months}) AS expected,
               {misu} AS misu_amt,
               COALESCE(p.paid, 0) AS paid,
               COALESCE(p.paid_dache, 0) AS paid_dache
        FROM bd03_det d
        {paid_sql}
        WHERE {" AND ".join(where)}
          AND (d.ipju_dt IS NULL OR d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY))
    """
    # months, expected, misu, paid-join as_of, ipju_dt cutoff
    inner_args = [as_of_s, as_of_s, as_of_s, *paid_args, *args, as_of_s]
    return sql, inner_args


def _misu_outer_where(building_wide, only_misu):
    # 미수 현황은 입주자 기준이므로 공실 행은 어떤 조회 조건에서도 제외한다.
    tenant_filter = "t.ipju_seq IS NOT NULL AND TRIM(t.ipju_seq)<>''"
    if not only_misu:
        return tenant_filter if building_wide else "1=1"
    return f"{tenant_filter + ' AND ' if building_wide else ''}t.misu_amt > 0"


def _misu_order_sql(building_wide):
    if building_wide:
        return (
            "CASE WHEN UPPER(LEFT(t.hosu,1))='B' THEN 0 "
            "WHEN LEFT(t.hosu,1) BETWEEN '0' AND '9' THEN 1 ELSE 2 END, t.hosu"
        )
    return "t.misu_amt DESC, t.bunji1, t.bunji2, t.hosu"


def query_misu_page(
    bunji1, bunji2, hosu, name, as_of_s, *,
    building_wide, only_misu, dache_from=None, limit=None, offset=0,
):
    """합계는 SQL, 목록은 LIMIT/OFFSET. 만 호실도 페이지 단위만 읽는다."""
    if building_wide:
        inner, args = _building_inner_sql(bunji1, bunji2, as_of_s, dache_from)
    else:
        inner, args = _tenant_inner_sql(bunji1, bunji2, hosu, name, as_of_s, dache_from)
    outer = _misu_outer_where(building_wide, only_misu)
    order_sql = _misu_order_sql(building_wide)
    tot = db.query_one(
        f"""
        SELECT COUNT(*) AS c,
               COALESCE(SUM(t.misu_amt),0) AS misu,
               COALESCE(SUM(LEAST(t.paid_dache, t.misu_amt)),0) AS dache
        FROM ({inner}) t
        WHERE {outer}
        """,
        args,
    ) or {}
    total = int(tot.get("c") or 0)
    sql = f"""
        SELECT t.*, LEAST(t.paid_dache, t.misu_amt) AS dache_amt
        FROM ({inner}) t
        WHERE {outer}
        ORDER BY {order_sql}
    """
    page_args = list(args)
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        page_args.extend([int(limit), int(offset or 0)])
    rows = db.query(sql, page_args) if total or limit is None else []
    return rows, total, _to_int_amt(tot.get("misu")), _to_int_amt(tot.get("dache"))


def count_current_misu(as_of_s):
    """홈 KPI: 현재 입주자 중 미수>0 건수만."""
    inner, args = _tenant_inner_sql("", "", "", "", as_of_s)
    row = db.query_one(
        f"SELECT COUNT(*) AS c FROM ({inner}) t WHERE t.misu_amt > 0",
        args,
    )
    return int((row or {}).get("c") or 0)


def _building_room_rows(bunji1, bunji2, as_of_s, dache_from=None):
    """건물 전체 호수(공실 포함). 인쇄 등에서 전체 행이 필요할 때."""
    rows, _n, _m, _d = query_misu_page(
        bunji1, bunji2, "", "", as_of_s,
        building_wide=True, only_misu=False, dache_from=dache_from,
    )
    return rows


def _filtered_tenant_rows(bunji1, bunji2, hosu, name, as_of_s):
    """주소/호수/이름 현재입주자. 인쇄 등에서 전체 행이 필요할 때."""
    rows, _n, _m, _d = query_misu_page(
        bunji1, bunji2, hosu, name, as_of_s,
        building_wide=False, only_misu=False,
    )
    return rows


def _room_row_to_result(r, *, as_of, building_wide, bunji1="", bunji2=""):
    is_vacant = building_wide and not (r.get("ipju_nm") or "").strip()
    rent = _to_int_amt(r.get("rent_amt"))
    manage = _to_int_amt(r.get("manage_amt"))
    monthly = rent + manage
    if r.get("months") is not None:
        months = int(r.get("months") or 0)
    else:
        months = _months_elapsed(r.get("ipju_dt"), as_of)
    expected = _to_int_amt(r.get("expected")) if r.get("expected") is not None else monthly * months
    paid = _to_int_amt(r.get("paid"))
    dache_cum = _to_int_amt(r.get("paid_dache"))
    if r.get("misu_amt") is not None:
        misu_amt = 0 if is_vacant else _to_int_amt(r.get("misu_amt"))
    else:
        misu_amt = 0 if is_vacant else max(0, expected - paid)
    if r.get("dache_amt") is not None:
        dache_amt = 0 if is_vacant else _to_int_amt(r.get("dache_amt"))
    else:
        dache_amt = 0 if is_vacant else min(dache_cum, misu_amt)
    return {
        "bunji1": r.get("bunji1") or bunji1,
        "bunji2": r.get("bunji2") or bunji2,
        "hosu": r.get("hosu") or r.get("room_hosu"),
        "ipju_seq": r.get("ipju_seq"),
        "ipju_nm": r.get("ipju_nm"),
        "ipju_dt": r.get("ipju_dt"),
        "is_vacant": is_vacant,
        "misu_amt": misu_amt,
        "dache_amt": dache_amt,
        "months": months,
        "expected": expected,
        "paid": paid,
        "bojung_amt": r.get("bojung_amt"),
        "rent_amt": rent,
        "manage_amt": manage,
        "monthly": monthly,
    }


@app.route("/misu")
@login_required
def misu():
    """
    미수금 현황 조회 (XP「미수 현황 조회」).
    기준일 시점 거주 입주자 기준 누적 미수 목록.
    UX: 주소·호수·성명 위, 기준일자는 아래.
    """
    today = date.today()
    # XP 기본: 당월 말일
    default_as_of = date(
        today.year, today.month, monthrange(today.year, today.month)[1]
    ).isoformat()

    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    name = (request.args.get("name") or "").strip()
    as_of_s = (request.args.get("as_of") or "").strip() or default_as_of
    # 체크 없으면 미전송 → 기본 True, 조회 후 해제 시 only_misu 미포함이면 False 처리 위해
    if "q" in request.args or "as_of" in request.args or any(
        k in request.args for k in ("bunji1", "bunji2", "hosu", "name", "only_misu")
    ):
        only_misu = request.args.get("only_misu") == "1"
    else:
        only_misu = True

    ran = (
        "as_of" in request.args
        or "bunji1" in request.args
        or "bunji2" in request.args
        or "hosu" in request.args
        or "name" in request.args
        or "only_misu" in request.args
    )

    results = []
    total_misu = 0
    total_dache = 0
    total_count = 0
    pager = None
    if ran:
        try:
            as_of = datetime.strptime(as_of_s[:10], "%Y-%m-%d").date()
        except ValueError:
            as_of = today
            as_of_s = as_of.isoformat()

        building_wide = bool(bunji1 and bunji2 and not hosu and not name)
        pager = _make_pager(0)
        rows, total_count, total_misu, total_dache = query_misu_page(
            bunji1, bunji2, hosu, name, as_of_s,
            building_wide=building_wide, only_misu=only_misu,
            limit=pager["per_page"], offset=0,
        )
        pager = _make_pager(total_count)
        if pager["offset"]:
            rows, total_count, total_misu, total_dache = query_misu_page(
                bunji1, bunji2, hosu, name, as_of_s,
                building_wide=building_wide, only_misu=only_misu,
                limit=pager["per_page"], offset=pager["offset"],
            )
        results = [
            _room_row_to_result(
                r, as_of=as_of, building_wide=building_wide,
                bunji1=bunji1, bunji2=bunji2,
            )
            for r in rows
        ]

    return render_template(
        "misu.html",
        filters={
            "bunji1": bunji1,
            "bunji2": bunji2,
            "hosu": hosu,
            "name": name,
            "as_of": as_of_s,
            "only_misu": only_misu,
        },
        results=results,
        ran=ran,
        total_misu=total_misu,
        total_dache=total_dache,
        total_count=total_count,
        pager=pager,
    )


@app.route("/misu/print")
@login_required
def misu_print():
    """미수 현황 인쇄 — 별도 인쇄 전용 템플릿.

    건물 하나 전체(공실 포함)를 볼 땐 XP 레거시 「수금(대체)현황」 출력물
    형식 참고해서 기간(대체금액 집계 시작일)을 지정할 수 있음 — 미수잔액은
    항상 기간 종료일 기준 누적, 대체금액만 그 기간으로 한정.
    호수·이름으로 좁힌 결과(공실 없음)는 화면(misu.html)과 동일하게 기준일
    하나로 계산해서 화면에 보이는 것과 같은 목록을 그대로 인쇄함.
    """
    today = date.today()
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    name = (request.args.get("name") or "").strip()
    only_misu = request.args.get("only_misu") == "1"
    building_wide = bool(bunji1 and bunji2 and not hosu and not name)

    date_from = (request.args.get("date_from") or "").strip() or date(today.year, 1, 1).isoformat()
    date_to = (request.args.get("date_to") or "").strip() or today.isoformat()
    as_of_s = (request.args.get("as_of") or "").strip() or date_to

    results = []
    total_misu = 0
    total_dache = 0
    total_bojung = 0
    total_rent = 0
    total_manage = 0
    if building_wide:
        try:
            as_of = datetime.strptime(date_to[:10], "%Y-%m-%d").date()
        except ValueError:
            as_of = today
            date_to = as_of.isoformat()

        rows = _building_room_rows(bunji1, bunji2, date_to, dache_from=date_from)
        for r in rows:
            row = _room_row_to_result(
                r, as_of=as_of, building_wide=True, bunji1=bunji1, bunji2=bunji2,
            )
            total_misu += row["misu_amt"]
            total_dache += row["dache_amt"]
            total_bojung += _to_int_amt(row["bojung_amt"])
            total_rent += row["rent_amt"]
            total_manage += row["manage_amt"]
            results.append(row)
        results.sort(key=lambda x: (
            0 if (x["hosu"] or "").upper().startswith("B") else
            1 if (x["hosu"] or "")[:1].isdigit() else 2,
            x["hosu"] or "",
        ))
    else:
        try:
            as_of = datetime.strptime(as_of_s[:10], "%Y-%m-%d").date()
        except ValueError:
            as_of = today
            as_of_s = as_of.isoformat()

        rows = _filtered_tenant_rows(bunji1, bunji2, hosu, name, as_of_s)
        for r in rows:
            row = _room_row_to_result(
                r, as_of=as_of, building_wide=False, bunji1=bunji1, bunji2=bunji2,
            )
            if only_misu and row["misu_amt"] <= 0:
                continue
            total_misu += row["misu_amt"]
            total_dache += row["dache_amt"]
            total_bojung += _to_int_amt(row["bojung_amt"])
            total_rent += row["rent_amt"]
            total_manage += row["manage_amt"]
            results.append(row)
        results.sort(key=lambda x: (-x["misu_amt"], x["bunji1"] or "", x["hosu"] or ""))

    pages = [
        results[i:i + _PRINT_ROWS_PER_PAGE]
        for i in range(0, len(results), _PRINT_ROWS_PER_PAGE)
    ] or [[]]

    return render_template(
        "misu_print.html",
        building_wide=building_wide,
        bunji1=bunji1,
        bunji2=bunji2,
        hosu=hosu,
        name=name,
        only_misu=only_misu,
        building_name=_building_label(bunji1, bunji2) if bunji1 and bunji2 else "",
        addr_label=_fmt_bunji_pair(bunji1, bunji2) if bunji1 and bunji2 else "",
        date_from=date_from,
        date_to=date_to,
        as_of=as_of_s,
        pages=pages,
        total_pages=len(pages),
        total_count=len(results),
        total_misu=total_misu,
        total_dache=total_dache,
        total_bojung=total_bojung,
        total_rent=total_rent,
        total_manage=total_manage,
    )
