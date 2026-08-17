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
    login_required,
    months_elapsed as _months_elapsed,
    pad_bunji as _pad_bunji,
    paginate as _paginate,
    to_int_amt as _to_int_amt,
)


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
    if ran:
        try:
            as_of = datetime.strptime(as_of_s[:10], "%Y-%m-%d").date()
        except ValueError:
            as_of = today
            as_of_s = as_of.isoformat()

        where = [_CURRENT_TENANT_SQL]
        args = []
        if bunji1:
            where.append("d.bunji1=%s")
            args.append(bunji1)
        if bunji2:
            where.append("d.bunji2=%s")
            args.append(bunji2)
        if hosu:
            where.append("UPPER(TRIM(d.hosu))=%s")
            args.append(hosu)
        if name:
            where.append("d.ipju_nm LIKE %s")
            args.append(f"%{name}%")

        # 기준일 이전 입주 현재 거주자 + 기준일까지 월세+관리 수금 합
        sql = f"""
            SELECT d.bunji1, d.bunji2, d.hosu, d.ipju_seq, d.ipju_nm, d.ipju_dt,
                   d.rent_amt, d.manage_amt, d.bojung_amt,
                   COALESCE(p.paid, 0) AS paid
            FROM bd03_det d
            LEFT JOIN (
                SELECT bunji1, bunji2, hosu, ipju_seq,
                       SUM(COALESCE(su_sil_amt,0) + COALESCE(su_dache_amt,0)) AS paid
                FROM sukum01
                WHERE sukum_char='01'
                  AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
                  AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
                GROUP BY bunji1, bunji2, hosu, ipju_seq
            ) p
              ON p.bunji1=d.bunji1 AND p.bunji2=d.bunji2
             AND UPPER(TRIM(p.hosu))=UPPER(TRIM(d.hosu)) AND p.ipju_seq=d.ipju_seq
            WHERE {" AND ".join(where)}
              AND (d.ipju_dt IS NULL OR d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY))
            ORDER BY d.bunji1, d.bunji2, d.hosu, d.ipju_seq
            LIMIT 2000
        """
        rows = db.query(sql, [as_of_s, *args, as_of_s])
        for r in rows:
            rent = _to_int_amt(r.get("rent_amt"))
            manage = _to_int_amt(r.get("manage_amt"))
            monthly = rent + manage
            months = _months_elapsed(r.get("ipju_dt"), as_of)
            expected = monthly * months
            paid = _to_int_amt(r.get("paid"))
            misu_amt = max(0, expected - paid)
            if only_misu and misu_amt <= 0:
                continue
            total_misu += misu_amt
            results.append(
                {
                    "bunji1": r.get("bunji1"),
                    "bunji2": r.get("bunji2"),
                    "hosu": r.get("hosu"),
                    "ipju_seq": r.get("ipju_seq"),
                    "ipju_nm": r.get("ipju_nm"),
                    "ipju_dt": r.get("ipju_dt"),
                    "misu_amt": misu_amt,
                    "months": months,
                    "expected": expected,
                    "paid": paid,
                    "bojung_amt": r.get("bojung_amt"),
                    "rent_amt": rent,
                    "manage_amt": manage,
                    "monthly": monthly,
                }
            )
        # 미수 큰 순
        results.sort(key=lambda x: (-x["misu_amt"], x["bunji1"] or "", x["hosu"] or ""))

    # 전체 건수 저장 (페이징 전)
    total_count = len(results)

    pager = None
    if ran and results:
        results, pager = _paginate(results)

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
        total_count=total_count,
        pager=pager,
    )
