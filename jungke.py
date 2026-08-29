"""중개보수 등록 화면.

중개보수 등록/수정/삭제와 기간별 목록 조회 라우트를 모아둔
모듈입니다. (수금관리 메뉴)
"""
import re
from datetime import date, timedelta

from flask import flash, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    building_label as _building_label,
    fmt_bunji,
    fmt_date,
    login_required,
    money,
    pad_bunji as _pad_bunji,
    make_pager as _make_pager,
    parse_money as _parse_money,
    require_write_access,
    resolve_hosu as _resolve_hosu,
)

JUNGKE_DESC_FIXED = "중개보수"
_HOSU_IN_DESC = re.compile(
    r"^\s*([Bb]?\d+)\s*(?:호)?\s*(?:깔)?\s*(?:(?:중개|중계)?\s*수수료|복비).*$",
    re.I,
)


def _hosu_from_desc(desc):
    s = (desc or "").strip()
    if not s or "," in s:
        return ""
    m = _HOSU_IN_DESC.match(s)
    if m:
        return m.group(1).upper()
    if re.fullmatch(r"[Bb]?\d+", s):
        return s.upper()
    return ""


def _ensure_jungke_hosu_col():
    try:
        db.execute(
            "ALTER TABLE sjungke01 ADD COLUMN hosu char(3) NOT NULL DEFAULT ''"
        )
    except Exception:
        pass
    try:
        db.execute(
            """
            UPDATE sjungke01
            SET jungke_desc=%s
            WHERE jungke_desc=%s
            """,
            (JUNGKE_DESC_FIXED, "중개수수료"),
        )
    except Exception:
        pass
    try:
        db.execute(
            """
            UPDATE gicho_code
            SET g_cd_nm=%s
            WHERE g_cd='01' AND g_sub_cd='05' AND g_cd_nm=%s
            """,
            ("중개보수", "중개수수료"),
        )
    except Exception:
        pass
    rows = db.query(
        """
        SELECT jungke_dt, jungke_seq, bunji1, bunji2, jungke_desc
        FROM sjungke01
        WHERE hosu IS NULL OR TRIM(hosu)=''
        """
    )
    for r in rows or []:
        h = _hosu_from_desc(r.get("jungke_desc"))
        if not h:
            continue
        db.execute(
            """
            UPDATE sjungke01
            SET hosu=%s, jungke_desc=%s
            WHERE jungke_dt=%s AND jungke_seq=%s AND bunji1=%s AND bunji2=%s
            """,
            (
                h[:3],
                JUNGKE_DESC_FIXED,
                r.get("jungke_dt"),
                r.get("jungke_seq"),
                r.get("bunji1"),
                r.get("bunji2"),
            ),
        )


def _empty_jungke_form():
    return {
        "mode": "new",
        "jungke_dt": date.today().isoformat(),
        "jungke_seq": "",
        "bunji1": "",
        "bunji2": "",
        "hosu": "",
        "jungke_desc": JUNGKE_DESC_FIXED,
        "jungke_amt": "0",
        "orig_dt": "",
        "orig_seq": "",
        "orig_bunji1": "",
        "orig_bunji2": "",
    }


def _jungke_next_seq(jungke_dt):
    """같은 중개일자 내 다음 순번 (2자리)."""
    row = db.query_one(
        """
        SELECT MAX(CAST(jungke_seq AS UNSIGNED)) AS mx
        FROM sjungke01
        WHERE jungke_dt = %s
        """,
        (jungke_dt,),
    )
    mx = 0
    if row and row.get("mx") is not None:
        try:
            mx = int(row["mx"])
        except (TypeError, ValueError):
            mx = 0
    return str(mx + 1).zfill(2)


@app.route("/jungke", methods=["GET", "POST"])
@login_required
@require_write_access
def jungke():
    """중개보수 등록 · 기간 목록 (XP「중개수수료 등록」 → sjungke01)."""
    today = date.today()
    # 목록 기본 시작일: 전월 1일
    _first_this = today.replace(day=1)
    _prev_month_last = _first_this - timedelta(days=1)
    default_from = _prev_month_last.replace(day=1).isoformat()

    def _list_filters_from_req(src):
        df = (src.get("date_from") or src.get("keep_from") or "").strip() or default_from
        dt = (src.get("date_to") or src.get("keep_to") or "").strip() or today.isoformat()
        return {
            "date_from": df,
            "date_to": dt,
            # 목록 필터만 (입력 폼 주소와 분리)
            "bunji1": _pad_bunji(src.get("q_bunji1") or src.get("keep_q_b1")),
            "bunji2": _pad_bunji(src.get("q_bunji2") or src.get("keep_q_b2")),
        }

    _ensure_jungke_hosu_col()

    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        filters = _list_filters_from_req(request.form)
        redirect_kw = {
            "date_from": filters["date_from"],
            "date_to": filters["date_to"],
        }
        if filters["bunji1"]:
            redirect_kw["q_bunji1"] = fmt_bunji(filters["bunji1"])
        if filters["bunji2"]:
            redirect_kw["q_bunji2"] = fmt_bunji(filters["bunji2"])

        if action == "new":
            return redirect(url_for("jungke", **redirect_kw))

        jungke_dt = (request.form.get("jungke_dt") or "").strip()
        bunji1 = _pad_bunji(request.form.get("bunji1"))
        bunji2 = _pad_bunji(request.form.get("bunji2"))
        hosu = _resolve_hosu(bunji1, bunji2, request.form.get("hosu"))
        desc = JUNGKE_DESC_FIXED
        amt = _parse_money(request.form.get("jungke_amt"))
        mode = (request.form.get("mode") or "new").strip()
        orig_dt = (request.form.get("orig_dt") or "").strip()
        orig_seq = (request.form.get("orig_seq") or "").strip()
        orig_b1 = _pad_bunji(request.form.get("orig_bunji1"))
        orig_b2 = _pad_bunji(request.form.get("orig_bunji2"))
        uid = session.get("sabun") or ""

        if action == "delete":
            if not (orig_dt and orig_seq and orig_b1 and orig_b2):
                flash("삭제할 항목을 목록에서 선택한 뒤 삭제하세요.", "err")
                return redirect(url_for("jungke", **redirect_kw))
            try:
                n = db.execute(
                    """
                    DELETE FROM sjungke01
                    WHERE jungke_dt=%s AND jungke_seq=%s
                      AND bunji1=%s AND bunji2=%s
                    """,
                    (orig_dt, orig_seq.zfill(2), orig_b1, orig_b2),
                )
                if n:
                    flash("삭제했습니다.", "ok")
                else:
                    flash("삭제할 자료를 찾지 못했습니다.", "err")
            except Exception as e:
                flash(f"삭제 실패: {e}", "err")
            return redirect(url_for("jungke", **redirect_kw))

        # save
        if not jungke_dt or len(jungke_dt) < 10:
            flash("중개일자를 입력하세요.", "err")
            return redirect(url_for("jungke", **redirect_kw))
        if not bunji1 or not bunji2:
            flash("주소를 입력하세요.", "err")
            return redirect(url_for("jungke", **redirect_kw))
        if not hosu:
            flash("호수를 입력하세요.", "err")
            return redirect(url_for("jungke", **redirect_kw))
        if amt is None:
            amt = 0

        try:
            if mode == "edit" and orig_dt and orig_seq and orig_b1 and orig_b2:
                db.execute(
                    """
                    UPDATE sjungke01
                    SET jungke_dt=%s, bunji1=%s, bunji2=%s, hosu=%s,
                        jungke_desc=%s, jungke_amt=%s, uid=%s, sys_dt=NOW()
                    WHERE jungke_dt=%s AND jungke_seq=%s
                      AND bunji1=%s AND bunji2=%s
                    """,
                    (
                        jungke_dt,
                        bunji1,
                        bunji2,
                        hosu[:3],
                        desc[:50],
                        amt,
                        uid,
                        orig_dt,
                        orig_seq.zfill(2),
                        orig_b1,
                        orig_b2,
                    ),
                )
                flash("수정 저장했습니다.", "ok")
            else:
                seq = _jungke_next_seq(jungke_dt)
                db.execute(
                    """
                    INSERT INTO sjungke01
                      (jungke_dt, jungke_seq, bunji1, bunji2, hosu,
                       jungke_desc, jungke_amt, uid, sys_dt)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    """,
                    (jungke_dt, seq, bunji1, bunji2, hosu[:3], desc[:50], amt, uid),
                )
                flash(f"등록했습니다. (순번 {seq})", "ok")
        except Exception as e:
            flash(f"저장 실패: {e}", "err")
        return redirect(url_for("jungke", **redirect_kw))

    # GET
    # reset=1 또는 날짜 파라미터 없음 → 시작일 전월 1일 강제
    force_default_dates = (
        request.args.get("reset") == "1"
        or ("date_from" not in request.args and "date_to" not in request.args)
    )
    if force_default_dates:
        filters = {
            "date_from": default_from,
            "date_to": today.isoformat(),
            "bunji1": _pad_bunji(request.args.get("q_bunji1")),
            "bunji2": _pad_bunji(request.args.get("q_bunji2")),
        }
    else:
        filters = {
            "date_from": (request.args.get("date_from") or "").strip() or default_from,
            "date_to": (request.args.get("date_to") or "").strip() or today.isoformat(),
            "bunji1": _pad_bunji(request.args.get("q_bunji1")),
            "bunji2": _pad_bunji(request.args.get("q_bunji2")),
        }
    where = [
        "jungke_dt >= %s",
        "jungke_dt < DATE_ADD(%s, INTERVAL 1 DAY)",
    ]
    args = [filters["date_from"], filters["date_to"]]
    if filters["bunji1"]:
        where.append("bunji1=%s")
        args.append(filters["bunji1"])
    if filters["bunji2"]:
        where.append("bunji2=%s")
        args.append(filters["bunji2"])
    where_sql = " AND ".join(where)
    total = int(
        (db.query_one(f"SELECT COUNT(*) AS c FROM sjungke01 WHERE {where_sql}", args) or {}).get("c")
        or 0
    )
    pager = _make_pager(total)
    results = []
    if total:
        results = db.query(
            f"""
            SELECT jungke_dt, jungke_seq, bunji1, bunji2, hosu, jungke_desc, jungke_amt
            FROM sjungke01
            WHERE {where_sql}
            ORDER BY jungke_dt DESC, bunji1, bunji2, jungke_seq
            LIMIT %s OFFSET %s
            """,
            args + [pager["per_page"], pager["offset"]],
        )

    form = _empty_jungke_form()
    building_label = ""
    edit_dt = (request.args.get("edit_dt") or "").strip()
    edit_seq = (request.args.get("edit_seq") or "").strip()
    edit_b1 = _pad_bunji(request.args.get("edit_b1"))
    edit_b2 = _pad_bunji(request.args.get("edit_b2"))
    if edit_dt and edit_seq and edit_b1 and edit_b2:
        row = db.query_one(
            """
            SELECT jungke_dt, jungke_seq, bunji1, bunji2, hosu, jungke_desc, jungke_amt
            FROM sjungke01
            WHERE jungke_dt=%s AND jungke_seq=%s AND bunji1=%s AND bunji2=%s
            """,
            (edit_dt, edit_seq.zfill(2), edit_b1, edit_b2),
        )
        if row:
            form = {
                "mode": "edit",
                "jungke_dt": fmt_date(row.get("jungke_dt")),
                "jungke_seq": str(row.get("jungke_seq") or "").zfill(2),
                "bunji1": row.get("bunji1") or "",
                "bunji2": row.get("bunji2") or "",
                "hosu": (row.get("hosu") or "").strip()
                or _hosu_from_desc(row.get("jungke_desc")),
                "jungke_desc": JUNGKE_DESC_FIXED,
                "jungke_amt": money(row.get("jungke_amt")) or "0",
                "orig_dt": fmt_date(row.get("jungke_dt")),
                "orig_seq": str(row.get("jungke_seq") or "").zfill(2),
                "orig_bunji1": row.get("bunji1") or "",
                "orig_bunji2": row.get("bunji2") or "",
            }
            building_label = _building_label(form["bunji1"], form["bunji2"])

    return render_template(
        "jungke.html",
        form=form,
        filters=filters,
        results=results,
        building_label=building_label,
        pager=pager,
    )
