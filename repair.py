"""수리 관리 화면.

수리내역조회, 수리내역등록/수정/삭제 라우트와 그 전용 도우미 함수들을
모아둔 모듈입니다.
"""
from datetime import date
from threading import Lock

from flask import flash, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    CURRENT_TENANT_SQL as _CURRENT_TENANT_SQL,
    building_label as _building_label,
    fmt_bunji,
    fmt_date,
    login_required,
    money,
    pad_bunji as _pad_bunji,
    resolve_hosu as _resolve_hosu,
    is_common_hosu as _is_common_hosu,
    make_pager as _make_pager,
    parse_money as _parse_money,
    require_write_access,
    table_columns as _table_columns,
)


# A4 가로(높이 210mm) + 제목·기간행. 32줄이면 크롬이 한 장을 둘로 쪼개
# 날짜가 이어지지 않은 것처럼 보임.
_PRINT_ROWS_PER_PAGE = 20
_REPAIR_PRINT_COL_READY = False
_REPAIR_COMMON_HOSU_READY = False
_REPAIR_SCHEMA_LOCK = Lock()


def _repair_list_filters():
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    suri_desc = (request.args.get("suri_desc") or "").strip()
    date_from = (request.args.get("date_from") or "").strip() or month_start
    date_to = (request.args.get("date_to") or "").strip() or today.isoformat()
    return {
        "bunji1": bunji1,
        "bunji2": bunji2,
        "hosu": hosu,
        "suri_desc": suri_desc,
        "date_from": date_from,
        "date_to": date_to,
    }


def _repair_list_sql(f):
    where = ["s.suri_dt >= %s", "s.suri_dt < DATE_ADD(%s, INTERVAL 1 DAY)"]
    args = [f["date_from"], f["date_to"]]
    if f["bunji1"]:
        where.append("s.bunji1=%s")
        args.append(f["bunji1"])
    if f["bunji2"]:
        where.append("s.bunji2=%s")
        args.append(f["bunji2"])
    if f["hosu"]:
        where.append("UPPER(TRIM(s.hosu))=%s")
        args.append(f["hosu"])
    if f["suri_desc"]:
        where.append("s.suri_desc LIKE %s")
        args.append("%" + f["suri_desc"] + "%")
    where_sql = " AND ".join(where)
    join_sql = """
        FROM bd05_suri s
        LEFT JOIN bd03_det d
          ON d.bunji1=s.bunji1 AND d.bunji2=s.bunji2
         AND d.hosu=s.hosu AND d.ipju_seq=s.ipju_seq
        WHERE
    """
    return join_sql, where_sql, args


@app.route("/repairs")
@login_required
def repairs():
    """
    수리내역조회 (XP「수리내역조회」).
    UX: 주소·호수 먼저, 기준일자, 수리내역.
    """
    _ensure_blank_hosu_common()
    f = _repair_list_filters()
    ran = (
        "date_from" in request.args
        or "bunji1" in request.args
        or "hosu" in request.args
        or "suri_desc" in request.args
    )

    results = []
    pager = None
    if ran:
        join_sql, where_sql, args = _repair_list_sql(f)
        total = int(
            (db.query_one(f"SELECT COUNT(*) AS c {join_sql} {where_sql}", args) or {}).get("c")
            or 0
        )
        pager = _make_pager(total)
        if total:
            results = db.query(
                f"""
                SELECT s.suri_dt, s.suri_seq, s.bunji1, s.bunji2, s.hosu, s.ipju_seq,
                       s.suri_desc, s.suri_won_amt, s.ipjuja_budam, s.owner_budam, s.manage_budam,
                       d.ipju_nm, d.ipju_dt
                {join_sql} {where_sql}
                ORDER BY s.suri_dt DESC, s.bunji1, s.bunji2, s.hosu, s.suri_seq
                LIMIT %s OFFSET %s
                """,
                args + [pager["per_page"], pager["offset"]],
            )

    return render_template(
        "repairs.html",
        filters=f,
        results=results,
        ran=ran,
        pager=pager,
    )


@app.route("/repairs/print")
@login_required
def repairs_print():
    """수리내역조회 인쇄 — 화면과 같은 필터로 전체 건."""
    _ensure_blank_hosu_common()
    f = _repair_list_filters()
    join_sql, where_sql, args = _repair_list_sql(f)
    rows = db.query(
        f"""
        SELECT s.suri_dt, s.suri_seq, s.bunji1, s.bunji2, s.hosu, s.ipju_seq,
               s.suri_desc, s.suri_won_amt, s.ipjuja_budam, s.owner_budam, s.manage_budam,
               d.ipju_nm, d.ipju_dt
        {join_sql} {where_sql}
        ORDER BY s.suri_dt ASC, s.bunji1, s.bunji2, s.hosu, s.suri_seq
        """,
        args,
    ) or []
    tot_won = sum(int(r.get("suri_won_amt") or 0) for r in rows)
    tot_ipjuja = sum(int(r.get("ipjuja_budam") or 0) for r in rows)
    tot_owner = sum(int(r.get("owner_budam") or 0) for r in rows)
    tot_manage = sum(int(r.get("manage_budam") or 0) for r in rows)
    pages = [
        rows[i : i + _PRINT_ROWS_PER_PAGE]
        for i in range(0, len(rows), _PRINT_ROWS_PER_PAGE)
    ] or [[]]
    return render_template(
        "repairs_print.html",
        building_name=_building_label(f["bunji1"], f["bunji2"]) if f["bunji1"] and f["bunji2"] else "",
        addr_label=(
            f"{fmt_bunji(f['bunji1'])}-{fmt_bunji(f['bunji2'])}"
            if f["bunji1"] and f["bunji2"]
            else ""
        ),
        filters=f,
        pages=pages,
        total_pages=len(pages),
        total_count=len(rows),
        tot_won=tot_won,
        tot_ipjuja=tot_ipjuja,
        tot_owner=tot_owner,
        tot_manage=tot_manage,
    )


def _empty_repair_form():
    return {
        "mode": "new",
        "suri_dt": date.today().isoformat(),
        "suri_seq": "",
        "bunji1": "",
        "bunji2": "",
        "hosu": "",
        "ipju_seq": "",
        "ipju_nm": "",
        "ipju_tel": "",
        "ipju_dt": "",
        "suri_desc": "",
        "suri_won_amt": "0",
        "owner_budam": "0",
        "ipjuja_budam": "0",
        "manage_budam": "0",
        "ipju_su_churi": "N",  # N=미수, Y=동시수금
        "js_print_yn": "Y",  # 기본 정산서 출력. 해제하면 건물주 0원은 안 냄
        "orig_dt": "",
        "orig_seq": "",
        "orig_b1": "",
        "orig_b2": "",
        "orig_hosu": "",
    }


def _ensure_js_print_col_once():
    if "js_print_yn" in _table_columns("bd05_suri"):
        return
    try:
        db.execute(
            "ALTER TABLE bd05_suri ADD COLUMN js_print_yn char(1) NOT NULL DEFAULT 'N'"
        )
    except Exception:
        pass


def _ensure_blank_hosu_common_once():
    """호수 없는 옛 수리 내역은 공용."""
    need = db.query_one(
        """
        SELECT 1 AS ok FROM bd05_suri
        WHERE TRIM(hosu)='' OR hosu IN ('00','000')
           OR UPPER(TRIM(hosu)) IN ('COM','NONE')
        LIMIT 1
        """
    )
    if not need:
        return
    db.execute(
        """
        UPDATE bd05_suri
        SET hosu='공용', ipju_seq='00'
        WHERE TRIM(hosu)='' OR hosu IN ('00','000')
           OR UPPER(TRIM(hosu)) IN ('COM','NONE')
        """
    )


def _ensure_js_print_col():
    """Run the optional-column compatibility migration once per worker."""
    global _REPAIR_PRINT_COL_READY
    if _REPAIR_PRINT_COL_READY:
        return
    with _REPAIR_SCHEMA_LOCK:
        if _REPAIR_PRINT_COL_READY:
            return
        _ensure_js_print_col_once()
        _REPAIR_PRINT_COL_READY = True


def _ensure_blank_hosu_common():
    """Normalize legacy common-room markers once per worker."""
    global _REPAIR_COMMON_HOSU_READY
    if _REPAIR_COMMON_HOSU_READY:
        return
    with _REPAIR_SCHEMA_LOCK:
        if _REPAIR_COMMON_HOSU_READY:
            return
        try:
            _ensure_blank_hosu_common_once()
        finally:
            _REPAIR_COMMON_HOSU_READY = True


def _repair_next_seq(suri_dt):
    """같은 수리일자의 전역 순번. PK가 (suri_dt, suri_seq) 이라 호실별 채번하면 중복난다."""
    day = (suri_dt or "")[:10]
    row = db.query_one(
        """
        SELECT MAX(CAST(suri_seq AS UNSIGNED)) AS mx
        FROM bd05_suri
        WHERE suri_dt >= %s AND suri_dt < %s + INTERVAL 1 DAY
        """,
        (day + " 00:00:00", day),
    )
    mx = int((row or {}).get("mx") or 0)
    nxt = mx + 1
    if nxt > 99:
        raise ValueError("그날 수리 순번이 99를 넘었습니다.")
    return str(nxt).zfill(2)


def _recent_repairs():
    """오늘 등록(sys_dt)한 수리. 수금 등록의 오늘 목록과 같은 방식."""
    today = date.today().isoformat()
    day_from = today + " 00:00:00"
    total = int(
        (
            db.query_one(
                """
                SELECT COUNT(*) AS c
                FROM bd05_suri
                WHERE sys_dt >= %s AND sys_dt < %s + INTERVAL 1 DAY
                """,
                (day_from, today),
            )
            or {}
        ).get("c")
        or 0
    )
    pager = _make_pager(total)
    rows = []
    if total:
        rows = db.query(
            """
            SELECT s.suri_dt, s.suri_seq, s.bunji1, s.bunji2, s.hosu, s.ipju_seq,
                   s.suri_desc, s.suri_won_amt, s.ipjuja_budam, s.owner_budam,
                   s.manage_budam, s.ipju_su_churi, s.sys_dt,
                   d.ipju_nm, d.ipju_dt
            FROM bd05_suri s
            LEFT JOIN bd03_det d
              ON d.bunji1=s.bunji1 AND d.bunji2=s.bunji2
             AND d.hosu=s.hosu AND d.ipju_seq=s.ipju_seq
            WHERE s.sys_dt >= %s AND s.sys_dt < %s + INTERVAL 1 DAY
            ORDER BY s.sys_dt DESC, s.suri_dt DESC, CAST(s.suri_seq AS UNSIGNED) DESC
            LIMIT %s OFFSET %s
            """,
            (day_from, today, pager["per_page"], pager["offset"]),
        )
    return rows, pager


def _lookup_tenant_for_repair(bunji1, bunji2, hosu, ipju_seq=""):
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    h = (hosu or "").strip().upper()
    seq = (ipju_seq or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    if not (b1 and b2 and h):
        return None
    if seq:
        t = db.query_one(
            """
            SELECT ipju_seq, ipju_nm, ipju_tel1, ipju_tel2, ipju_tel3, ipju_dt
            FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (b1, b2, h, seq),
        )
        if t:
            return t
    t = db.query_one(
        f"""
        SELECT ipju_seq, ipju_nm, ipju_tel1, ipju_tel2, ipju_tel3, ipju_dt
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
          AND {_CURRENT_TENANT_SQL.replace('d.', '')}
        ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (b1, b2, h),
    )
    if t:
        return t
    return None


@app.route("/repairs/new", methods=["GET", "POST"])
@login_required
@require_write_access
def repair_new():
    """수리내역등록 (XP「수리 이력 등록」)."""
    _ensure_js_print_col()
    _ensure_blank_hosu_common()
    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        if action == "new":
            return redirect(url_for("repair_new"))

        suri_dt = (request.form.get("suri_dt") or "").strip()[:10]
        bunji1 = _pad_bunji(request.form.get("bunji1"))
        bunji2 = _pad_bunji(request.form.get("bunji2"))
        hosu = _resolve_hosu(bunji1, bunji2, request.form.get("hosu"))
        ipju_seq = (request.form.get("ipju_seq") or "").strip()
        if ipju_seq.isdigit():
            ipju_seq = ipju_seq.zfill(2)
        desc = (request.form.get("suri_desc") or "").strip()
        def _xp_won(n):
            n = int(n or 0)
            return n * 10000 if 0 < n < 10000 else n

        won = _xp_won(_parse_money(request.form.get("suri_won_amt")) or 0)
        owner_in = _parse_money(request.form.get("owner_budam"))
        ipjuja_in = _parse_money(request.form.get("ipjuja_budam"))
        manage_in = _parse_money(request.form.get("manage_budam"))
        owner = _xp_won(owner_in) if owner_in is not None else 0
        ipjuja = _xp_won(ipjuja_in) if ipjuja_in is not None else 0
        manage = _xp_won(manage_in) if manage_in is not None else 0
        if won <= 0 and owner > 0:
            won = owner
        # 세 칸이 다 0이면 건물주. 아니면 0은 0으로 두고 빈 잔액은 입주자→관리실
        if owner == 0 and ipjuja == 0 and manage == 0:
            owner = won
        elif won > 0 and owner + ipjuja + manage != won:
            rest = won - owner - ipjuja - manage
            if rest > 0:
                if ipjuja == 0:
                    ipjuja = rest
                elif manage == 0:
                    manage = rest
        su_churi = (request.form.get("ipju_su_churi") or "N").strip().upper()
        if su_churi not in ("N", "Y"):
            su_churi = "N"
        js_print = "Y" if (request.form.get("js_print_yn") or "").strip().upper() == "Y" else "N"
        mode = (request.form.get("mode") or "new").strip()
        orig_dt = (request.form.get("orig_dt") or "").strip()[:10]
        orig_seq = (request.form.get("orig_seq") or "").strip()
        orig_b1 = _pad_bunji(request.form.get("orig_b1"))
        orig_b2 = _pad_bunji(request.form.get("orig_b2"))
        orig_hosu = (request.form.get("orig_hosu") or "").strip().upper()
        uid = session.get("sabun") or ""

        def _back(**extra):
            kw = {
                "bunji1": fmt_bunji(bunji1) if bunji1 else "",
                "bunji2": fmt_bunji(bunji2) if bunji2 else "",
                "hosu": hosu,
                "suri_dt": suri_dt,
            }
            kw.update(extra)
            return redirect(url_for("repair_new", **{k: v for k, v in kw.items() if v}))

        if action == "delete":
            if not (orig_dt and orig_seq and orig_b1 and orig_b2 and orig_hosu):
                flash("삭제할 항목을 목록·수정 화면에서 선택하세요.", "err")
                return redirect(url_for("repair_new"))
            try:
                n = db.execute(
                    """
                    DELETE FROM bd05_suri
                    WHERE suri_dt=%s AND suri_seq=%s
                      AND bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                    """,
                    (orig_dt, orig_seq.zfill(2), orig_b1, orig_b2, orig_hosu),
                )
                flash("삭제했습니다." if n else "삭제할 자료를 찾지 못했습니다.", "ok" if n else "err")
            except Exception as e:
                flash(f"삭제 실패: {e}", "err")
            return redirect(url_for("repairs", date_from=orig_dt[:7] + "-01" if orig_dt else None))

        if not suri_dt or len(suri_dt) < 10:
            flash("수리일자를 입력하세요.", "err")
            return _back()
        if not bunji1 or not bunji2:
            flash("주소를 입력하세요.", "err")
            return _back()
        if not hosu:
            flash("호수를 입력하세요.", "err")
            return _back()
        if not desc:
            flash("수리내역을 입력하세요.", "err")
            return _back()
        if won <= 0:
            flash("수리총액을 입력하세요.", "err")
            return _back()
        if ipjuja < 0 or manage < 0 or owner < 0 or owner + ipjuja + manage != won:
            flash("건물주+입주자+관리실 합이 수리총액과 다릅니다.", "err")
            return _back()

        # 주소·호수 등록 여부 (없는 호수 01 등 저장 차단)
        bld = db.query_one(
            "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
            (bunji1, bunji2),
        )
        if not bld:
            flash("등록되지 않은 주소입니다. 주소를 확인하세요.", "err")
            return _back()
        common = _is_common_hosu(hosu)
        if common:
            hosu = "공용"
        else:
            room = db.query_one(
                """
                SELECT hosu FROM bd03_m
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                """,
                (bunji1, bunji2, hosu),
            )
            if room:
                hosu = (room.get("hosu") or hosu).strip().upper()
            if not room:
                flash("등록되지 않은 호수입니다. 호수를 확인하세요.", "err")
                return _back()

        # 입주 순번 자동. 공용은 세입자 없음
        if common:
            ipju_seq = "00"
        elif not ipju_seq:
            t = _lookup_tenant_for_repair(bunji1, bunji2, hosu)
            if t:
                ipju_seq = str(t.get("ipju_seq") or "").zfill(2)
        if not ipju_seq:
            ipju_seq = "00"

        try:
            if mode == "edit" and orig_dt and orig_seq and orig_b1 and orig_b2 and orig_hosu:
                db.execute(
                    """
                    UPDATE bd05_suri SET
                      suri_dt=%s, bunji1=%s, bunji2=%s, hosu=%s, ipju_seq=%s,
                      suri_desc=%s, suri_won_amt=%s,
                      owner_budam=%s, ipjuja_budam=%s, manage_budam=%s,
                      ipju_su_churi=%s, js_print_yn=%s, uid=%s, sys_dt=NOW()
                    WHERE suri_dt=%s AND suri_seq=%s
                      AND bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                    """,
                    (
                        suri_dt,
                        bunji1,
                        bunji2,
                        hosu,
                        ipju_seq,
                        desc[:50],
                        won,
                        owner,
                        ipjuja,
                        manage,
                        su_churi,
                        js_print,
                        uid,
                        orig_dt,
                        orig_seq.zfill(2),
                        orig_b1,
                        orig_b2,
                        orig_hosu,
                    ),
                )
                flash("수정 저장했습니다.", "ok")
                seq = orig_seq.zfill(2)
            else:
                seq = _repair_next_seq(suri_dt)
                db.execute(
                    """
                    INSERT INTO bd05_suri (
                      suri_dt, suri_seq, bunji1, bunji2, hosu, ipju_seq,
                      suri_desc, suri_won_amt, owner_budam, ipjuja_budam, manage_budam,
                      biyong_gb, ipju_su_churi, js_print_yn, uid, sys_dt
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,
                      'A',%s,%s,%s,NOW()
                    )
                    """,
                    (
                        suri_dt,
                        seq,
                        bunji1,
                        bunji2,
                        hosu,
                        ipju_seq,
                        desc[:50],
                        won,
                        owner,
                        ipjuja,
                        manage,
                        su_churi,
                        js_print,
                        uid,
                    ),
                )
                flash(f"등록했습니다. (순번 {seq})", "ok")
        except Exception as e:
            flash(f"저장 실패: {e}", "err")
            return _back()

        # 저장 후 수정 화면으로 다시 열지 않음 (순번·내용이 그대로 남아 헷갈림 방지)
        # 신규 등록 폼으로 돌아가 다음 입력을 받음. 같은 주소·호수는 유지.
        if mode == "edit":
            return redirect(
                url_for(
                    "repair_new",
                    suri_dt=suri_dt,
                    bunji1=fmt_bunji(bunji1),
                    bunji2=fmt_bunji(bunji2),
                    hosu=hosu,
                    suri_seq=seq,
                )
            )
        return redirect(
            url_for(
                "repair_new",
                suri_dt=suri_dt,
                bunji1=fmt_bunji(bunji1),
                bunji2=fmt_bunji(bunji2),
                hosu=hosu,
            )
        )

    # GET
    form = _empty_repair_form()
    form["suri_dt"] = (request.args.get("suri_dt") or form["suri_dt"])[:10]
    form["bunji1"] = _pad_bunji(request.args.get("bunji1"))
    form["bunji2"] = _pad_bunji(request.args.get("bunji2"))
    form["hosu"] = _resolve_hosu(
        form["bunji1"], form["bunji2"], request.args.get("hosu")
    )
    form["suri_desc"] = (request.args.get("suri_desc") or "").strip()
    edit_seq = (request.args.get("suri_seq") or "").strip()
    building_label = ""
    tenant_hint = ""

    if form["bunji1"] and form["bunji2"] and form["hosu"] and edit_seq:
        row = db.query_one(
            """
            SELECT * FROM bd05_suri
            WHERE suri_dt=%s AND suri_seq=%s
              AND bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
            """,
            (
                form["suri_dt"],
                edit_seq.zfill(2),
                form["bunji1"],
                form["bunji2"],
                form["hosu"],
            ),
        )
        if row:
            form = {
                "mode": "edit",
                "suri_dt": fmt_date(row.get("suri_dt")),
                "suri_seq": str(row.get("suri_seq") or "").zfill(2),
                "bunji1": row.get("bunji1") or "",
                "bunji2": row.get("bunji2") or "",
                "hosu": (row.get("hosu") or "").strip(),
                "ipju_seq": str(row.get("ipju_seq") or "").zfill(2),
                "ipju_nm": "",
                "ipju_tel": "",
                "ipju_dt": "",
                "suri_desc": row.get("suri_desc") or "",
                "suri_won_amt": money(row.get("suri_won_amt")) or "0",
                "owner_budam": money(row.get("owner_budam")) or "0",
                "ipjuja_budam": money(row.get("ipjuja_budam")) or "0",
                "manage_budam": money(row.get("manage_budam")) or "0",
                "ipju_su_churi": (row.get("ipju_su_churi") or "N").strip().upper() or "N",
                "js_print_yn": (row.get("js_print_yn") or "Y").strip().upper() or "Y",
                "orig_dt": fmt_date(row.get("suri_dt")),
                "orig_seq": str(row.get("suri_seq") or "").zfill(2),
                "orig_b1": row.get("bunji1") or "",
                "orig_b2": row.get("bunji2") or "",
                "orig_hosu": (row.get("hosu") or "").strip(),
            }

    if form["bunji1"] and form["bunji2"]:
        building_label = _building_label(form["bunji1"], form["bunji2"])
    if _is_common_hosu(form.get("hosu")):
        form["hosu"] = "공용"
        form["ipju_seq"] = "00"
        form["ipju_nm"] = "공용"
        form["ipju_tel"] = ""
        form["ipju_dt"] = ""
        tenant_hint = "공용"
    elif form["bunji1"] and form["bunji2"] and form["hosu"]:
        t = _lookup_tenant_for_repair(
            form["bunji1"], form["bunji2"], form["hosu"], form.get("ipju_seq")
        )
        if t:
            form["ipju_seq"] = str(t.get("ipju_seq") or "").zfill(2)
            form["ipju_nm"] = (t.get("ipju_nm") or "").strip()
            form["ipju_tel"] = (
                t.get("ipju_tel1") or t.get("ipju_tel3") or t.get("ipju_tel2") or ""
            ).strip()
            form["ipju_dt"] = fmt_date(t.get("ipju_dt")) or ""
            tenant_hint = form["ipju_nm"]
        else:
            form["ipju_seq"] = "00"
            form["ipju_nm"] = "공실"
            form["ipju_tel"] = ""
            form["ipju_dt"] = ""
            tenant_hint = "공실"

    recent, pager = _recent_repairs()
    return render_template(
        "repair_form.html",
        form=form,
        building_label=building_label,
        tenant_hint=tenant_hint,
        recent_repairs=recent,
        pager=pager,
    )
