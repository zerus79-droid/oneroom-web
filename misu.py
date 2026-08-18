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
    pad_bunji as _pad_bunji,
    paginate as _paginate,
    to_int_amt as _to_int_amt,
)

# 인쇄 한 장(A4)에 담을 줄 수 — 페이지 번호는 이 값으로 직접 나눠서 계산
# (브라우저 인쇄 엔진의 실제 쪽수 계산 CSS는 크롬이 지원 안 함)
_PRINT_ROWS_PER_PAGE = 30


def _building_room_rows(bunji1, bunji2, as_of_s, dache_from=None):
    """건물 전체 호수(공실 포함) 미수/대체 요약 행.

    dache_from 있으면 대체금액 합계를 그 날짜부터 as_of_s까지로 한정(인쇄용 기간
    조회). 미수잔액 계산용 누적 납부액(paid)은 항상 전체 기간(~as_of_s) 기준.
    """
    if dache_from:
        dache_where = "sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)"
        dache_args = [dache_from, as_of_s]
    else:
        dache_where = "sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)"
        dache_args = [as_of_s]
    sql = f"""
        SELECT m.hosu AS room_hosu, d.bunji1, d.bunji2, d.hosu, d.ipju_seq,
               d.ipju_nm, d.ipju_dt, d.rent_amt, d.manage_amt, d.bojung_amt,
               COALESCE(pa.paid, 0) AS paid,
               COALESCE(pd.paid_dache, 0) AS paid_dache
        FROM bd03_m m
        LEFT JOIN bd03_det d
          ON d.bunji1=m.bunji1 AND d.bunji2=m.bunji2
         AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu))
         AND {_CURRENT_TENANT_SQL}
         AND (d.ipju_dt IS NULL OR d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY))
        LEFT JOIN (
            SELECT bunji1, bunji2, hosu, ipju_seq,
                   SUM(COALESCE(su_sil_amt,0) + COALESCE(su_dache_amt,0)) AS paid
            FROM sukum01
            WHERE sukum_char='01' AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
              AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
            GROUP BY bunji1, bunji2, hosu, ipju_seq
        ) pa
          ON pa.bunji1=d.bunji1 AND pa.bunji2=d.bunji2
         AND UPPER(TRIM(pa.hosu))=UPPER(TRIM(d.hosu)) AND pa.ipju_seq=d.ipju_seq
        LEFT JOIN (
            SELECT bunji1, bunji2, hosu, ipju_seq,
                   SUM(COALESCE(su_dache_amt,0)) AS paid_dache
            FROM sukum01
            WHERE sukum_char='01' AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
              AND {dache_where}
            GROUP BY bunji1, bunji2, hosu, ipju_seq
        ) pd
          ON pd.bunji1=d.bunji1 AND pd.bunji2=d.bunji2
         AND UPPER(TRIM(pd.hosu))=UPPER(TRIM(d.hosu)) AND pd.ipju_seq=d.ipju_seq
        WHERE m.bunji1=%s AND m.bunji2=%s
        ORDER BY m.hosu
        LIMIT 2000
    """
    args = [as_of_s, as_of_s, *dache_args, bunji1, bunji2]
    return db.query(sql, args)


def _room_row_to_result(r, *, as_of, building_wide, bunji1="", bunji2=""):
    is_vacant = building_wide and not r.get("ipju_nm")
    rent = _to_int_amt(r.get("rent_amt"))
    manage = _to_int_amt(r.get("manage_amt"))
    monthly = rent + manage
    months = _months_elapsed(r.get("ipju_dt"), as_of)
    expected = monthly * months
    paid = _to_int_amt(r.get("paid"))
    dache_amt = _to_int_amt(r.get("paid_dache"))
    misu_amt = 0 if is_vacant else max(0, expected - paid)
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
    if ran:
        try:
            as_of = datetime.strptime(as_of_s[:10], "%Y-%m-%d").date()
        except ValueError:
            as_of = today
            as_of_s = as_of.isoformat()

        # 건물 하나를 통째로 볼 때(호수·이름 지정 없음): 공실 포함 그 건물 전체 호수
        building_wide = bool(bunji1 and bunji2 and not hosu and not name)

        if building_wide:
            rows = _building_room_rows(bunji1, bunji2, as_of_s)
        else:
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
                       COALESCE(p.paid, 0) AS paid,
                       COALESCE(p.paid_dache, 0) AS paid_dache
                FROM bd03_det d
                LEFT JOIN (
                    SELECT bunji1, bunji2, hosu, ipju_seq,
                           SUM(COALESCE(su_sil_amt,0) + COALESCE(su_dache_amt,0)) AS paid,
                           SUM(COALESCE(su_dache_amt,0)) AS paid_dache
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
            row = _room_row_to_result(
                r, as_of=as_of, building_wide=building_wide,
                bunji1=bunji1, bunji2=bunji2,
            )
            if only_misu and not row["is_vacant"] and row["misu_amt"] <= 0:
                continue
            total_misu += row["misu_amt"]
            total_dache += row["dache_amt"]
            results.append(row)
        if building_wide:
            # 건물 전체 보기: 호수 순서(지하부터 높은 층) 그대로 유지
            results.sort(key=lambda x: (
                0 if (x["hosu"] or "").upper().startswith("B") else
                1 if (x["hosu"] or "")[:1].isdigit() else 2,
                x["hosu"] or "",
            ))
        else:
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
        total_dache=total_dache,
        total_count=total_count,
        pager=pager,
    )


@app.route("/misu/print")
@login_required
def misu_print():
    """미수 현황 인쇄 — 건물 하나 전체(공실 포함), 별도 인쇄 전용 템플릿.

    XP 레거시 「수금(대체)현황」 출력물 형식 참고. 화면(misu.html)과 달리
    기간(대체금액 집계 시작일)을 지정할 수 있음 — 미수잔액은 항상 기간 종료일
    기준 누적, 대체금액만 그 기간으로 한정.
    """
    today = date.today()
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    date_from = (request.args.get("date_from") or "").strip() or date(today.year, 1, 1).isoformat()
    date_to = (request.args.get("date_to") or "").strip() or today.isoformat()

    results = []
    total_misu = 0
    total_dache = 0
    total_bojung = 0
    total_rent = 0
    total_manage = 0
    if bunji1 and bunji2:
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

    pages = [
        results[i:i + _PRINT_ROWS_PER_PAGE]
        for i in range(0, len(results), _PRINT_ROWS_PER_PAGE)
    ] or [[]]

    return render_template(
        "misu_print.html",
        bunji1=bunji1,
        bunji2=bunji2,
        building_name=_building_label(bunji1, bunji2),
        addr_label=_fmt_bunji_pair(bunji1, bunji2) if bunji1 and bunji2 else "",
        date_from=date_from,
        date_to=date_to,
        pages=pages,
        total_pages=len(pages),
        total_count=len(results),
        total_misu=total_misu,
        total_dache=total_dache,
        total_bojung=total_bojung,
        total_rent=total_rent,
        total_manage=total_manage,
    )
