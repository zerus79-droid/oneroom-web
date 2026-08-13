"""월정기보고(정산) 화면.

주소별 정산서 작성/미리보기, 결산현황 인쇄, 월별 정산서 목록 조회
라우트와 그 전용 도우미 함수들을 모아둔 모듈입니다.
"""
from calendar import monthrange
from datetime import date, datetime

from flask import redirect, render_template, request, url_for

import db
from app_instance import app
from utils import (
    building_label as _building_label,
    calc_misu_amt as _calc_misu_amt,
    fmt_bunji,
    fmt_date,
    fmt_ipju_short as _fmt_ipju_short,
    login_required,
    money,
    pad_bunji as _pad_bunji,
    to_int_amt as _to_int_amt,
)


def _month_bounds(as_of):
    """기준일이 속한 달의 시작·끝 (date)."""
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    start = as_of.replace(day=1)
    end = date(as_of.year, as_of.month, monthrange(as_of.year, as_of.month)[1])
    return start, end


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
            if r.get("dache_gb") and "대체" in str(r.get("dache_gb")):
                jisi = (jisi + " (대체)").strip() if jisi else "(대체)"
            elif str(r.get("dache_gb") or "").upper() in ("Y", "1", "대체"):
                jisi = (jisi + " (대체)").strip() if jisi else "(대체)"
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
        "SELECT bunji1, bunji2, juso, owner_nm, first_amt, man_cost FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (b1, b2),
    )
    if not building:
        return {"error": "미등록 주소입니다.", "building": None}

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
        # 라이브: 호수 마스터 + 현재 입주자
        rooms = db.query(
            """
            SELECT m.hosu,
                   d.ipju_seq, d.ipju_nm, d.ipju_dt, d.bojung_amt, d.rent_amt, d.manage_amt,
                   d.napbu_gb
            FROM bd03_m m
            LEFT JOIN bd03_det d
              ON d.bunji1=m.bunji1 AND d.bunji2=m.bunji2
             AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu))
             AND (d.out_dt IS NULL OR d.out_dt < '1000-01-01')
            WHERE m.bunji1=%s AND m.bunji2=%s
            ORDER BY m.hosu
            """,
            (b1, b2),
        )
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
                    "manage_desc": "",
                    "dache_gb": "대체" if dache_amt > 0 else "",
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
        # 당월지급액 = 입금합 − 관리수수료 − 수리 − 중개 (508-88 PDF와 일치)
        pay_amt = max(0, sum_ipkum - man_cost - owner_suri - jungke_cost)
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
            "imdae_dache": sum_misu,  # 인쇄 임대료대체
            "bojung_dache": 0,
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
    }


def _jungsan_request_common():
    today = date.today()
    default_as_of = date(
        today.year, today.month, monthrange(today.year, today.month)[1]
    ).isoformat()
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
    try:
        y = int(request.args.get("year") or today.year)
    except ValueError:
        y = today.year
    try:
        m = int(request.args.get("month") or today.month)
    except ValueError:
        m = today.month
    if m < 1 or m > 12:
        m = today.month
    if y < 1990 or y > 2100:
        y = today.year

    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    ran = "q" in request.args or "year" in request.args or "month" in request.args

    month_start = date(y, m, 1)
    month_end = date(y, m, monthrange(y, m)[1])
    results = []
    sum_pay = 0

    if ran:
        where = [
            "(j.del_yn IS NULL OR j.del_yn='N' OR j.del_yn='')",
            "j.jungsan_dt >= %s",
            "j.jungsan_dt < DATE_ADD(%s, INTERVAL 1 DAY)",
        ]
        args = [month_start.isoformat(), month_end.isoformat()]
        if bunji1:
            where.append("j.bunji1=%s")
            args.append(bunji1)
        if bunji2:
            where.append("j.bunji2=%s")
            args.append(bunji2)
        rows = db.query(
            f"""
            SELECT j.*, b.juso, b.owner_nm
            FROM jungsan_m j
            LEFT JOIN bd01 b ON b.bunji1=j.bunji1 AND b.bunji2=j.bunji2
            WHERE {" AND ".join(where)}
            ORDER BY j.bunji1, j.bunji2, j.jungsan_seq
            LIMIT 500
            """,
            args,
        )
        for r in rows:
            pay = _to_int_amt(r.get("pay_amt"))
            sum_pay += pay
            as_of = fmt_date(r.get("jungsan_dt")) or month_end.isoformat()
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
                    "as_of": as_of,
                }
            )

    years = list(range(today.year, today.year - 15, -1))
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
    )
