"""입주자 이력 조회 화면.

입주자관리 메뉴의 검색·페이지네이션 라우트를 모아둔 모듈입니다.
"""
from flask import render_template, request

import db
from app_instance import app
from utils import (
    build_pager as _build_pager,
    login_required,
    pad_bunji as _pad_bunji,
    parse_bunji_input as _parse_bunji_input,
    tenant_is_past_out as _tenant_is_past_out,
)


@app.route("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    # 주소·주소2 분리 입력 (구 링크용 bunji=508-88 도 허용)
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    bunji_legacy = (request.args.get("bunji") or "").strip()
    if bunji_legacy and not (bunji1 or bunji2):
        try:
            bunji1, bunji2 = _parse_bunji_input(bunji_legacy)
        except Exception:
            bunji1, bunji2 = "", ""
    hosu = (request.args.get("hosu") or "").strip().upper()
    ipju_seq = (request.args.get("ipju_seq") or "").strip()
    tenant_status = (request.args.get("tenant_status") or "all").strip().lower()
    if tenant_status not in ("current", "past", "all"):
        tenant_status = "all"

    per_page = 30
    try:
        page = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    results = []
    total = 0
    total_pages = 1
    has_filter = bool(q or bunji1 or bunji2 or hosu or ipju_seq or tenant_status != "all")
    if has_filter:
        where = []
        args = []
        if q:
            like = f"%{q}%"
            where.append("(ipju_nm LIKE %s OR ipju_tel1 LIKE %s OR ipju_tel2 LIKE %s OR ipju_tel3 LIKE %s)")
            args.extend([like, like, like, like])
        if bunji1:
            where.append("bunji1=%s")
            args.append(bunji1)
        if bunji2:
            where.append("bunji2=%s")
            args.append(bunji2)
        if hosu:
            where.append("UPPER(TRIM(hosu))=%s")
            args.append(hosu)
        if ipju_seq:
            where.append("ipju_seq=%s")
            args.append(ipju_seq.zfill(2))
        if tenant_status == "current":
            where.append("(out_dt IS NULL OR out_dt < '1000-01-01')")
        elif tenant_status == "past":
            where.append("out_dt IS NOT NULL AND out_dt >= '1000-01-01'")

        where_sql = " AND ".join(where)
        count_row = db.query_one(
            f"SELECT COUNT(*) AS c FROM bd03_det WHERE {where_sql}",
            args,
        )
        total = int((count_row or {}).get("c") or 0)
        total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * per_page

        sql = f"""
            SELECT bunji1, bunji2, hosu, ipju_seq, ipju_nm, ipju_tel1, ipju_tel2,
                   ipju_dt, out_dt, rent_amt, manage_amt, bojung_amt
            FROM bd03_det
            WHERE {where_sql}
            ORDER BY (out_dt IS NULL) DESC, ipju_dt DESC
            LIMIT %s OFFSET %s
        """
        results = db.query(sql, args + [per_page, offset])
        for r in results or []:
            r["is_past"] = _tenant_is_past_out(r.get("out_dt"))

    pager = _build_pager(page, total_pages, page_block_size=6)
    pager["total"] = total
    pager["per_page"] = per_page

    return render_template(
        "search.html",
        q=q,
        bunji1=bunji1,
        bunji2=bunji2,
        hosu=hosu,
        ipju_seq=ipju_seq,
        tenant_status=tenant_status,
        results=results,
        total=total,
        total_pages=total_pages,
        page=page,
        per_page=per_page,
        pager=pager,
        has_filter=has_filter,
    )
