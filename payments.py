"""수금관리 화면.

기간별 수금 현황, 수금(대체) 등록/삭제, 건물·현세입자 조회 API
라우트와 그 전용 도우미 함수들을 모아둔 모듈입니다.
"""
from calendar import monthrange
from datetime import date, datetime

from flask import flash, jsonify, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    building_label as _building_label,
    buildings_and_rooms as _buildings_and_rooms,
    calc_misu_amt as _calc_misu_amt,
    calc_month_misu_amt as _calc_month_misu_amt,
    clamp_date_str,
    fmt_bunji,
    fmt_bunji_pair,
    login_required,
    money,
    pad_bunji as _pad_bunji,
    parse_bunji_input as _parse_bunji_input,
    to_int_amt as _to_int_amt,
)


def _parse_bunji_src(src):
    bunji_raw = (src.get("bunji") or "").strip()
    if bunji_raw:
        return _parse_bunji_input(bunji_raw)
    return (
        _pad_bunji((src.get("bunji1") or "").strip()),
        _pad_bunji((src.get("bunji2") or "").strip()),
    )


def _pad_ipju_seq(seq):
    seq = str(seq or "").strip()
    if seq.isdigit():
        return seq.zfill(2)
    return seq


def _tenant_key(bunji1, bunji2, hosu, ipju_seq):
    return (
        bunji1 or "",
        bunji2 or "",
        (hosu or "").strip().upper(),
        _pad_ipju_seq(ipju_seq),
    )


def _iso_min_date(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)[:10]
    return s if len(s) >= 10 else None


def _ym_from_iso(s, today):
    try:
        df = date.fromisoformat((s or "")[:10])
        return str(df.year), f"{df.month:02d}"
    except ValueError:
        return str(today.year), f"{today.month:02d}"


def _tenant_status_sql(tenant_status, alias="d"):
    """현 거주자 / 과거 입주자 SQL 조건"""
    if tenant_status == "past":
        return f"({alias}.out_dt IS NOT NULL AND {alias}.out_dt >= '1000-01-01')"
    if tenant_status == "all":
        return "1=1"
    return f"({alias}.out_dt IS NULL OR {alias}.out_dt < '1000-01-01')"


def _first_date_for_name(nm):
    """세입자 이름 최초 등장일: 입주일·수금일 중 가장 이른 날"""
    like = f"%{nm}%"
    row = db.query_one(
        """
        SELECT MIN(dt) AS mn FROM (
          SELECT d.ipju_dt AS dt
          FROM bd03_det d
          WHERE d.ipju_nm LIKE %s
            AND d.ipju_dt IS NOT NULL AND d.ipju_dt > '1000-01-01'
          UNION ALL
          SELECT s.sukum_dt AS dt
          FROM sukum01 s
          INNER JOIN bd03_det d
            ON d.bunji1=s.bunji1 AND d.bunji2=s.bunji2
           AND TRIM(d.hosu)=TRIM(s.hosu) AND d.ipju_seq=s.ipju_seq
          WHERE d.ipju_nm LIKE %s
            AND s.sukum_dt IS NOT NULL AND s.sukum_dt > '1000-01-01'
        ) t
        """,
        (like, like),
    )
    if not row or not row.get("mn"):
        return None
    return _iso_min_date(row["mn"])


def _payment_year_options(today):
    year_rows = db.query(
        """
        SELECT DISTINCT YEAR(sukum_dt) AS y
        FROM sukum01
        WHERE sukum_dt IS NOT NULL AND sukum_dt > '1000-01-01'
        ORDER BY y DESC
        """
    )
    years = [int(r["y"]) for r in year_rows if r.get("y")]
    if today.year not in years:
        years = [today.year] + years
    if today.year - 1 not in years:
        years.append(today.year - 1)
    return sorted(set(years), reverse=True)


def _empty_payment_filters(today):
    month_start = today.replace(day=1)
    return {
        "bunji1": "",
        "bunji2": "",
        "hosu": "",
        "ipju_seq": "",
        "name": "",
        "name_mode": "",
        "tenant_status": "current",
        "date_from": month_start.isoformat(),
        "date_to": today.isoformat(),
        "ym_year": str(today.year),
        "ym_month": f"{today.month:02d}",
    }


def _read_payment_list_args(args):
    bunji1, bunji2 = _parse_bunji_src(args)
    hosu = (args.get("hosu") or "").strip().upper()
    ipju_seq_f = _pad_ipju_seq(args.get("ipju_seq"))
    name_raw = (args.get("name") or args.get("q") or "").strip()
    name_mode = args.get("name_mode") == "1"
    tenant_status = (args.get("tenant_status") or "current").strip().lower()
    if tenant_status not in ("current", "past", "all"):
        tenant_status = "current"
    return {
        "bunji1": bunji1,
        "bunji2": bunji2,
        "hosu": hosu,
        "ipju_seq_f": ipju_seq_f,
        "name_raw": name_raw,
        "name_mode": name_mode,
        "name_q": name_raw if name_mode else "",
        "name_display": name_raw,
        "tenant_status": tenant_status,
        "date_from": (args.get("date_from") or "").strip(),
        "date_to": (args.get("date_to") or "").strip(),
        "ym_year": (args.get("ym_year") or "").strip(),
        "ym_month": (args.get("ym_month") or "").strip(),
        "use_ym": args.get("use_ym") == "1",
        "all_hist": args.get("all_hist") in ("1", "true", "yes"),
        "name_list_mode": bool(name_raw)
        and name_mode
        and not (bunji1 and bunji2 and hosu),
    }


def _resolve_list_period(
    *,
    today,
    use_ym,
    ym_year,
    ym_month,
    all_hist,
    name_list_mode,
    bunji1,
    bunji2,
    hosu,
    ipju_seq_f,
    name_q,
    date_from,
    date_to,
):
    month_start = today.replace(day=1)
    if use_ym and ym_year.isdigit() and ym_month.isdigit():
        y = int(ym_year)
        m = int(ym_month)
        if 1 <= m <= 12 and 1990 <= y <= 2100:
            last_day = monthrange(y, m)[1]
            date_from = date(y, m, 1).isoformat()
            month_end = date(y, m, last_day)
            date_to = min(month_end, today).isoformat()
            return date_from, date_to, str(y), f"{m:02d}"
        return date_from, date_to, ym_year, ym_month
    if all_hist and bunji1 and bunji2 and hosu:
        first = _first_date_for_tenant(bunji1, bunji2, hosu, ipju_seq_f)
        date_from = first or "2000-01-01"
        date_to = today.isoformat()
        ym_year, ym_month = _ym_from_iso(date_from, today)
        return date_from, date_to, ym_year, ym_month
    if name_list_mode:
        first = _first_date_for_name(name_q)
        date_from = first or "2000-01-01"
        date_to = today.isoformat()
        ym_year, ym_month = _ym_from_iso(date_from, today)
        return date_from, date_to, ym_year, ym_month
    if date_from:
        date_from = clamp_date_str(date_from)
    if date_to:
        date_to = clamp_date_str(date_to)
    if not date_from:
        date_from = month_start.isoformat()
    if not date_to:
        date_to = today.isoformat()
    try:
        if date.fromisoformat(date_to[:10]) > today:
            date_to = today.isoformat()
    except ValueError:
        date_to = today.isoformat()
    ym_year, ym_month = _ym_from_iso(date_from, today)
    return date_from, date_to, ym_year, ym_month


def _search_tenants_by_name(name_q, tenant_status):
    """이름 매칭: 정확 일치 우선 → 접두 → 포함 (+ 현거주/과거 필터)"""
    status_sql = _tenant_status_sql(tenant_status, "d")
    tenants = db.query(
        f"""
        SELECT d.bunji1, d.bunji2, d.hosu, d.ipju_seq, d.ipju_nm, d.out_dt, b.juso
        FROM bd03_det d
        LEFT JOIN bd01 b ON b.bunji1=d.bunji1 AND b.bunji2=d.bunji2
        WHERE TRIM(d.ipju_nm)=%s
          AND {status_sql}
        ORDER BY d.bunji1, d.bunji2, d.hosu, d.ipju_seq
        LIMIT 200
        """,
        (name_q,),
    )
    if tenants:
        return tenants
    tenants = db.query(
        f"""
        SELECT d.bunji1, d.bunji2, d.hosu, d.ipju_seq, d.ipju_nm, d.out_dt, b.juso
        FROM bd03_det d
        LEFT JOIN bd01 b ON b.bunji1=d.bunji1 AND b.bunji2=d.bunji2
        WHERE d.ipju_nm LIKE %s
          AND {status_sql}
        ORDER BY d.bunji1, d.bunji2, d.hosu, d.ipju_seq
        LIMIT 200
        """,
        (f"{name_q}%",),
    )
    if tenants:
        return tenants
    return db.query(
        f"""
        SELECT d.bunji1, d.bunji2, d.hosu, d.ipju_seq, d.ipju_nm, d.out_dt, b.juso
        FROM bd03_det d
        LEFT JOIN bd01 b ON b.bunji1=d.bunji1 AND b.bunji2=d.bunji2
        WHERE d.ipju_nm LIKE %s
          AND {status_sql}
        ORDER BY d.bunji1, d.bunji2, d.hosu, d.ipju_seq
        LIMIT 200
        """,
        (f"%{name_q}%",),
    )


def _focus_single_tenant(t, name_q, today):
    bunji1 = t.get("bunji1") or ""
    bunji2 = t.get("bunji2") or ""
    hosu = (t.get("hosu") or "").strip().upper()
    ipju_seq_f = _pad_ipju_seq(t.get("ipju_seq"))
    name_display = (t.get("ipju_nm") or name_q).strip()
    first = _first_date_for_tenant(bunji1, bunji2, hosu, ipju_seq_f)
    date_from = first or "2000-01-01"
    date_to = today.isoformat()
    ym_year, ym_month = _ym_from_iso(date_from, today)
    return {
        "bunji1": bunji1,
        "bunji2": bunji2,
        "hosu": hosu,
        "ipju_seq_f": ipju_seq_f,
        "name_display": name_display,
        "date_from": date_from,
        "date_to": date_to,
        "ym_year": ym_year,
        "ym_month": ym_month,
        "all_hist": True,
    }


def _name_match_payment_groups(tenants, date_from, date_to):
    """동명이인: 수금 전건 로드 금지 + 통계 1회 쿼리로 묶기."""
    key_set = set()
    tenant_meta = []
    for t in tenants:
        t_b1 = t.get("bunji1") or ""
        t_b2 = t.get("bunji2") or ""
        t_hosu = (t.get("hosu") or "").strip().upper()
        t_seq = _pad_ipju_seq(t.get("ipju_seq"))
        key = (t_b1, t_b2, t_hosu, t_seq)
        if key in key_set:
            continue
        key_set.add(key)
        tenant_meta.append(
            {
                "bunji1": t_b1,
                "bunji2": t_b2,
                "hosu": t_hosu,
                "ipju_seq": t_seq,
                "ipju_nm": (t.get("ipju_nm") or "").strip(),
                "juso": (t.get("juso") or "").strip(),
            }
        )
    stats_map = {}
    if tenant_meta:
        placeholders = []
        args_in = []
        for m in tenant_meta:
            placeholders.append("(%s,%s,%s,%s)")
            args_in.extend([m["bunji1"], m["bunji2"], m["hosu"], m["ipju_seq"]])
        stats_sql = f"""
            SELECT
              s.bunji1, s.bunji2,
              UPPER(TRIM(s.hosu)) AS hosu,
              LPAD(TRIM(s.ipju_seq), 2, '0') AS ipju_seq,
              COUNT(*) AS hist_c,
              MIN(s.sukum_dt) AS mn,
              MAX(s.sukum_dt) AS mx,
              SUM(
                CASE
                  WHEN s.sukum_dt >= %s AND s.sukum_dt < %s + INTERVAL 1 DAY
                  THEN 1 ELSE 0
                END
              ) AS pay_c
            FROM sukum01 s
            WHERE (s.bunji1, s.bunji2, UPPER(TRIM(s.hosu)),
                   LPAD(TRIM(s.ipju_seq), 2, '0'))
                  IN ({','.join(placeholders)})
            GROUP BY s.bunji1, s.bunji2,
                     UPPER(TRIM(s.hosu)),
                     LPAD(TRIM(s.ipju_seq), 2, '0')
        """
        stats_rows = db.query(
            stats_sql,
            tuple([date_from + " 00:00:00", date_to] + args_in),
        )
        for sr in stats_rows or []:
            stats_map[_tenant_key(sr.get("bunji1"), sr.get("bunji2"), sr.get("hosu"), sr.get("ipju_seq"))] = sr
    groups = []
    for m in tenant_meta:
        sr = stats_map.get((m["bunji1"], m["bunji2"], m["hosu"], m["ipju_seq"])) or {}
        hist_mn = sr.get("mn")
        hist_mx = sr.get("mx")
        groups.append(
            {
                "bunji1": m["bunji1"],
                "bunji2": m["bunji2"],
                "hosu": m["hosu"],
                "ipju_seq": m["ipju_seq"],
                "ipju_nm": m["ipju_nm"],
                "juso": m["juso"],
                "pay_count": int(sr.get("pay_c") or 0),
                "hist_count": int(sr.get("hist_c") or 0),
                "hist_from": str(hist_mn)[:10] if hist_mn is not None else None,
                "hist_to": str(hist_mx)[:10] if hist_mx is not None else None,
            }
        )
    return groups


def _query_payment_rows(
    bunji1, bunji2, hosu, ipju_seq_f, date_from, date_to, all_hist
):
    where = ["1=1"]
    args = []
    if bunji1:
        where.append("s.bunji1=%s")
        args.append(bunji1)
    if bunji2:
        where.append("s.bunji2=%s")
        args.append(bunji2)
    if hosu:
        where.append("UPPER(TRIM(s.hosu))=UPPER(TRIM(%s))")
        args.append(hosu)
    if ipju_seq_f:
        where.append("LPAD(TRIM(s.ipju_seq), 2, '0')=LPAD(TRIM(%s), 2, '0')")
        args.append(ipju_seq_f)
    if date_from:
        where.append("s.sukum_dt >= %s")
        args.append(date_from + " 00:00:00")
    if date_to:
        where.append("s.sukum_dt < %s + INTERVAL 1 DAY")
        args.append(date_to)
    where.append("(s.del_yn IS NULL OR s.del_yn='' OR s.del_yn='N')")
    row_limit = 2000 if all_hist else 500
    sql = f"""
        SELECT s.sukum_dt, s.sukum_seq, s.bunji1, s.bunji2, s.hosu, s.ipju_seq,
               s.sukum_char, s.sukum_gb, s.manage_desc,
               s.su_sil_amt, s.su_dache_amt, s.s_method, s.del_yn,
               c1.g_cd_nm AS char_nm, c2.g_cd_nm AS gb_nm,
               d.ipju_nm, b.juso
        FROM sukum01 s
        LEFT JOIN gicho_code c1
          ON c1.g_cd='01' AND c1.g_sub_cd=s.sukum_char
        LEFT JOIN gicho_code c2
          ON c2.g_cd='02' AND c2.g_sub_cd=s.sukum_gb
        LEFT JOIN bd03_det d
          ON d.bunji1=s.bunji1 AND d.bunji2=s.bunji2
         AND UPPER(TRIM(d.hosu))=UPPER(TRIM(s.hosu))
         AND LPAD(TRIM(d.ipju_seq),2,'0')=LPAD(TRIM(s.ipju_seq),2,'0')
        LEFT JOIN bd01 b
          ON b.bunji1=s.bunji1 AND b.bunji2=s.bunji2
        WHERE {' AND '.join(where)}
        ORDER BY s.sukum_dt ASC, CAST(s.sukum_seq AS UNSIGNED) ASC
        LIMIT {int(row_limit)}
    """
    return db.query(sql, tuple(args))


@app.route("/payments")
@login_required
def payments():
    today = date.today()
    # 상단 메뉴 등: fresh=1 또는 쿼리 없음 → 이전 세입자/주소 조건 초기화
    arg_keys = [k for k in request.args.keys() if k not in ("fresh",)]
    is_fresh = request.args.get("fresh") == "1" or len(arg_keys) == 0
    buildings, rooms = _buildings_and_rooms()
    years = _payment_year_options(today)

    if is_fresh:
        return render_template(
            "payments.html",
            payments=[],
            payment_groups=[],
            name_list_mode=False,
            is_fresh=True,
            buildings=buildings,
            rooms=rooms,
            years=years,
            filters=_empty_payment_filters(today),
        )

    q = _read_payment_list_args(request.args)
    bunji1 = q["bunji1"]
    bunji2 = q["bunji2"]
    hosu = q["hosu"]
    ipju_seq_f = q["ipju_seq_f"]
    name_mode = q["name_mode"]
    name_q = q["name_q"]
    name_display = q["name_display"]
    tenant_status = q["tenant_status"]
    all_hist = q["all_hist"]
    name_list_mode = q["name_list_mode"]

    date_from, date_to, ym_year, ym_month = _resolve_list_period(
        today=today,
        use_ym=q["use_ym"],
        ym_year=q["ym_year"],
        ym_month=q["ym_month"],
        all_hist=all_hist,
        name_list_mode=name_list_mode,
        bunji1=bunji1,
        bunji2=bunji2,
        hosu=hosu,
        ipju_seq_f=ipju_seq_f,
        name_q=name_q,
        date_from=q["date_from"],
        date_to=q["date_to"],
    )

    rows = []
    payment_groups = []
    if name_list_mode:
        tenants = _search_tenants_by_name(name_q, tenant_status)
        if len(tenants) == 1:
            hit = _focus_single_tenant(tenants[0], name_q, today)
            bunji1 = hit["bunji1"]
            bunji2 = hit["bunji2"]
            hosu = hit["hosu"]
            ipju_seq_f = hit["ipju_seq_f"]
            name_display = hit["name_display"]
            date_from = hit["date_from"]
            date_to = hit["date_to"]
            ym_year = hit["ym_year"]
            ym_month = hit["ym_month"]
            all_hist = True
            name_list_mode = False
        elif len(tenants) >= 2:
            payment_groups = _name_match_payment_groups(tenants, date_from, date_to)

    if not name_list_mode:
        rows = _query_payment_rows(
            bunji1, bunji2, hosu, ipju_seq_f, date_from, date_to, all_hist
        )

    return render_template(
        "payments.html",
        payments=rows if not name_list_mode else [],
        payment_groups=payment_groups,
        name_list_mode=name_list_mode,
        is_fresh=False,
        buildings=buildings,
        rooms=rooms,
        years=years,
        filters={
            "bunji1": bunji1,
            "bunji2": bunji2,
            "hosu": hosu,
            "ipju_seq": ipju_seq_f,
            "name": name_display,
            "name_mode": "1" if name_mode else "",
            "tenant_status": tenant_status,
            "date_from": date_from,
            "date_to": date_to,
            "ym_year": ym_year,
            "ym_month": ym_month,
        },
    )


def _first_date_for_tenant(b1, b2, h, seq=""):
    """특정 입주자(주소·호·입주순번) 최초 입주일/수금일"""
    h = (h or "").strip().upper()
    b1 = _pad_bunji(b1)
    b2 = _pad_bunji(b2)
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
    return _iso_min_date(row["mn"])


def _recent_payments(bunji1="", bunji2="", hosu="", sukum_dt="", limit=80):
    """수금 등록 화면 하단: 오늘 입력(등록)한 수금 전부 표시.
    각 행에 hist_from/hist_to 를 붙여 클릭 시 세입자 전체 납부 내역으로 이동.
    """
    today = date.today().isoformat()
    rows = db.query(
        """
        SELECT s.sukum_dt, s.sukum_seq, s.bunji1, s.bunji2, s.hosu, s.ipju_seq,
               s.sukum_char, s.sukum_gb, s.manage_desc,
               s.su_sil_amt, s.su_dache_amt, s.sys_dt,
               c1.g_cd_nm AS char_nm, c2.g_cd_nm AS gb_nm,
               d.ipju_nm
        FROM sukum01 s
        LEFT JOIN gicho_code c1
          ON c1.g_cd='01' AND c1.g_sub_cd=s.sukum_char
        LEFT JOIN gicho_code c2
          ON c2.g_cd='02' AND c2.g_sub_cd=s.sukum_gb
        LEFT JOIN bd03_det d
          ON d.bunji1=s.bunji1 AND d.bunji2=s.bunji2
         AND d.hosu=s.hosu AND d.ipju_seq=s.ipju_seq
        WHERE s.sys_dt >= %s AND s.sys_dt < %s + INTERVAL 1 DAY
          AND (s.del_yn IS NULL OR s.del_yn='' OR s.del_yn='N')
        ORDER BY s.sys_dt DESC, s.sukum_dt DESC, CAST(s.sukum_seq AS UNSIGNED) DESC
        LIMIT %s
        """,
        (today + " 00:00:00", today, int(limit)),
    )
    cache = {}
    for r in rows or []:
        key = _tenant_key(r.get("bunji1"), r.get("bunji2"), r.get("hosu"), r.get("ipju_seq"))
        if key not in cache:
            first = _first_date_for_tenant(key[0], key[1], key[2], key[3])
            cache[key] = first or "2000-01-01"
        r["hist_from"] = cache[key]
        r["hist_to"] = today
    return rows


def _next_sukum_seq(sukum_dt, bunji1, bunji2, hosu):
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


def _lookup_current_tenant(bunji1, bunji2, hosu):
    """호실의 현재 입주자(거주 우선). 없으면 최신 이력 1건."""
    hosu = (hosu or "").strip().upper()
    if not (bunji1 and bunji2 and hosu):
        return None
    cols = """
        hosu, ipju_seq, ipju_nm, out_dt,
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


@app.route("/api/building")
@login_required
def api_building():
    """주소-주소2 가 bd01 에 등록된 건물인지 확인"""
    bunji1, bunji2 = _parse_bunji_src(request.args)
    display = fmt_bunji_pair(bunji1, bunji2)
    hosu = (request.args.get("hosu") or "").strip().upper()
    # 주소1(번지1)만 검사: 해당 앞자리로 등록 건물이 하나라도 있는지
    if bunji1 and not bunji2:
        hit = db.query_one(
            "SELECT bunji1 FROM bd01 WHERE bunji1=%s LIMIT 1",
            (bunji1,),
        )
        d1 = fmt_bunji(bunji1)
        if hit:
            return jsonify(
                {
                    "ok": True,
                    "found": True,
                    "bunji1_only": True,
                    "bunji1": bunji1,
                    "bunji2": "",
                    "display": d1,
                    "label": "",
                    "juso": "",
                    "message": "",
                    "room_found": None,
                    "hosu": hosu,
                }
            )
        return jsonify(
            {
                "ok": True,
                "found": False,
                "bunji1_only": True,
                "bunji1": bunji1,
                "bunji2": "",
                "display": d1,
                "label": "미등록 주소",
                "juso": "",
                "message": f"주소 「{d1}」 은(는) 등록된 건물이 없습니다.\n주소를 다시 확인하세요.",
                "room_found": None,
                "hosu": hosu,
            }
        )
    if not bunji1 or not bunji2:
        return jsonify(
            {
                "ok": True,
                "found": False,
                "bunji1": bunji1,
                "bunji2": bunji2,
                "display": display,
                "label": "",
                "juso": "",
                "message": "주소·주소2를 입력하세요.",
            }
        )
    b = db.query_one(
        "SELECT bunji1, bunji2, juso, owner_nm FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (bunji1, bunji2),
    )
    if b:
        juso = (b.get("juso") or "").strip()
        payload = {
            "ok": True,
            "found": True,
            "bunji1": bunji1,
            "bunji2": bunji2,
            "display": display,
            "juso": juso,
            "label": _building_label(bunji1, bunji2),
            "message": "",
            "room_found": None,
            "hosu": hosu,
        }
        # 호수까지 넘기면 호수 등록 여부도 검사
        if hosu:
            room = db.query_one(
                """
                SELECT hosu FROM bd03_m
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                """,
                (bunji1, bunji2, hosu),
            )
            if room:
                payload["room_found"] = True
            else:
                payload["room_found"] = False
                payload["message"] = (
                    f"호수 「{hosu}」 은(는) 주소 {display} 건물에 등록되어 있지 않습니다.\n"
                    "호수를 다시 확인해 주세요."
                )
        return jsonify(payload)
    return jsonify(
        {
            "ok": True,
            "found": False,
            "bunji1": bunji1,
            "bunji2": bunji2,
            "display": display,
            "juso": "",
            "label": "미등록 주소",
            "room_found": False if hosu else None,
            "hosu": hosu,
            "message": f"주소 {display} 은(는) 등록된 건물이 없습니다.\n주소를 다시 확인하세요.",
        }
    )


@app.route("/api/current_tenant")
@login_required
def api_current_tenant():
    """수금 등록: 호실 입력 시 현재 입주 순번 조회"""
    bunji1, bunji2 = _parse_bunji_src(request.args)
    hosu = (request.args.get("hosu") or "").strip().upper()
    row = _lookup_current_tenant(bunji1, bunji2, hosu)
    if not row:
        return jsonify(
            {
                "ok": False,
                "hosu": hosu,
                "ipju_seq": "",
                "ipju_nm": "",
                "bunji1": bunji1,
                "bunji2": bunji2,
                "misu_amt": 0,
                "misu_display": "",
                "month_misu_amt": 0,
                "month_misu_display": "",
                "prev_misu_amt": 0,
                "prev_misu_display": "",
                "rent_amt": 0,
                "manage_amt": 0,
                "monthly_amt": 0,
                "bojung_amt": 0,
                "yechi_amt": 0,
                "ipju_dt": "",
                "ipju_tel": "",
            }
        )
    seq = str(row.get("ipju_seq") or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    h = (row.get("hosu") or hosu).strip()
    rent = _to_int_amt(row.get("rent_amt"))
    manage = _to_int_amt(row.get("manage_amt"))
    monthly = rent + manage
    # 전월미수총액 ≈ 누적 미수
    prev_misu = _calc_misu_amt(
        bunji1,
        bunji2,
        h,
        seq,
        rent_amt=rent,
        manage_amt=manage,
        ipju_dt=row.get("ipju_dt"),
    )
    # 미수총액 = 이번 달 미입금액
    month_misu = _calc_month_misu_amt(
        bunji1,
        bunji2,
        h,
        seq,
        rent_amt=rent,
        manage_amt=manage,
    )
    tel = (row.get("ipju_tel1") or row.get("ipju_tel2") or "").strip()
    ipju_dt = row.get("ipju_dt")
    if isinstance(ipju_dt, datetime):
        ipju_dt_s = ipju_dt.strftime("%Y-%m-%d")
    elif isinstance(ipju_dt, date):
        ipju_dt_s = ipju_dt.isoformat()
    else:
        ipju_dt_s = str(ipju_dt or "")[:10]
    out_dt = row.get("out_dt")
    is_current = not out_dt or (
        isinstance(out_dt, datetime) and out_dt.year < 1000
    ) or (isinstance(out_dt, date) and out_dt.year < 1000)
    return jsonify(
        {
            "ok": True,
            "hosu": h,
            "ipju_seq": seq,
            "ipju_nm": (row.get("ipju_nm") or "").strip(),
            "current": bool(is_current),
            "bunji1": bunji1,
            "bunji2": bunji2,
            "rent_amt": rent,
            "manage_amt": manage,
            "monthly_amt": monthly,
            "bojung_amt": _to_int_amt(row.get("bojung_amt")),
            "yechi_amt": _to_int_amt(row.get("yechi_amt")),
            "ipju_dt": ipju_dt_s,
            "ipju_tel": tel,
            # 하위 호환: misu_amt = 전월미수(누적)
            "misu_amt": prev_misu,
            "misu_display": money(prev_misu),
            "prev_misu_amt": prev_misu,
            "prev_misu_display": money(prev_misu),
            "month_misu_amt": month_misu,
            "month_misu_display": money(month_misu),
        }
    )


@app.route("/api/payments/delete", methods=["POST"])
@login_required
def api_payments_delete():
    """수금 내역 삭제(del_yn='Y'). body: { items: [{sukum_dt,sukum_seq,bunji1,bunji2,hosu}, ...] }"""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"ok": False, "message": "삭제할 항목이 없습니다.", "deleted": 0})
    
    # 배치 처리: 단일 트랜잭션으로 모든 삭제 수행
    conn = db.get_conn()
    try:
        deleted = 0
        errors = []
        sabun = session.get("sabun") or ""
        
        with conn.cursor() as cur:
            for it in items:
                try:
                    sukum_dt = clamp_date_str(str(it.get("sukum_dt") or "")[:10])
                    sukum_seq = str(it.get("sukum_seq") or "").strip()
                    bunji1 = _pad_bunji(str(it.get("bunji1") or "").strip())
                    bunji2 = _pad_bunji(str(it.get("bunji2") or "").strip())
                    hosu = str(it.get("hosu") or "").strip().upper()
                    if not (sukum_dt and sukum_seq and bunji1 and bunji2 and hosu):
                        errors.append("키 누락")
                        continue
                    n = cur.execute(
                        """
                        UPDATE sukum01
                           SET del_yn='Y',
                               uid=%s,
                               sys_dt=NOW()
                         WHERE sukum_dt >= %s AND sukum_dt < %s + INTERVAL 1 DAY
                           AND sukum_seq=%s
                           AND bunji1=%s AND bunji2=%s
                           AND UPPER(TRIM(hosu))=%s
                           AND (del_yn IS NULL OR del_yn='' OR del_yn='N')
                        """,
                        (
                            sabun,
                            sukum_dt + " 00:00:00",
                            sukum_dt,
                            sukum_seq,
                            bunji1,
                            bunji2,
                            hosu,
                        ),
                    )
                    deleted += int(n or 0)
                except Exception as e:
                    errors.append(str(e))
        
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "message": f"DB 오류: {str(e)}", "deleted": 0})
    finally:
        conn.close()
    
    return jsonify(
        {
            "ok": deleted > 0,
            "deleted": deleted,
            "message": f"{deleted}건 삭제했습니다."
            if deleted
            else ("삭제 실패: " + ("; ".join(errors[:3]) if errors else "대상 없음")),
        }
    )


def _payment_form_codes():
    chars = db.query(
        """
        SELECT g_sub_cd, g_cd_nm FROM gicho_code
        WHERE g_cd='01' AND g_sub_cd <> '00'
        ORDER BY g_sub_cd
        """
    )
    gbs = db.query(
        """
        SELECT g_sub_cd, g_cd_nm FROM gicho_code
        WHERE g_cd='02' AND g_sub_cd <> '00'
        ORDER BY g_sub_cd
        """
    )
    return chars, gbs


def _tenants_in_building(bunji1, bunji2):
    if not (bunji1 and bunji2):
        return []
    return db.query(
        """
        SELECT hosu, ipju_seq, ipju_nm, out_dt
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s
        ORDER BY (out_dt IS NULL) DESC, hosu, ipju_seq DESC
        """,
        (bunji1, bunji2),
    )


def _render_payment_new(buildings, rooms, chars, gbs, form, tenants, recent):
    return render_template(
        "payment_new.html",
        buildings=buildings,
        rooms=rooms,
        chars=chars,
        gbs=gbs,
        form=form,
        tenants=tenants,
        recent_payments=recent,
        building_label=_building_label(form.get("bunji1"), form.get("bunji2")),
    )


@app.route("/payments/new", methods=["GET", "POST"])
@login_required
def payment_new():
    buildings, rooms = _buildings_and_rooms()
    chars, gbs = _payment_form_codes()

    arg_b1, arg_b2 = _parse_bunji_src(request.args)
    pre = {
        "bunji1": arg_b1,
        "bunji2": arg_b2,
        "hosu": (request.args.get("hosu") or "").strip().upper(),
        "ipju_seq": (request.args.get("ipju_seq") or "").strip(),
        "sukum_dt": date.today().isoformat(),
        "sukum_char": "01",
        "sukum_gb": "03",  # 기본: 통장입금
        "su_sil_amt": "",
        "su_dache_amt": "",
        "manage_desc": "",
    }
    # form 금액은 템플릿에서 |money 로 표시

    tenants = _tenants_in_building(pre["bunji1"], pre["bunji2"])
    if pre["bunji1"] and pre["bunji2"]:
        # 호실만 있고 순번 없으면 현재 입주 순번 자동
        if pre["hosu"] and not pre["ipju_seq"]:
            for t in tenants:
                if (t.get("hosu") or "").strip().upper() != pre["hosu"]:
                    continue
                if t.get("out_dt"):
                    continue
                seq = (t.get("ipju_seq") or "").strip()
                if seq:
                    pre["ipju_seq"] = seq.zfill(2)
                    break
        elif pre["ipju_seq"]:
            pre["ipju_seq"] = pre["ipju_seq"].zfill(2)

    if request.method == "POST":
        bunji1, bunji2 = _parse_bunji_src(request.form)
        hosu = (request.form.get("hosu") or "").strip().upper()
        ipju_seq = (request.form.get("ipju_seq") or "").strip().zfill(2)
        sukum_dt = clamp_date_str((request.form.get("sukum_dt") or "").strip())
        sukum_char = (request.form.get("sukum_char") or "01").strip().zfill(2)
        sukum_gb = (request.form.get("sukum_gb") or "03").strip().zfill(2)
        manage_desc = (request.form.get("manage_desc") or "").strip()
        amount_raw = (request.form.get("su_sil_amt") or "0").replace(",", "").strip()
        dache_raw = (request.form.get("su_dache_amt") or "0").replace(",", "").strip()

        pre.update(
            {
                "bunji1": bunji1,
                "bunji2": bunji2,
                "hosu": hosu,
                "ipju_seq": ipju_seq,
                "sukum_dt": sukum_dt,
                "sukum_char": sukum_char,
                "sukum_gb": sukum_gb,
                "su_sil_amt": amount_raw,
                "su_dache_amt": dache_raw,
                "manage_desc": manage_desc,
            }
        )
        tenants = _tenants_in_building(bunji1, bunji2)
        recent = _recent_payments()

        try:
            amount = int(amount_raw or 0)
            dache_amt = int(dache_raw or 0)
        except ValueError:
            flash("금액은 숫자로 입력하세요.", "err")
            return _render_payment_new(
                buildings, rooms, chars, gbs, pre, tenants, recent
            )

        if not (bunji1 and bunji2 and hosu and ipju_seq and sukum_dt):
            flash("건물(주소·주소2), 호실, 입주순번, 수금일은 필수입니다.", "err")
            return _render_payment_new(
                buildings, rooms, chars, gbs, pre, tenants, recent
            )

        # 순번: 같은 날 + 같은 건물·호실만 카운트
        sukum_seq = _next_sukum_seq(sukum_dt, bunji1, bunji2, hosu)

        try:
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
                    sukum_dt + " 00:00:00",
                    sukum_seq,
                    bunji1,
                    bunji2,
                    hosu,
                    ipju_seq,
                    sukum_char,
                    sukum_gb,
                    manage_desc,
                    amount,
                    dache_amt,
                    session.get("sabun") or "",
                ),
            )
        except Exception as e:
            flash(f"저장 실패: {e}", "err")
            return _render_payment_new(
                buildings, rooms, chars, gbs, pre, tenants, recent
            )

        # 같은 수금 등록 화면에 머무름 + 하단 목록에 방금 입력 표시
        flash(f"수금이 등록되었습니다. (순번 {sukum_seq})", "ok")
        return redirect(
            url_for(
                "payment_new",
                bunji1=bunji1,
                bunji2=bunji2,
                hosu=hosu,
                ipju_seq=ipju_seq,
            )
        )

    recent = _recent_payments()
    return _render_payment_new(buildings, rooms, chars, gbs, pre, tenants, recent)
