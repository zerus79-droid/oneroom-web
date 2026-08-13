"""수리 관리 화면.

수리내역조회, 수리내역등록/수정/삭제 라우트와 그 전용 도우미 함수들을
모아둔 모듈입니다.
"""
from datetime import date

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
    parse_money as _parse_money,
)


@app.route("/repairs")
@login_required
def repairs():
    """
    수리내역조회 (XP「수리내역조회」).
    UX: 주소·호수·성명 먼저, 기준일자는 필터 맨 아래.
    """
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    month_end = today.isoformat()

    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    name = (request.args.get("name") or "").strip()
    date_from = (request.args.get("date_from") or "").strip() or month_start
    date_to = (request.args.get("date_to") or "").strip() or month_end

    # 조회 실행 여부: 아무 조건이 있어도 GET 파라미터 있으면 / 항상 기간 기본값으로 조회
    ran = "date_from" in request.args or "bunji1" in request.args or "name" in request.args or "hosu" in request.args

    results = []
    if ran:
        where = ["s.suri_dt >= %s", "s.suri_dt < DATE_ADD(%s, INTERVAL 1 DAY)"]
        args = [date_from, date_to]
        if bunji1:
            where.append("s.bunji1=%s")
            args.append(bunji1)
        if bunji2:
            where.append("s.bunji2=%s")
            args.append(bunji2)
        if hosu:
            where.append("UPPER(TRIM(s.hosu))=%s")
            args.append(hosu)
        if name:
            where.append("d.ipju_nm LIKE %s")
            args.append(f"%{name}%")
        sql = f"""
            SELECT s.suri_dt, s.suri_seq, s.bunji1, s.bunji2, s.hosu, s.ipju_seq,
                   s.suri_desc, s.suri_won_amt, s.ipjuja_budam, s.owner_budam, s.manage_budam,
                   d.ipju_nm, d.ipju_dt
            FROM bd05_suri s
            LEFT JOIN bd03_det d
              ON d.bunji1=s.bunji1 AND d.bunji2=s.bunji2
             AND d.hosu=s.hosu AND d.ipju_seq=s.ipju_seq
            WHERE {" AND ".join(where)}
            ORDER BY s.suri_dt DESC, s.bunji1, s.bunji2, s.hosu, s.suri_seq
            LIMIT 500
        """
        results = db.query(sql, args)

    return render_template(
        "repairs.html",
        filters={
            "bunji1": bunji1,
            "bunji2": bunji2,
            "hosu": hosu,
            "name": name,
            "date_from": date_from,
            "date_to": date_to,
        },
        results=results,
        ran=ran,
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
        "suri_desc": "",
        "suri_won_amt": "0",
        "owner_budam": "0",
        "ipjuja_budam": "0",
        "manage_budam": "0",
        "ipju_su_churi": "N",  # N=미수, Y=동시수금
        "orig_dt": "",
        "orig_seq": "",
        "orig_b1": "",
        "orig_b2": "",
        "orig_hosu": "",
    }


def _repair_next_seq(suri_dt, bunji1, bunji2, hosu):
    row = db.query_one(
        """
        SELECT MAX(CAST(suri_seq AS UNSIGNED)) AS mx
        FROM bd05_suri
        WHERE suri_dt=%s AND bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
        """,
        (suri_dt, bunji1, bunji2, (hosu or "").strip().upper()),
    )
    mx = int((row or {}).get("mx") or 0)
    return str(mx + 1).zfill(2)


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
            SELECT ipju_seq, ipju_nm, ipju_tel1, ipju_tel2, ipju_tel3
            FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (b1, b2, h, seq),
        )
        if t:
            return t
    t = db.query_one(
        f"""
        SELECT ipju_seq, ipju_nm, ipju_tel1, ipju_tel2, ipju_tel3
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
    return db.query_one(
        """
        SELECT ipju_seq, ipju_nm, ipju_tel1, ipju_tel2, ipju_tel3
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
        ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (b1, b2, h),
    )


@app.route("/repairs/new", methods=["GET", "POST"])
@login_required
def repair_new():
    """수리내역등록 (XP「수리 이력 등록」)."""
    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        if action == "new":
            return redirect(url_for("repair_new"))

        suri_dt = (request.form.get("suri_dt") or "").strip()[:10]
        bunji1 = _pad_bunji(request.form.get("bunji1"))
        bunji2 = _pad_bunji(request.form.get("bunji2"))
        hosu = (request.form.get("hosu") or "").strip().upper()
        ipju_seq = (request.form.get("ipju_seq") or "").strip()
        if ipju_seq.isdigit():
            ipju_seq = ipju_seq.zfill(2)
        desc = (request.form.get("suri_desc") or "").strip()
        won = _parse_money(request.form.get("suri_won_amt")) or 0
        owner = _parse_money(request.form.get("owner_budam"))
        if owner is None:
            owner = won
        ipjuja = _parse_money(request.form.get("ipjuja_budam")) or 0
        manage = _parse_money(request.form.get("manage_budam")) or 0
        su_churi = (request.form.get("ipju_su_churi") or "N").strip().upper()
        if su_churi not in ("N", "Y"):
            su_churi = "N"
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

        # 주소·호수 등록 여부 (없는 호수 01 등 저장 차단)
        bld = db.query_one(
            "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
            (bunji1, bunji2),
        )
        if not bld:
            flash("등록되지 않은 주소입니다. 주소를 확인하세요.", "err")
            return _back()
        room = db.query_one(
            """
            SELECT hosu FROM bd03_m
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
            """,
            (bunji1, bunji2, hosu),
        )
        if not room:
            flash("등록되지 않은 호수입니다. 호수를 확인하세요.", "err")
            return _back()

        # 입주 순번 자동
        if not ipju_seq:
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
                      ipju_su_churi=%s, uid=%s, sys_dt=NOW()
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
                seq = _repair_next_seq(suri_dt, bunji1, bunji2, hosu)
                db.execute(
                    """
                    INSERT INTO bd05_suri (
                      suri_dt, suri_seq, bunji1, bunji2, hosu, ipju_seq,
                      suri_desc, suri_won_amt, owner_budam, ipjuja_budam, manage_budam,
                      biyong_gb, ipju_su_churi, uid, sys_dt
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,
                      'A',%s,%s,NOW()
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
    form["hosu"] = (request.args.get("hosu") or "").strip().upper()
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
                "suri_desc": row.get("suri_desc") or "",
                "suri_won_amt": money(row.get("suri_won_amt")) or "0",
                "owner_budam": money(row.get("owner_budam")) or "0",
                "ipjuja_budam": money(row.get("ipjuja_budam")) or "0",
                "manage_budam": money(row.get("manage_budam")) or "0",
                "ipju_su_churi": (row.get("ipju_su_churi") or "N").strip().upper() or "N",
                "orig_dt": fmt_date(row.get("suri_dt")),
                "orig_seq": str(row.get("suri_seq") or "").zfill(2),
                "orig_b1": row.get("bunji1") or "",
                "orig_b2": row.get("bunji2") or "",
                "orig_hosu": (row.get("hosu") or "").strip(),
            }

    if form["bunji1"] and form["bunji2"]:
        building_label = _building_label(form["bunji1"], form["bunji2"])
    if form["bunji1"] and form["bunji2"] and form["hosu"]:
        t = _lookup_tenant_for_repair(
            form["bunji1"], form["bunji2"], form["hosu"], form.get("ipju_seq")
        )
        if t:
            form["ipju_seq"] = str(t.get("ipju_seq") or "").zfill(2)
            form["ipju_nm"] = (t.get("ipju_nm") or "").strip()
            form["ipju_tel"] = (
                t.get("ipju_tel1") or t.get("ipju_tel3") or t.get("ipju_tel2") or ""
            ).strip()
            tenant_hint = form["ipju_nm"]

    return render_template(
        "repair_form.html",
        form=form,
        building_label=building_label,
        tenant_hint=tenant_hint,
    )
