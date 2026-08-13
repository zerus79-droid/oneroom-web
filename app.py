from calendar import monthrange
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
import re
from flask import (
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import db
from app_instance import app
from nav import nav_context
from utils import (
    CURRENT_TENANT_SQL as _CURRENT_TENANT_SQL,
    building_label as _building_label,
    clamp_date_str,
    fmt_bunji,
    fmt_bunji_pair,
    fmt_date,
    login_required,
    mask_jumin,
    mask_phone,
    money,
    pad_bunji as _pad_bunji,
    parse_bunji_input as _parse_bunji_input,
    parse_money as _parse_money,
)

# building.py, repair.py: 각 화면(@app.route)들을 등록하기 위한 import.
# 실제로 이 모듈 안에서 building_routes.xxx 형태로 쓰이진 않지만, import 하는 순간
# 해당 파일 안의 @app.route(...) 들이 실행되며 같은 app에 화면이 등록됨.
import building as building_routes  # noqa: F401
import repair as repair_routes  # noqa: F401

app.jinja_env.filters["money"] = money
app.jinja_env.filters["fmt_date"] = fmt_date
app.jinja_env.filters["bunji"] = fmt_bunji
app.jinja_env.filters["bunji_pair"] = fmt_bunji_pair
app.jinja_env.filters["mask_phone"] = mask_phone
app.jinja_env.filters["mask_jumin"] = mask_jumin


@app.context_processor
def inject_user():
    ctx = {
        "current_user": {
            "sabun": session.get("sabun"),
            "name": session.get("s_name"),
            "grade": session.get("grade"),
        }
        if session.get("sabun")
        else None
    }
    try:
        ctx.update(nav_context())
    except Exception:
        ctx.update(
            {
                "nav_section": None,
                "nav_section_label": None,
                "nav_page_label": None,
                "nav_endpoint": None,
            }
        )
    return ctx


@app.route("/")
def index():
    if session.get("sabun"):
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@app.route("/home")
@login_required
def home():
    """홈: 메뉴 + 집사 PC 메인 스타일 요약 KPI (공실·입주 등)."""
    stats = {
        "building_total": 0,
        "room_total": 0,
        "occupied_total": 0,
        "vacant_total": 0,
        "occ_pct": 0,
        "vac_pct": 0,
    }
    try:
        totals = db.query_one(
            f"""
            SELECT
              (SELECT COUNT(*) FROM bd03_m) AS room_total,
              (SELECT COUNT(*) FROM bd03_det d WHERE {_CURRENT_TENANT_SQL}) AS occupied_total,
              (SELECT COUNT(*) FROM bd01) AS building_total
            """
        )
        room_total = int((totals or {}).get("room_total") or 0)
        occupied_total = int((totals or {}).get("occupied_total") or 0)
        vacant_total = max(0, room_total - occupied_total)
        stats["building_total"] = int((totals or {}).get("building_total") or 0)
        stats["room_total"] = room_total
        stats["occupied_total"] = occupied_total
        stats["vacant_total"] = vacant_total
        if room_total > 0:
            stats["occ_pct"] = round(occupied_total * 100 / room_total)
            stats["vac_pct"] = max(0, 100 - stats["occ_pct"])
    except Exception:
        # DB 일시 오류 시에도 메뉴는 표시
        pass
    return render_template("home.html", stats=stats)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        sabun = (request.form.get("sabun") or "").strip()
        password = (request.form.get("password") or "").strip()
        row = db.query_one(
            "SELECT sabun, s_name, grade, pass_wd FROM sawon_m WHERE sabun=%s",
            (sabun,),
        )
        if row and (row.get("pass_wd") or "") == password:
            session["sabun"] = row["sabun"]
            session["s_name"] = row["s_name"]
            session["grade"] = row["grade"]
            return redirect(url_for("home"))
        flash("사번 또는 비밀번호가 올바르지 않습니다.", "err")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("로그아웃했습니다.", "ok")
    return redirect(url_for("login"))


# 사용자 등급 (XP 사용자관리: A / B / C / 무제한)
GRADE_OPTIONS = [
    ("A", "A"),
    ("B", "B"),
    ("C", "C"),
    ("U", "무제한"),
]


def _grade_label(g):
    g = (g or "").strip().upper()
    for code, name in GRADE_OPTIONS:
        if code == g:
            return name
    return g or "—"


def _empty_user_form():
    return {
        "mode": "new",
        "sabun": "",
        "s_name": "",
        "grade": "B",
        "password": "",
        "orig_sabun": "",
    }


@app.route("/users", methods=["GET", "POST"])
@login_required
def users():
    """기초 내역 · 사용자관리 (sawon_m). XP「사용자」화면."""
    uid = session.get("sabun") or ""

    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        if action == "new":
            return redirect(url_for("users"))

        sabun = (request.form.get("sabun") or "").strip()
        s_name = (request.form.get("s_name") or "").strip()
        grade = (request.form.get("grade") or "B").strip().upper()
        password = (request.form.get("password") or "").strip()
        mode = (request.form.get("mode") or "new").strip()
        orig_sabun = (request.form.get("orig_sabun") or "").strip()

        if grade not in ("A", "B", "C", "U"):
            grade = "B"

        if action == "delete":
            target = orig_sabun or sabun
            if not target:
                flash("삭제할 사용자를 목록에서 선택하세요.", "err")
                return redirect(url_for("users"))
            if target == uid:
                flash("현재 로그인 중인 계정은 삭제할 수 없습니다.", "err")
                return redirect(url_for("users"))
            try:
                n = db.execute("DELETE FROM sawon_m WHERE sabun=%s", (target,))
                if n:
                    flash(f"삭제했습니다. ({target})", "ok")
                else:
                    flash("삭제할 사용자를 찾지 못했습니다.", "err")
            except Exception as e:
                flash(f"삭제 실패: {e}", "err")
            return redirect(url_for("users"))

        # save
        if not sabun:
            flash("사용자 ID(사번)를 입력하세요.", "err")
            return redirect(url_for("users"))
        if len(sabun) > 5:
            flash("사용자 ID는 5자 이내입니다.", "err")
            return redirect(url_for("users"))
        if not s_name:
            flash("사용자명을 입력하세요.", "err")
            return redirect(url_for("users"))
        if password and len(password) > 10:
            flash("비밀번호는 10자 이내입니다.", "err")
            return redirect(url_for("users"))

        try:
            if mode == "edit" and orig_sabun:
                if password:
                    db.execute(
                        """
                        UPDATE sawon_m
                        SET sabun=%s, s_name=%s, grade=%s, pass_wd=%s, sys_dt=NOW()
                        WHERE sabun=%s
                        """,
                        (sabun, s_name[:50], grade, password[:10], orig_sabun),
                    )
                else:
                    db.execute(
                        """
                        UPDATE sawon_m
                        SET sabun=%s, s_name=%s, grade=%s, sys_dt=NOW()
                        WHERE sabun=%s
                        """,
                        (sabun, s_name[:50], grade, orig_sabun),
                    )
                # 본인 정보 수정 시 세션 갱신
                if orig_sabun == uid or sabun == uid:
                    session["sabun"] = sabun
                    session["s_name"] = s_name[:50]
                    session["grade"] = grade
                flash("수정 저장했습니다.", "ok")
            else:
                exists = db.query_one(
                    "SELECT 1 AS x FROM sawon_m WHERE sabun=%s", (sabun,)
                )
                if exists:
                    flash("이미 등록된 사용자 ID입니다.", "err")
                    return redirect(url_for("users"))
                if not password:
                    flash("신규 사용자는 비밀번호를 입력하세요.", "err")
                    return redirect(url_for("users"))
                db.execute(
                    """
                    INSERT INTO sawon_m (sabun, s_name, grade, pass_wd, sys_dt)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (sabun, s_name[:50], grade, password[:10]),
                )
                flash(f"등록했습니다. ({sabun})", "ok")
        except Exception as e:
            flash(f"저장 실패: {e}", "err")
        return redirect(url_for("users"))

    # GET
    rows = db.query(
        """
        SELECT sabun, s_name, grade, sys_dt
        FROM sawon_m
        ORDER BY sabun
        """
    )
    for r in rows:
        r["grade_label"] = _grade_label(r.get("grade"))

    form = _empty_user_form()
    edit_sabun = (request.args.get("sabun") or "").strip()
    if edit_sabun:
        row = db.query_one(
            "SELECT sabun, s_name, grade FROM sawon_m WHERE sabun=%s",
            (edit_sabun,),
        )
        if row:
            g = (row.get("grade") or "B").strip().upper()
            if g not in ("A", "B", "C", "U"):
                g = "B"
            form = {
                "mode": "edit",
                "sabun": row.get("sabun") or "",
                "s_name": row.get("s_name") or "",
                "grade": g,
                "password": "",
                "orig_sabun": row.get("sabun") or "",
            }

    return render_template(
        "users.html",
        form=form,
        users=rows,
        grade_options=GRADE_OPTIONS,
    )


@app.route("/password", methods=["GET", "POST"])
@login_required
def password_change():
    """비밀번호변경 (XP「비밀번호」— 본인 계정만)."""
    sabun = session.get("sabun") or ""
    s_name = session.get("s_name") or ""
    row = db.query_one(
        "SELECT sabun, s_name, pass_wd FROM sawon_m WHERE sabun=%s",
        (sabun,),
    )
    if not row:
        flash("사용자 정보를 찾을 수 없습니다. 다시 로그인하세요.", "err")
        return redirect(url_for("logout"))

    if request.method == "POST":
        old_pw = (request.form.get("old_password") or "").strip()
        new_pw = (request.form.get("new_password") or "").strip()
        new_pw2 = (request.form.get("new_password2") or "").strip()
        cur_pw = (row.get("pass_wd") or "").strip()

        if not old_pw:
            flash("기존 비밀번호를 입력하세요.", "err")
            return redirect(url_for("password_change"))
        if old_pw != cur_pw:
            flash("기존 비밀번호가 올바르지 않습니다.", "err")
            return redirect(url_for("password_change"))
        if not new_pw:
            flash("새 비밀번호를 입력하세요.", "err")
            return redirect(url_for("password_change"))
        if len(new_pw) > 10:
            flash("비밀번호는 10자 이내입니다.", "err")
            return redirect(url_for("password_change"))
        if new_pw != new_pw2:
            flash("새 비밀번호와 확인이 일치하지 않습니다.", "err")
            return redirect(url_for("password_change"))
        if new_pw == old_pw:
            flash("기존 비밀번호와 다른 비밀번호를 입력하세요.", "err")
            return redirect(url_for("password_change"))
        try:
            db.execute(
                "UPDATE sawon_m SET pass_wd=%s, sys_dt=NOW() WHERE sabun=%s",
                (new_pw[:10], sabun),
            )
            flash("비밀번호를 변경했습니다. 다음 로그인부터 새 비밀번호를 사용하세요.", "ok")
        except Exception as e:
            flash(f"변경 실패: {e}", "err")
        return redirect(url_for("password_change"))

    return render_template(
        "password.html",
        sabun=row.get("sabun") or sabun,
        s_name=row.get("s_name") or s_name,
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

    results = []
    if q or bunji1 or bunji2 or hosu or ipju_seq or tenant_status != "all":
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
        sql = """
            SELECT bunji1, bunji2, hosu, ipju_seq, ipju_nm, ipju_tel1, ipju_tel2,
                   ipju_dt, out_dt, rent_amt, manage_amt, bojung_amt
            FROM bd03_det
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY (out_dt IS NULL) DESC, ipju_dt DESC LIMIT 100"
        results = db.query(sql, args)

    return render_template(
        "search.html",
        q=q,
        bunji1=bunji1,
        bunji2=bunji2,
        hosu=hosu,
        ipju_seq=ipju_seq,
        tenant_status=tenant_status,
        results=results,
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
    )


def _month_bounds(as_of):
    """기준일이 속한 달의 시작·끝 (date)."""
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    start = as_of.replace(day=1)
    end = date(as_of.year, as_of.month, monthrange(as_of.year, as_of.month)[1])
    return start, end


def _fmt_ipju_short(v):
    """입주일 인쇄: 15-12-27"""
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%y-%m-%d")
    s = str(v)[:10]
    if len(s) >= 10 and s[4] == "-":
        return s[2:4] + "-" + s[5:7] + "-" + s[8:10]
    return s


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



def _empty_jungke_form():
    return {
        "mode": "new",
        "jungke_dt": date.today().isoformat(),
        "jungke_seq": "",
        "bunji1": "",
        "bunji2": "",
        "jungke_desc": "",
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
def jungke():
    """중개수수료 등록 · 기간 목록 (XP「중개수수료 등록」 → sjungke01)."""
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
        desc = (request.form.get("jungke_desc") or "").strip()
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
        if not desc:
            flash("내역을 입력하세요.", "err")
            return redirect(url_for("jungke", **redirect_kw))
        if amt is None:
            amt = 0

        try:
            if mode == "edit" and orig_dt and orig_seq and orig_b1 and orig_b2:
                db.execute(
                    """
                    UPDATE sjungke01
                    SET jungke_dt=%s, bunji1=%s, bunji2=%s,
                        jungke_desc=%s, jungke_amt=%s, uid=%s, sys_dt=NOW()
                    WHERE jungke_dt=%s AND jungke_seq=%s
                      AND bunji1=%s AND bunji2=%s
                    """,
                    (
                        jungke_dt,
                        bunji1,
                        bunji2,
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
                      (jungke_dt, jungke_seq, bunji1, bunji2,
                       jungke_desc, jungke_amt, uid, sys_dt)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                    """,
                    (jungke_dt, seq, bunji1, bunji2, desc[:50], amt, uid),
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
    results = db.query(
        f"""
        SELECT jungke_dt, jungke_seq, bunji1, bunji2, jungke_desc, jungke_amt
        FROM sjungke01
        WHERE {" AND ".join(where)}
        ORDER BY jungke_dt DESC, bunji1, bunji2, jungke_seq
        LIMIT 500
        """,
        args,
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
            SELECT jungke_dt, jungke_seq, bunji1, bunji2, jungke_desc, jungke_amt
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
                "jungke_desc": row.get("jungke_desc") or "",
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
    )


def _empty_tenant_form():
    today = date.today().isoformat()
    return {
        "bunji1": "",
        "bunji2": "",
        "hosu": "",
        "ipju_seq": "",
        "ipju_nm": "",
        "ipju_tel3": "",  # 연락처 일반
        "ipju_tel1": "",  # 휴대1
        "ipju_tel2": "",  # 휴대2
        "ipju_dt": today,
        "jumin1": "",
        "jumin2": "",
        "bojung_amt": "0",
        "rent_amt": "0",
        "manage_amt": "0",
        "yechi_amt": "0",
        "napbu_gb": "B",  # B=후납 (레거시 기본)
        "ipju_gb": "A",
        "mode": "new",  # new | edit
        "out_dt": "",
        "tenant_status": "new",  # current | past | new
    }


def _coerce_amount_to_str(v, default="0"):
    if v is None:
        return default
    if isinstance(v, (int, float, Decimal)):
        try:
            return str(int(v))
        except (ValueError, TypeError, InvalidOperation):
            return default
    s = str(v).strip()
    if not s:
        return default
    try:
        return str(int(Decimal(s.replace(",", "").replace(" ", ""))))
    except (ValueError, TypeError, InvalidOperation):
        return default


def _tenant_search_query(form):
    for key in ("ipju_nm", "ipju_tel1", "ipju_tel2", "ipju_tel3"):
        value = (form.get(key) or "").strip()
        if value:
            return value
    bunji1 = (form.get("bunji1") or "").strip()
    bunji2 = (form.get("bunji2") or "").strip()
    hosu = (form.get("hosu") or "").strip()
    if bunji1 and bunji2:
        return f"{bunji1}-{bunji2}"
    if hosu:
        return hosu
    return ""


def _tenant_is_past_out(out_dt) -> bool:
    """퇴실일 있으면 True (현세입자 아님)."""
    if out_dt is None:
        return False
    if isinstance(out_dt, datetime):
        return out_dt.year >= 1000
    if isinstance(out_dt, date):
        return out_dt.year >= 1000
    s = str(out_dt)[:10]
    if len(s) >= 4 and s[:4].isdigit():
        return int(s[:4]) >= 1000
    return False


def _tenant_status_from_row(row) -> str:
    """current=현세입자, past=퇴실자, new=신규."""
    if not row:
        return "new"
    if _tenant_is_past_out(row.get("out_dt")):
        return "past"
    return "current"


def _tenant_form_from_row(row):
    form = _empty_tenant_form()
    if not row:
        return form
    jumin = (row.get("ipju_jumin_no") or "").strip()
    jumin = re.sub(r"\D", "", jumin)
    out_s = fmt_date(row.get("out_dt")) or ""
    if out_s and out_s.startswith("0"):
        # 0000-00-00 류는 공백
        try:
            if int(out_s[:4]) < 1000:
                out_s = ""
        except ValueError:
            pass
    form.update(
        {
            "bunji1": row.get("bunji1") or "",
            "bunji2": row.get("bunji2") or "",
            "hosu": (row.get("hosu") or "").strip(),
            "ipju_seq": str(row.get("ipju_seq") or "").strip().zfill(2)
            if str(row.get("ipju_seq") or "").strip()
            else "",
            "ipju_nm": (row.get("ipju_nm") or "").strip(),
            "ipju_tel3": (row.get("ipju_tel3") or "").strip(),
            "ipju_tel1": (row.get("ipju_tel1") or "").strip(),
            "ipju_tel2": (row.get("ipju_tel2") or "").strip(),
            "ipju_dt": fmt_date(row.get("ipju_dt")) or date.today().isoformat(),
            "out_dt": out_s,
            "jumin1": jumin[:6] if jumin else "",
            "jumin2": jumin[6:13] if len(jumin) > 6 else (jumin[6:] if jumin else ""),
            "bojung_amt": _coerce_amount_to_str(row.get("bojung_amt")),
            "rent_amt": _coerce_amount_to_str(row.get("rent_amt")),
            "manage_amt": _coerce_amount_to_str(row.get("manage_amt")),
            "yechi_amt": _coerce_amount_to_str(row.get("yechi_amt")),
            "napbu_gb": (row.get("napbu_gb") or "B").strip() or "B",
            "ipju_gb": (row.get("ipju_gb") or "A").strip() or "A",
            "mode": "edit",
            "tenant_status": _tenant_status_from_row(row),
        }
    )
    return form


def _next_ipju_seq(bunji1, bunji2, hosu):
    """해당 호실의 다음 입주 순번 (max+1, 없으면 01)."""
    hosu = (hosu or "").strip().upper()
    row = db.query_one(
        """
        SELECT MAX(CAST(ipju_seq AS UNSIGNED)) AS mx
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
        """,
        (bunji1, bunji2, hosu),
    )
    mx = int((row or {}).get("mx") or 0)
    return str(mx + 1).zfill(2)


def _current_ipju_seq(bunji1, bunji2, hosu):
    """현세입자 순번. 없으면 빈 문자열."""
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    h = (hosu or "").strip().upper()
    if not (b1 and b2 and h):
        return ""
    row = db.query_one(
        f"""
        SELECT ipju_seq FROM bd03_det d
        WHERE d.bunji1=%s AND d.bunji2=%s AND UPPER(TRIM(d.hosu))=%s
          AND {_CURRENT_TENANT_SQL}
        ORDER BY CAST(d.ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (b1, b2, h),
    )
    if not row:
        return ""
    s = str(row.get("ipju_seq") or "").strip()
    return s.zfill(2) if s.isdigit() else s


def _clamp_new_ipju_seq(bunji1, bunji2, hosu, seq):
    """신규 입력용 순번 보정.
    요청 순번이 (현세입자 다음 = max+1) 보다 크면 다음 순번으로 맞춤.
    반환: (보정된순번, 보정여부)
    """
    next_s = _next_ipju_seq(bunji1, bunji2, hosu)
    s = str(seq or "").strip()
    if not s:
        return next_s, True
    digits = re.sub(r"\D", "", s)
    if not digits:
        return next_s, True
    sn = int(digits)
    nn = int(next_s)
    if sn > nn:
        return next_s, True
    # 2자리 표기 (99 초과 드묾)
    return str(sn).zfill(2) if sn < 100 else str(sn), False


def _form_from_tenant_request(src):
    """request.form / request.args → form dict"""
    form = _empty_tenant_form()
    bunji_raw = (src.get("bunji") or "").strip()
    if bunji_raw:
        b1, b2 = _parse_bunji_input(bunji_raw)
    else:
        b1 = _pad_bunji((src.get("bunji1") or "").strip())
        b2 = _pad_bunji((src.get("bunji2") or "").strip())
    form["bunji1"] = b1
    form["bunji2"] = b2
    form["hosu"] = (src.get("hosu") or "").strip().upper()
    seq = (src.get("ipju_seq") or "").strip()
    form["ipju_seq"] = seq.zfill(2) if seq.isdigit() else seq
    form["ipju_nm"] = (src.get("ipju_nm") or "").strip()
    form["ipju_tel3"] = (src.get("ipju_tel3") or "").strip()
    form["ipju_tel1"] = (src.get("ipju_tel1") or "").strip()
    form["ipju_tel2"] = (src.get("ipju_tel2") or "").strip()
    form["ipju_dt"] = clamp_date_str((src.get("ipju_dt") or "").strip()) or date.today().isoformat()
    form["jumin1"] = re.sub(r"\D", "", (src.get("jumin1") or ""))[:6]
    form["jumin2"] = re.sub(r"\D", "", (src.get("jumin2") or ""))[:7]
    form["bojung_amt"] = (src.get("bojung_amt") or "0").replace(",", "").strip() or "0"
    form["rent_amt"] = (src.get("rent_amt") or "0").replace(",", "").strip() or "0"
    form["manage_amt"] = (src.get("manage_amt") or "0").replace(",", "").strip() or "0"
    form["yechi_amt"] = (src.get("yechi_amt") or "0").replace(",", "").strip() or "0"
    nap = (src.get("napbu_gb") or "B").strip().upper()
    form["napbu_gb"] = nap if nap in ("A", "B") else "B"
    form["mode"] = (src.get("mode") or "new").strip() or "new"
    return form


@app.route("/tenants/manage", methods=["GET", "POST"])
@login_required
def tenant_manage():
    """입주자관리 · 입주 이력 등록/수정 (레거시 「입주 이력 등록」 창)"""
    buildings, rooms = _buildings_and_rooms()

    if request.method == "GET":
        form = _empty_tenant_form()
        # URL 로 기존 건 로드
        b1 = _pad_bunji((request.args.get("bunji1") or "").strip())
        b2 = _pad_bunji((request.args.get("bunji2") or "").strip())
        if request.args.get("bunji"):
            b1, b2 = _parse_bunji_input(request.args.get("bunji"))
        hosu = (request.args.get("hosu") or "").strip().upper()
        seq = (request.args.get("ipju_seq") or "").strip()
        if seq.isdigit():
            seq = seq.zfill(2)
        if b1 and b2 and hosu and seq:
            row = _lookup_tenant_row(b1, b2, hosu, seq)
            if row:
                form = _tenant_form_from_row(row)
            else:
                form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"] = (
                    b1,
                    b2,
                    hosu,
                    seq,
                )
                form["mode"] = "new"
        elif b1 and b2:
            form["bunji1"], form["bunji2"] = b1, b2
            if hosu:
                form["hosu"] = hosu
                # 호수만 지정: 다음 순번 신규가 아니라 현재(또는 최신) 이력 표시
                row = _lookup_tenant_row(b1, b2, hosu)
                if row:
                    form = _tenant_form_from_row(row)
                else:
                    form["ipju_seq"] = _next_ipju_seq(b1, b2, hosu)
                    form["mode"] = "new"
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=_building_label(form["bunji1"], form["bunji2"]),
        )

    action = (request.form.get("action") or "save").strip()
    form = _form_from_tenant_request(request.form)
    building_label = _building_label(form["bunji1"], form["bunji2"])

    if action == "new":
        # 키(주소·호수)만 남기고 신규
        keep_b1, keep_b2, keep_h = form["bunji1"], form["bunji2"], form["hosu"]
        form = _empty_tenant_form()
        form["bunji1"], form["bunji2"], form["hosu"] = keep_b1, keep_b2, keep_h
        if keep_b1 and keep_b2 and keep_h:
            form["ipju_seq"] = _next_ipju_seq(keep_b1, keep_b2, keep_h)
        flash("신규 입력 모드입니다. 저장하면 새 순번으로 등록됩니다.", "ok")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=_building_label(form["bunji1"], form["bunji2"]),
        )

    if action == "delete":
        if not (
            form["bunji1"]
            and form["bunji2"]
            and form["hosu"]
            and form["ipju_seq"]
        ):
            flash("삭제할 주소·호수·순번을 확인하세요.", "err")
            return render_template(
                "tenant_form.html",
                form=form,
                buildings=buildings,
                rooms=rooms,
                building_label=building_label,
            )
        # 퇴실자 이력 삭제 금지
        del_row = _lookup_tenant_row(
            form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]
        )
        if not del_row:
            flash("삭제할 이력을 찾지 못했습니다.", "err")
            return render_template(
                "tenant_form.html",
                form=form,
                buildings=buildings,
                rooms=rooms,
                building_label=building_label,
            )
        if _tenant_is_past_out(del_row.get("out_dt")):
            flash(
                "퇴실 완료된 이력은 삭제할 수 없습니다. (퇴실자)",
                "err",
            )
            form = _tenant_form_from_row(del_row)
            return render_template(
                "tenant_form.html",
                form=form,
                buildings=buildings,
                rooms=rooms,
                building_label=_building_label(form["bunji1"], form["bunji2"]),
                popup_msg="퇴실 완료된 이력은 삭제할 수 없습니다.",
                popup_type="err",
            )
        n = db.execute(
            """
            DELETE FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
              AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]),
        )
        if n:
            flash(f"입주 이력(순번 {form['ipju_seq']})이 삭제되었습니다.", "ok")
            keep_b1, keep_b2, keep_h = form["bunji1"], form["bunji2"], form["hosu"]
            form = _empty_tenant_form()
            form["bunji1"], form["bunji2"], form["hosu"] = keep_b1, keep_b2, keep_h
            if keep_h:
                form["ipju_seq"] = _next_ipju_seq(keep_b1, keep_b2, keep_h)
            return render_template(
                "tenant_form.html",
                form=form,
                buildings=buildings,
                rooms=rooms,
                building_label=_building_label(form["bunji1"], form["bunji2"]),
                popup_msg="삭제되었습니다.",
                popup_type="ok",
            )
        flash("삭제할 이력을 찾지 못했습니다.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=_building_label(form["bunji1"], form["bunji2"]),
        )

    # ── 저장 ──
    if not (form["bunji1"] and form["bunji2"] and form["hosu"]):
        flash("주소와 호수는 필수입니다.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )
    if not form["ipju_nm"]:
        flash("성명을 입력하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )
    # 건물·호수 존재
    b = db.query_one(
        "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (form["bunji1"], form["bunji2"]),
    )
    if not b:
        flash("등록되지 않은 주소입니다. 건물 내역을 먼저 등록하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )
    room = db.query_one(
        """
        SELECT hosu FROM bd03_m
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
        """,
        (form["bunji1"], form["bunji2"], form["hosu"]),
    )
    if not room:
        flash("등록되지 않은 호수입니다. 호수 내역을 먼저 등록하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )

    if not form["ipju_seq"]:
        form["ipju_seq"] = _next_ipju_seq(
            form["bunji1"], form["bunji2"], form["hosu"]
        )

    # 신규 INSERT 대상인지 미리 확인 후, 순번이 max+1 초과면 보정
    pre_exists = db.query_one(
        """
        SELECT ipju_seq FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s
          AND CAST(ipju_seq AS UNSIGNED)=CAST(%s AS UNSIGNED)
        """,
        (form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]),
    )
    if not pre_exists:
        clamped, did_clamp = _clamp_new_ipju_seq(
            form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]
        )
        if did_clamp:
            form["ipju_seq"] = clamped
            flash(
                f"신규 순번이 너무 커서 「다음 순번 {clamped}」으로 맞췄습니다.",
                "ok",
            )

    try:
        bojung = int(form["bojung_amt"] or 0)
        rent = int(form["rent_amt"] or 0)
        manage = int(form["manage_amt"] or 0)
        yechi = int(form["yechi_amt"] or 0)
    except ValueError:
        flash("금액은 숫자로 입력하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )

    # 보증금 · 예치금 동시 입력 불가
    if bojung > 0 and yechi > 0:
        flash("보증금과 예치금은 함께 입력할 수 없습니다. 하나만 입력하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
            popup_msg="보증금과 예치금은 함께 입력할 수 없습니다.",
            popup_type="err",
        )

    jumin = (form["jumin1"] + form["jumin2"])[:13]
    ipju_dt = form["ipju_dt"] + " 00:00:00"
    uid = (session.get("sabun") or "")[:5]

    exists = db.query_one(
        """
        SELECT ipju_seq, ipju_nm, ipju_jumin_no, ipju_dt, out_dt
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
        """,
        (form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]),
    )
    was_insert = not exists

    def _saved_snapshot():
        """저장 직후 하단 결과 박스용."""
        b1d = form["bunji1"]
        b2d = form["bunji2"]
        try:
            b1s = str(int(re.sub(r"\D", "", str(b1d)) or "0"))
            b2s = str(int(re.sub(r"\D", "", str(b2d)) or "0"))
        except ValueError:
            b1s, b2s = str(b1d), str(b2d)
        return {
            "action": "insert" if was_insert else "update",
            "bunji": f"{b1s}-{b2s}",
            "hosu": form["hosu"],
            "ipju_seq": form["ipju_seq"],
            "ipju_nm": form["ipju_nm"],
            "ipju_dt": form.get("ipju_dt") or "",
            "ipju_tel1": form.get("ipju_tel1") or "",
            "ipju_tel2": form.get("ipju_tel2") or "",
            "ipju_tel3": form.get("ipju_tel3") or "",
            "bojung_amt": bojung,
            "rent_amt": rent,
            "manage_amt": manage,
            "yechi_amt": yechi,
            "napbu_gb": form.get("napbu_gb") or "B",
            "napbu_label": "선납" if (form.get("napbu_gb") or "") == "A" else "후납",
        }

    try:
        if exists:
            # 퇴실자 이력은 조회만 — 수정 저장 불가
            if _tenant_is_past_out(exists.get("out_dt")):
                flash("퇴실 완료된 이력은 수정할 수 없습니다. (조회 전용)", "err")
                full = _lookup_tenant_row(
                    form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]
                )
                if full:
                    form = _tenant_form_from_row(full)
                return render_template(
                    "tenant_form.html",
                    form=form,
                    buildings=buildings,
                    rooms=rooms,
                    building_label=building_label,
                    popup_msg="퇴실 완료된 이력은 수정할 수 없습니다.",
                    popup_type="err",
                )
            db.execute(
                """
                UPDATE bd03_det SET
                  ipju_nm=%s, ipju_jumin_no=%s,
                  ipju_tel1=%s, ipju_tel2=%s, ipju_tel3=%s,
                  ipju_dt=%s, bojung_amt=%s, rent_amt=%s,
                  manage_amt=%s, yechi_amt=%s, napbu_gb=%s,
                  sys_dt=NOW(), uid=%s
                WHERE bunji1=%s AND bunji2=%s
                  AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                """,
                (
                    form["ipju_nm"],
                    jumin,
                    form["ipju_tel1"],
                    form["ipju_tel2"],
                    form["ipju_tel3"],
                    ipju_dt,
                    bojung,
                    rent,
                    manage,
                    yechi,
                    form["napbu_gb"],
                    uid,
                    form["bunji1"],
                    form["bunji2"],
                    form["hosu"],
                    form["ipju_seq"],
                ),
            )
            # 성공 안내는 popup_msg 한 번만 (flash 중복 방지)
        else:
            db.execute(
                """
                INSERT INTO bd03_det (
                  bunji1, bunji2, hosu, ipju_seq, ipju_gb, ipju_dt, ipju_nm,
                  ipju_jumin_no, ipju_tel1, ipju_tel2, ipju_tel3,
                  plan_out_dt, out_dt, out_seq,
                  bojung_amt, rent_amt, manage_amt, yechi_amt, napbu_gb,
                  misu_tot, suri_tot, out_jungsan_end, del_yn, sys_dt, uid
                ) VALUES (
                  %s, %s, %s, %s, 'A', %s, %s,
                  %s, %s, %s, %s,
                  NULL, NULL, '',
                  %s, %s, %s, %s, %s,
                  NULL, 0, '', 'N', NOW(), %s
                )
                """,
                (
                    form["bunji1"],
                    form["bunji2"],
                    form["hosu"],
                    form["ipju_seq"],
                    ipju_dt,
                    form["ipju_nm"],
                    jumin,
                    form["ipju_tel1"],
                    form["ipju_tel2"],
                    form["ipju_tel3"],
                    bojung,
                    rent,
                    manage,
                    yechi,
                    form["napbu_gb"],
                    uid,
                ),
            )
    except Exception as e:
        flash(f"저장 실패: {e}", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=_building_label(form["bunji1"], form["bunji2"]),
        )

    # 예전 flash("…수정…") + flash("…조회 화면…") 잔여 메시지 비우기
    try:
        from flask import get_flashed_messages

        get_flashed_messages()
    except Exception:
        pass

    snap = _saved_snapshot()
    nm = (snap.get("ipju_nm") or "").strip()
    seq_s = snap.get("ipju_seq") or ""

    # 신규 저장: 팝업 1회 + 전체 필드 초기화 + 하단 결과 박스
    if was_insert:
        form = _empty_tenant_form()
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label="",
            popup_msg=f"저장되었습니다.\n{nm} · 순번 {seq_s}",
            popup_type="ok",
            last_saved=snap,
        )

    # 수정 저장: 팝업 1회 + 이력 유지 + 하단 결과 박스
    saved = _lookup_tenant_row(
        form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]
    )
    if saved:
        form = _tenant_form_from_row(saved)
    else:
        form["mode"] = "edit"
        form["tenant_status"] = "current"
    return render_template(
        "tenant_form.html",
        form=form,
        buildings=buildings,
        rooms=rooms,
        building_label=_building_label(form["bunji1"], form["bunji2"]),
        popup_msg=f"수정 저장되었습니다.\n{nm} · 순번 {seq_s}",
        popup_type="ok",
        last_saved=snap,
    )


def _lookup_tenant_row(bunji1, bunji2, hosu, ipju_seq=""):
    """입주 이력 1건. 순번 있으면 그 건, 없으면 현재거주 → 없으면 최신 순번."""
    b1 = _pad_bunji(bunji1)
    b2 = _pad_bunji(bunji2)
    h = (hosu or "").strip().upper()
    seq = (ipju_seq or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    if not (b1 and b2 and h):
        return None
    if seq:
        # 패딩 일치 우선 (인덱스 유리), 실패 시 CAST 폴백
        row = db.query_one(
            """
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
              AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            LIMIT 1
            """,
            (b1, b2, h, seq),
        )
        if row:
            return row
        return db.query_one(
            """
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
              AND UPPER(TRIM(hosu))=%s
              AND CAST(ipju_seq AS UNSIGNED)=CAST(%s AS UNSIGNED)
            LIMIT 1
            """,
            (b1, b2, h, seq),
        )
    # 현재 거주 우선
    row = db.query_one(
        f"""
        SELECT * FROM bd03_det d
        WHERE d.bunji1=%s AND d.bunji2=%s
          AND UPPER(TRIM(d.hosu))=%s
          AND {_CURRENT_TENANT_SQL}
        ORDER BY CAST(d.ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (b1, b2, h),
    )
    if row:
        return row
    # 공실이면 마지막 이력
    return db.query_one(
        """
        SELECT * FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
        ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (b1, b2, h),
    )


@app.route("/api/next_ipju_seq")
@login_required
def api_next_ipju_seq():
    bunji1 = _pad_bunji((request.args.get("bunji1") or "").strip())
    bunji2 = _pad_bunji((request.args.get("bunji2") or "").strip())
    if request.args.get("bunji"):
        bunji1, bunji2 = _parse_bunji_input(request.args.get("bunji"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    if not (bunji1 and bunji2 and hosu):
        return jsonify({"ok": False, "ipju_seq": ""})
    next_s = _next_ipju_seq(bunji1, bunji2, hosu)
    cur_s = _current_ipju_seq(bunji1, bunji2, hosu)
    req = (request.args.get("seq") or request.args.get("ipju_seq") or "").strip()
    clamped = next_s
    did_clamp = False
    if req:
        clamped, did_clamp = _clamp_new_ipju_seq(bunji1, bunji2, hosu, req)
    return jsonify(
        {
            "ok": True,
            "ipju_seq": next_s,
            "next_seq": next_s,
            "current_seq": cur_s,
            "clamped_seq": clamped,
            "did_clamp": did_clamp,
            "bunji1": bunji1,
            "bunji2": bunji2,
            "hosu": hosu,
        }
    )


@app.route("/api/tenant_load")
@login_required
def api_tenant_load():
    """주소·호수(·순번)로 입주 이력 1건 로드.
    순번 생략 시: 현재 거주자 → 없으면 최신 순번 이력.
    """
    bunji1 = _pad_bunji((request.args.get("bunji1") or "").strip())
    bunji2 = _pad_bunji((request.args.get("bunji2") or "").strip())
    if request.args.get("bunji"):
        bunji1, bunji2 = _parse_bunji_input(request.args.get("bunji"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    seq = (request.args.get("ipju_seq") or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    if not (bunji1 and bunji2 and hosu):
        return jsonify({"ok": False, "reason": "key"})
    next_s = _next_ipju_seq(bunji1, bunji2, hosu)
    cur_s = _current_ipju_seq(bunji1, bunji2, hosu)
    row = _lookup_tenant_row(bunji1, bunji2, hosu, seq) if seq else _lookup_tenant_row(
        bunji1, bunji2, hosu, ""
    )
    if not row:
        # 이력 없음 → 신규. 요청 순번이 다음(max+1)보다 크면 다음 순번으로 보정
        use_seq, did_clamp = _clamp_new_ipju_seq(bunji1, bunji2, hosu, seq or next_s)
        return jsonify(
            {
                "ok": False,
                "reason": "empty",
                "next_seq": next_s,
                "current_seq": cur_s,
                "ipju_seq": use_seq,
                "did_clamp": did_clamp,
                "bunji1": bunji1,
                "bunji2": bunji2,
                "hosu": hosu,
            }
        )
    form = _tenant_form_from_row(row)
    return jsonify(
        {
            "ok": True,
            "form": form,
            "next_seq": next_s,
            "current_seq": cur_s,
        }
    )


@app.route("/payments")
@login_required
def payments():
    today = date.today()
    month_start = today.replace(day=1)

    # 상단 메뉴 등: fresh=1 또는 쿼리 없음 → 이전 세입자/주소 조건 초기화
    # (수금 등록에서 넘어온 조회값이 폼에 남지 않도록)
    arg_keys = [k for k in request.args.keys() if k not in ("fresh",)]
    is_fresh = request.args.get("fresh") == "1" or len(arg_keys) == 0

    if is_fresh:
        bunji1 = bunji2 = hosu = ""
        ipju_seq_f = ""
        name_raw = name_q = name_display = ""
        name_mode = False
        date_from = month_start.isoformat()
        date_to = today.isoformat()
        ym_year = str(today.year)
        ym_month = f"{today.month:02d}"
        use_ym = False
        all_hist = False
        name_list_mode = False
        rows = []
        payment_groups = []
        buildings, rooms = _buildings_and_rooms()
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
        years = sorted(set(years), reverse=True)
        return render_template(
            "payments.html",
            payments=[],
            payment_groups=[],
            name_list_mode=False,
            is_fresh=True,
            buildings=buildings,
            rooms=rooms,
            years=years,
            filters={
                "bunji1": "",
                "bunji2": "",
                "hosu": "",
                "ipju_seq": "",
                "name": "",
                "name_mode": "",
                "tenant_status": "current",
                "date_from": date_from,
                "date_to": date_to,
                "ym_year": ym_year,
                "ym_month": ym_month,
            },
        )

    # 통합 입력 "508-88" 또는 칸 분리 bunji1 / bunji2
    bunji_raw = (request.args.get("bunji") or "").strip()
    b1_in = (request.args.get("bunji1") or "").strip()
    b2_in = (request.args.get("bunji2") or "").strip()
    if bunji_raw:
        bunji1, bunji2 = _parse_bunji_input(bunji_raw)
    else:
        bunji1 = _pad_bunji(b1_in)
        bunji2 = _pad_bunji(b2_in)
    hosu = (request.args.get("hosu") or "").strip().upper()
    ipju_seq_f = (request.args.get("ipju_seq") or "").strip()
    if ipju_seq_f.isdigit():
        ipju_seq_f = ipju_seq_f.zfill(2)
    name_raw = (request.args.get("name") or request.args.get("q") or "").strip()
    # name_mode=1 일 때만 세입자 이름 검색 (Enter). 조회/월 조회는 이름 무시
    name_mode = request.args.get("name_mode") == "1"
    name_q = name_raw if name_mode else ""
    # 폼 표시용 이름: 이름 검색이거나 상세(주소+호실) 드릴다운 시 유지
    name_display = name_raw
    # 현 거주자(기본) / 과거 입주자 / 전체
    tenant_status = (request.args.get("tenant_status") or "current").strip().lower()
    if tenant_status not in ("current", "past", "all"):
        tenant_status = "current"
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    ym_year = (request.args.get("ym_year") or "").strip()
    ym_month = (request.args.get("ym_month") or "").strip()

    use_ym = request.args.get("use_ym") == "1"

    def _tenant_status_sql(alias="d"):
        """현 거주자 / 과거 입주자 SQL 조건"""
        if tenant_status == "past":
            return (
                f"({alias}.out_dt IS NOT NULL AND {alias}.out_dt >= '1000-01-01')"
            )
        if tenant_status == "all":
            return "1=1"
        # current (기본)
        return (
            f"({alias}.out_dt IS NULL OR {alias}.out_dt < '1000-01-01')"
        )

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

    # 수금등록 등에서 세입자 전체 납부 내역 보기
    all_hist = request.args.get("all_hist") in ("1", "true", "yes")

    # 「월 조회」: 선택 년·월 1일~말일 (이름 검색이어도 월 범위 유지)
    # Enter 이름 검색 + 주소·호실 없음 → 동명이인 목록 모드
    name_list_mode = bool(name_q) and name_mode and not (bunji1 and bunji2 and hosu)

    if use_ym and ym_year.isdigit() and ym_month.isdigit():
        y = int(ym_year)
        m = int(ym_month)
        if 1 <= m <= 12 and 1990 <= y <= 2100:
            last_day = monthrange(y, m)[1]
            date_from = date(y, m, 1).isoformat()
            month_end = date(y, m, last_day)
            date_to = min(month_end, today).isoformat()
            ym_year = str(y)
            ym_month = f"{m:02d}"
    elif all_hist and bunji1 and bunji2 and hosu:
        # 해당 세입자(호실·입주순번) 최초일 ~ 오늘: 납부 전 내역
        first = _first_date_for_tenant(bunji1, bunji2, hosu, ipju_seq_f)
        date_from = first or "2000-01-01"
        date_to = today.isoformat()
        try:
            df = date.fromisoformat(date_from[:10])
            ym_year = str(df.year)
            ym_month = f"{df.month:02d}"
        except ValueError:
            ym_year = str(today.year)
            ym_month = f"{today.month:02d}"
    elif name_list_mode:
        # 이름 검색(엔터): 시작일=그 이름 최초 등장일, 종료=오늘
        first = _first_date_for_name(name_q)
        date_from = first or "2000-01-01"
        date_to = today.isoformat()
        try:
            df = date.fromisoformat(date_from[:10])
            ym_year = str(df.year)
            ym_month = f"{df.month:02d}"
        except ValueError:
            ym_year = str(today.year)
            ym_month = f"{today.month:02d}"
    else:
        # 일반 조회 또는 이름+주소 상세: URL/폼 기간 사용
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
        try:
            df = date.fromisoformat(date_from[:10])
            ym_year = str(df.year)
            ym_month = f"{df.month:02d}"
        except ValueError:
            ym_year = str(today.year)
            ym_month = f"{today.month:02d}"

    rows = []
    payment_groups = []

    if name_list_mode:
        # 1) 이름 매칭: 정확 일치 우선 → 접두 → 포함 (+ 현거주/과거 필터)
        status_sql = _tenant_status_sql("d")
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
        if not tenants:
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
                (f"{name_q}%",),  # 접두 검색(앞 % 제거) — 더 빠름
            )
            # 접두로 없으면 기존처럼 포함 검색
            if not tenants:
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
                    (f"%{name_q}%",),
                )

        # 1명 → 리다이렉트 없이 같은 요청에서 전체 수금 조회 (왕복 1회 절약)
        if len(tenants) == 1:
            t = tenants[0]
            bunji1 = t.get("bunji1") or ""
            bunji2 = t.get("bunji2") or ""
            hosu = (t.get("hosu") or "").strip().upper()
            ipju_seq_f = str(t.get("ipju_seq") or "").strip()
            if ipju_seq_f.isdigit():
                ipju_seq_f = ipju_seq_f.zfill(2)
            name_display = (t.get("ipju_nm") or name_q).strip()
            name_list_mode = False
            first = _first_date_for_tenant(bunji1, bunji2, hosu, ipju_seq_f)
            date_from = first or "2000-01-01"
            date_to = today.isoformat()
            all_hist = True
            try:
                df = date.fromisoformat(date_from[:10])
                ym_year = str(df.year)
                ym_month = f"{df.month:02d}"
            except ValueError:
                ym_year = str(today.year)
                ym_month = f"{today.month:02d}"
        elif len(tenants) >= 2:
            # 동명이인: 수금 전건 로드 금지 + 통계 1회 쿼리로 묶기
            # (예전: 세입자마다 COUNT 2회 → 이름 '김' 검색 시 수백 쿼리)
            key_set = set()
            tenant_meta = []
            for t in tenants:
                t_b1 = t.get("bunji1") or ""
                t_b2 = t.get("bunji2") or ""
                t_hosu = (t.get("hosu") or "").strip().upper()
                t_seq = str(t.get("ipju_seq") or "").strip()
                if t_seq.isdigit():
                    t_seq = t_seq.zfill(2)
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
                # IN 목록으로 한 번에 집계
                placeholders = []
                args_in = []
                for m in tenant_meta:
                    placeholders.append("(%s,%s,%s,%s)")
                    args_in.extend(
                        [
                            m["bunji1"],
                            m["bunji2"],
                            m["hosu"],
                            m["ipju_seq"],
                        ]
                    )
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
                    k = (
                        sr.get("bunji1") or "",
                        sr.get("bunji2") or "",
                        (sr.get("hosu") or "").strip().upper(),
                        str(sr.get("ipju_seq") or "").strip().zfill(2)
                        if str(sr.get("ipju_seq") or "").strip().isdigit()
                        else str(sr.get("ipju_seq") or "").strip(),
                    )
                    stats_map[k] = sr
            for m in tenant_meta:
                k = (m["bunji1"], m["bunji2"], m["hosu"], m["ipju_seq"])
                sr = stats_map.get(k) or {}
                hist_c = int(sr.get("hist_c") or 0)
                pay_c = int(sr.get("pay_c") or 0)
                hist_mn = sr.get("mn")
                hist_mx = sr.get("mx")
                if hist_mn is not None:
                    hist_mn = str(hist_mn)[:10]
                if hist_mx is not None:
                    hist_mx = str(hist_mx)[:10]
                payment_groups.append(
                    {
                        "bunji1": m["bunji1"],
                        "bunji2": m["bunji2"],
                        "hosu": m["hosu"],
                        "ipju_seq": m["ipju_seq"],
                        "ipju_nm": m["ipju_nm"],
                        "juso": m["juso"],
                        "pay_count": pay_c,
                        "hist_count": hist_c,
                        "hist_from": hist_mn,
                        "hist_to": hist_mx,
                    }
                )
        # 0명: payment_groups 빈 목록

    if not name_list_mode:
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
            # 날짜만 있는 컬럼/값도 포함되도록 다음날 미만 비교
            where.append("s.sukum_dt < %s + INTERVAL 1 DAY")
            args.append(date_to)
        # 삭제된 건 제외
        where.append("(s.del_yn IS NULL OR s.del_yn='' OR s.del_yn='N')")
        # 세입자 전체 내역이면 한도를 넉넉히
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
        rows = db.query(sql, tuple(args))

    buildings, rooms = _buildings_and_rooms()
    # 년 선택 목록: 데이터 있는 해 + 전후 1년
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
    years = sorted(set(years), reverse=True)

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


def _iso_min_date(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)[:10]
    return s if len(s) >= 10 else None


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
    # 클릭 링크용: 세입자별 전체 기간
    cache = {}
    for r in rows or []:
        key = (
            r.get("bunji1") or "",
            r.get("bunji2") or "",
            (r.get("hosu") or "").strip().upper(),
            str(r.get("ipju_seq") or "").strip().zfill(2)
            if str(r.get("ipju_seq") or "").strip().isdigit()
            else str(r.get("ipju_seq") or "").strip(),
        )
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


def _to_int_amt(v):
    if v is None or v == "":
        return 0
    try:
        return int(Decimal(str(v).replace(",", "").strip() or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def _months_elapsed(ipju_dt, as_of=None):
    """입주일 ~ 기준일 경과연월 (같은 달이면 0)."""
    if not ipju_dt:
        return 0
    if as_of is None:
        as_of = date.today()
    if isinstance(ipju_dt, datetime):
        ipju_dt = ipju_dt.date()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    m = (as_of.year - ipju_dt.year) * 12 + (as_of.month - ipju_dt.month)
    return max(0, m)


def _calc_misu_amt(
    bunji1, bunji2, hosu, ipju_seq, rent_amt=None, manage_amt=None, ipju_dt=None, as_of=None
):
    """전월미수총액(누적 추정).
    (월세+관리비) × 입주 후 경과연월 − 수금성격「월세+관리비」합계.
    as_of 가 있으면 그 날짜까지의 수금·경과연월 기준.
    음수(선수금)면 0.
    """
    monthly = _to_int_amt(rent_amt) + _to_int_amt(manage_amt)
    if monthly <= 0 or not (bunji1 and bunji2 and hosu and ipju_seq):
        return 0
    months = _months_elapsed(ipju_dt, as_of)
    expected = monthly * months
    sql = """
        SELECT COALESCE(SUM(COALESCE(su_sil_amt,0) + COALESCE(su_dache_amt,0)), 0) AS paid
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
          AND sukum_char='01'
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
    """
    args = [bunji1, bunji2, (hosu or "").strip().upper(), ipju_seq]
    if as_of is not None:
        if isinstance(as_of, datetime):
            as_of = as_of.date()
        sql += " AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)"
        args.append(as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)[:10])
    paid_row = db.query_one(sql, args)
    paid = _to_int_amt((paid_row or {}).get("paid"))
    return max(0, expected - paid)


def _calc_month_misu_amt(
    bunji1, bunji2, hosu, ipju_seq, rent_amt=None, manage_amt=None, as_of=None
):
    """이번 달 미입금액(미수총액).
    (월세+관리비) − 이번 달 수금성격「월세+관리비」합.
    이미 다 냈으면 0.
    """
    monthly = _to_int_amt(rent_amt) + _to_int_amt(manage_amt)
    if monthly <= 0 or not (bunji1 and bunji2 and hosu and ipju_seq):
        return 0
    if as_of is None:
        as_of = date.today()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    month_start = as_of.replace(day=1)
    if as_of.month == 12:
        next_month = date(as_of.year + 1, 1, 1)
    else:
        next_month = date(as_of.year, as_of.month + 1, 1)
    paid_row = db.query_one(
        """
        SELECT COALESCE(SUM(COALESCE(su_sil_amt,0) + COALESCE(su_dache_amt,0)), 0) AS paid
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
          AND sukum_char='01'
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
          AND sukum_dt >= %s AND sukum_dt < %s
        """,
        (
            bunji1,
            bunji2,
            (hosu or "").strip().upper(),
            ipju_seq,
            month_start.isoformat() + " 00:00:00",
            next_month.isoformat(),
        ),
    )
    paid = _to_int_amt((paid_row or {}).get("paid"))
    return max(0, monthly - paid)


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
    bunji_raw = (request.args.get("bunji") or "").strip()
    if bunji_raw:
        bunji1, bunji2 = _parse_bunji_input(bunji_raw)
    else:
        bunji1 = _pad_bunji((request.args.get("bunji1") or "").strip())
        bunji2 = _pad_bunji((request.args.get("bunji2") or "").strip())
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
    bunji_raw = (request.args.get("bunji") or "").strip()
    if bunji_raw:
        bunji1, bunji2 = _parse_bunji_input(bunji_raw)
    else:
        bunji1 = _pad_bunji((request.args.get("bunji1") or "").strip())
        bunji2 = _pad_bunji((request.args.get("bunji2") or "").strip())
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


def _buildings_and_rooms():
    """화면 검증용: 등록 건물·호실 목록"""
    buildings = db.query(
        "SELECT bunji1, bunji2, juso FROM bd01 ORDER BY bunji1, bunji2"
    )
    rooms = db.query(
        """
        SELECT bunji1, bunji2, hosu
        FROM bd03_m
        ORDER BY bunji1, bunji2, hosu
        """
    )
    return buildings, rooms


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


@app.route("/payments/new", methods=["GET", "POST"])
@login_required
def payment_new():
    buildings, rooms = _buildings_and_rooms()
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

    # 주소 · 주소2 분리 입력 (링크용 bunji= 통합 값도 허용)
    arg_bunji = (request.args.get("bunji") or "").strip()
    if arg_bunji:
        arg_b1, arg_b2 = _parse_bunji_input(arg_bunji)
    else:
        arg_b1 = _pad_bunji((request.args.get("bunji1") or "").strip())
        arg_b2 = _pad_bunji((request.args.get("bunji2") or "").strip())
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

    tenants = []
    if pre["bunji1"] and pre["bunji2"]:
        tenants = db.query(
            """
            SELECT hosu, ipju_seq, ipju_nm, out_dt
            FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
            ORDER BY (out_dt IS NULL) DESC, hosu, ipju_seq DESC
            """,
            (pre["bunji1"], pre["bunji2"]),
        )
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
        bunji_raw = (request.form.get("bunji") or "").strip()
        if bunji_raw:
            bunji1, bunji2 = _parse_bunji_input(bunji_raw)
        else:
            bunji1 = _pad_bunji((request.form.get("bunji1") or "").strip())
            bunji2 = _pad_bunji((request.form.get("bunji2") or "").strip())
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
        tenants = db.query(
            """
            SELECT hosu, ipju_seq, ipju_nm, out_dt
            FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
            ORDER BY (out_dt IS NULL) DESC, hosu, ipju_seq DESC
            """,
            (bunji1, bunji2),
        ) if bunji1 and bunji2 else []

        recent = _recent_payments()

        try:
            amount = int(amount_raw or 0)
            dache_amt = int(dache_raw or 0)
        except ValueError:
            flash("금액은 숫자로 입력하세요.", "err")
            return render_template(
                "payment_new.html",
                buildings=buildings,
                rooms=rooms,
                chars=chars,
                gbs=gbs,
                form=pre,
                tenants=tenants,
                recent_payments=recent,
                building_label=_building_label(bunji1, bunji2),
            )

        if not (bunji1 and bunji2 and hosu and ipju_seq and sukum_dt):
            flash("건물(주소·주소2), 호실, 입주순번, 수금일은 필수입니다.", "err")
            return render_template(
                "payment_new.html",
                buildings=buildings,
                rooms=rooms,
                chars=chars,
                gbs=gbs,
                form=pre,
                tenants=tenants,
                recent_payments=recent,
                building_label=_building_label(bunji1, bunji2),
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
            return render_template(
                "payment_new.html",
                buildings=buildings,
                rooms=rooms,
                chars=chars,
                gbs=gbs,
                form=pre,
                tenants=tenants,
                recent_payments=recent,
                building_label=_building_label(bunji1, bunji2),
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
    return render_template(
        "payment_new.html",
        buildings=buildings,
        rooms=rooms,
        chars=chars,
        gbs=gbs,
        form=pre,
        tenants=tenants,
        recent_payments=recent,
        building_label=_building_label(pre["bunji1"], pre["bunji2"]),
    )


def _to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _period_mm_dd(ipju_dt, out_dt):
    """입주~퇴실 개월·일 (XP 입주기간)."""
    start = _to_date(ipju_dt)
    end = _to_date(out_dt)
    if not start or not end or end < start:
        return 0, 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
        # 이전달 말일
        first = end.replace(day=1)
        prev_last = first - timedelta(days=1)
        days = prev_last.day - start.day + end.day
    else:
        days = end.day - start.day
    return max(0, months), max(0, days)


def _checkout_build(bunji1, bunji2, hosu, ipju_seq, out_dt, extra=None):
    """
    퇴실 정산 미리보기 데이터.
    extra: 화면에서 입력한 공과·수리 등 {elec, water, restore, gas, etc, suri}
    """
    extra = extra or {}
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    hosu = (hosu or "").strip().upper()
    seq = (ipju_seq or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    out_d = _to_date(out_dt) or date.today()

    if not (b1 and b2 and hosu):
        return {"error": "주소·호수를 입력하세요."}

    def _missing_addr_or_room():
        bld = db.query_one(
            "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
            (b1, b2),
        )
        if not bld:
            return "등록되지 않은 주소입니다. 주소를 확인하세요."
        room = db.query_one(
            """
            SELECT hosu FROM bd03_m
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
            """,
            (b1, b2, hosu),
        )
        if not room:
            return "등록되지 않은 호수입니다. 호수를 확인하세요."
        return None

    tenant = None
    # 순번 지정(퇴실자 목록→정산 링크): 해당 이력 1건
    if seq:
        tenant = db.query_one(
            """
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (b1, b2, hosu, seq),
        )
        if not tenant:
            miss = _missing_addr_or_room()
            if miss:
                return {"error": miss}
            return {"error": f"순번 {seq} 입주 이력이 없습니다."}
    else:
        # 일반 조회: 반드시 현재 입주자 (과거 퇴실자로 자동 폴백 금지)
        tenant = db.query_one(
            f"""
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
              AND {_CURRENT_TENANT_SQL.replace('d.', '')}
            ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
            LIMIT 1
            """,
            (b1, b2, hosu),
        )
        if not tenant:
            miss = _missing_addr_or_room()
            if miss:
                return {"error": miss}
            past = db.query_one(
                """
                SELECT ipju_nm, ipju_seq, out_dt FROM bd03_det
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
                LIMIT 1
                """,
                (b1, b2, hosu),
            )
            if past:
                pnm = (past.get("ipju_nm") or "").strip() or "(이름없음)"
                pseq = str(past.get("ipju_seq") or "").zfill(2)
                pout = fmt_date(past.get("out_dt")) or ""
                extra_msg = f" 최근 퇴실: {pnm} (순번 {pseq}"
                if pout:
                    extra_msg += f", 퇴실 {pout}"
                extra_msg += ")."
                return {
                    "error": "현재 입주자가 없습니다." + extra_msg
                    + " 과거 정산은 퇴실(예정)자 조회에서 열어 주세요."
                }
            return {"error": "해당 호수의 입주 이력이 없습니다."}

    seq = str(tenant.get("ipju_seq") or "").zfill(2)
    ipju_d = _to_date(tenant.get("ipju_dt"))
    bojung = _to_int_amt(tenant.get("bojung_amt"))
    yechi = _to_int_amt(tenant.get("yechi_amt"))
    rent = _to_int_amt(tenant.get("rent_amt"))
    manage = _to_int_amt(tenant.get("manage_amt"))
    napbu = (tenant.get("napbu_gb") or "B").strip().upper()
    nm = (tenant.get("ipju_nm") or "").strip()
    tel = (
        (tenant.get("ipju_tel1") or tenant.get("ipju_tel3") or tenant.get("ipju_tel2") or "")
        .strip()
    )
    jumin = (tenant.get("ipju_jumin_no") or "").strip()

    mm, dd = _period_mm_dd(ipju_d, out_d)
    monthly = rent + manage
    # 후납 기준 입금총액 추정: 보증+예치+(임+관)×개월 + 일할 임대 + 수리
    suri = _to_int_amt(extra.get("suri"))
    if suri <= 0:
        suri_row = db.query_one(
            """
            SELECT COALESCE(SUM(COALESCE(ipjuja_budam,0)),0) AS a
            FROM bd05_suri
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (b1, b2, hosu, seq),
        )
        suri = _to_int_amt((suri_row or {}).get("a"))

    day_rent = int(round(rent * dd / 30.0)) if dd and rent else 0
    if napbu == "A":
        # 선납: 대략 보+예+(임+관)×개월 + 수리 − 일할 환급 추정
        base = bojung + yechi + monthly * mm + suri - day_rent
    else:
        base = bojung + yechi + monthly * mm + day_rent + suri
    base = max(0, base)

    pays = db.query(
        """
        SELECT sukum_dt, sukum_seq, sukum_char, sukum_gb,
               su_sil_amt, su_dache_amt
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
        ORDER BY sukum_dt, sukum_seq
        """,
        (b1, b2, hosu, seq),
    )
    pay_list = []
    sukum_tot = 0
    for p in pays or []:
        sil = _to_int_amt(p.get("su_sil_amt"))
        dac = _to_int_amt(p.get("su_dache_amt"))
        tot = sil + dac
        sukum_tot += tot
        pay_list.append(
            {
                "dt": _fmt_ipju_short(p.get("sukum_dt")),
                "dt_full": fmt_date(p.get("sukum_dt")),
                "amt": tot,
                "amt_disp": money(tot),
                "sil": sil,
                "dache": dac,
                "char": p.get("sukum_char") or "",
            }
        )

    h_amt = bojung + yechi  # 환불성수금
    misu = max(0, base - sukum_tot)
    elec = _to_int_amt(extra.get("elec"))
    water = _to_int_amt(extra.get("water"))
    restore = _to_int_amt(extra.get("restore"))
    gas = _to_int_amt(extra.get("gas"))
    gita = _to_int_amt(extra.get("etc"))
    util_tot = elec + water + restore + gas + gita
    jungsan = h_amt - misu - util_tot

    bldg = db.query_one(
        "SELECT juso FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (b1, b2),
    )
    juso = ((bldg or {}).get("juso") or "").strip()
    addr = f"{fmt_bunji(b1)}-{fmt_bunji(b2)}"
    nap_label = "선" if napbu == "A" else "후"
    rent_man_label = f"{int(round(rent/10000)) if rent else 0}/{int(round(manage/10000)) if manage else 0} {nap_label}"
    is_current = not _tenant_is_past_out(tenant.get("out_dt"))

    return {
        "error": None,
        "bunji1": b1,
        "bunji2": b2,
        "hosu": hosu,
        "ipju_seq": seq,
        "out_dt": out_d.isoformat(),
        "gijun_dt": out_d.isoformat(),
        "ipju_dt": fmt_date(ipju_d),
        "tenant_out_dt": fmt_date(tenant.get("out_dt")) if not is_current else "",
        "is_current": is_current,
        "ipju_nm": nm,
        "tel": tel,
        "jumin": jumin,
        "bojung_amt": bojung,
        "yechi_amt": yechi,
        "rent_amt": rent,
        "manage_amt": manage,
        "napbu_gb": napbu,
        "nap_label": nap_label,
        "rent_manage_label": rent_man_label,
        "addr": addr,
        "juso": juso,
        "mm": mm,
        "dd": dd,
        "period_label": f"{mm}개월 {dd}일",
        "suri_amt": suri,
        "ipkum_gijun": base,
        "sukum_tot": sukum_tot,
        "h_amt": h_amt,
        "misu_amt": misu,
        "elec_amt": elec,
        "sudo_amt": water,
        "sisul_amt": restore,
        "gas_amt": gas,
        "gita_amt": gita,
        "util_tot": util_tot,
        "jungsan_amt": jungsan,
        "payments": pay_list,
        "building_label": juso or addr,
    }


def _checkout_to_print(data):
    """계약 해지 인쇄 템플릿용 dict."""
    util_tot = _to_int_amt(data.get("util_tot"))
    h = _to_int_amt(data.get("h_amt"))
    misu = _to_int_amt(data.get("misu_amt"))
    jungsan = _to_int_amt(data.get("jungsan_amt"))
    doc = {
        "name": data.get("ipju_nm") or "",
        "addr": data.get("addr") or "",
        "hosu": data.get("hosu") or "",
        "key_note": "",
        "broker_note": "",
        "move_after": "",
        "tel": data.get("tel") or "",
        "bojung_disp": money(data.get("bojung_amt")),
        "rent_manage_label": data.get("rent_manage_label") or "",
        "ipju_dt": data.get("ipju_dt") or "",
        "out_dt": data.get("out_dt") or "",
        "etc_note": "",
    }
    fac = {k: "" for k in (
        "door", "kitchen", "wallpaper", "floor", "light", "window",
        "bath", "boiler", "key2", "contract", "clean", "convert_amt",
    )}
    rent = {
        "period": data.get("period_label") or "",
        "repair_amt": money(data.get("suri_amt")) if _to_int_amt(data.get("suri_amt")) else "",
        "base_total": money(data.get("ipkum_gijun")),
        "paid_total": money(data.get("sukum_tot")),
        "refundable": money(data.get("h_amt")),
        "unpaid": money(data.get("misu_amt")),
    }
    payments = [
        {"dt": p.get("dt") or "", "amt": p.get("amt_disp") or money(p.get("amt"))}
        for p in (data.get("payments") or [])
    ]
    util = {
        "elec": money(data.get("elec_amt")) if _to_int_amt(data.get("elec_amt")) else "",
        "water": money(data.get("sudo_amt")) if _to_int_amt(data.get("sudo_amt")) else "",
        "restore": money(data.get("sisul_amt")) if _to_int_amt(data.get("sisul_amt")) else "",
        "gas": money(data.get("gas_amt")) if _to_int_amt(data.get("gas_amt")) else "",
        "etc": money(data.get("gita_amt")) if _to_int_amt(data.get("gita_amt")) else "",
        "total": money(util_tot) if util_tot else "",
    }
    settle = {
        "amount": money(jungsan),
        "formula_nums": f"({money(h)} - {money(misu)} - {money(util_tot) or '0'})",
        "confirm_year": str(date.today().year),
        "confirm_month": "",
        "confirm_day": "",
        "confirmer": session.get("s_name") or "",
    }
    return doc, fac, rent, payments, util, settle


@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    """퇴실 정산 관리 (XP). 조회·저장·인쇄."""
    today = date.today().isoformat()

    def _form_from_req(src):
        return {
            "out_dt": (src.get("out_dt") or today).strip()[:10],
            "bunji1": _pad_bunji(src.get("bunji1")),
            "bunji2": _pad_bunji(src.get("bunji2")),
            "hosu": (src.get("hosu") or "").strip().upper(),
            "ipju_seq": (src.get("ipju_seq") or "").strip(),
            "suri": (src.get("suri") or "0").strip(),
            "elec": (src.get("elec") or "0").strip(),
            "water": (src.get("water") or "0").strip(),
            "restore": (src.get("restore") or "0").strip(),
            "gas": (src.get("gas") or "0").strip(),
            "etc": (src.get("etc") or "0").strip(),
        }

    if request.method == "POST":
        action = (request.form.get("action") or "calc").strip()
        f = _form_from_req(request.form)
        extra = {
            "suri": f["suri"],
            "elec": f["elec"],
            "water": f["water"],
            "restore": f["restore"],
            "gas": f["gas"],
            "etc": f["etc"],
        }

        if action == "new":
            return redirect(url_for("checkout"))

        data = _checkout_build(
            f["bunji1"], f["bunji2"], f["hosu"], f["ipju_seq"], f["out_dt"], extra
        )
        if data.get("error"):
            flash(data["error"], "err")
            return redirect(url_for("checkout", **{k: v for k, v in f.items() if v}))

        if action == "save":
            uid = session.get("sabun") or ""
            out_d = data["out_dt"]
            # out_seq
            mx = db.query_one(
                """
                SELECT MAX(CAST(out_seq AS UNSIGNED)) AS m FROM bd07_out
                WHERE out_dt=%s AND bunji1=%s AND bunji2=%s AND hosu=%s AND ipju_seq=%s
                """,
                (out_d, data["bunji1"], data["bunji2"], data["hosu"], data["ipju_seq"]),
            )
            out_seq = str(int((mx or {}).get("m") or 0) + 1).zfill(2)
            try:
                # 기존 건 있으면 갱신
                exists = db.query_one(
                    """
                    SELECT out_seq FROM bd07_out
                    WHERE bunji1=%s AND bunji2=%s AND hosu=%s AND ipju_seq=%s
                      AND out_dt=%s
                    ORDER BY out_seq DESC LIMIT 1
                    """,
                    (
                        data["bunji1"],
                        data["bunji2"],
                        data["hosu"],
                        data["ipju_seq"],
                        out_d,
                    ),
                )
                if exists:
                    out_seq = exists["out_seq"]
                    db.execute(
                        """
                        UPDATE bd07_out SET
                          gijun_dt=%s, ipju_mm_cnt=%s, ipju_dd_cnt=%s,
                          ipkum_gijun=%s, sukum_tot=%s, h_amt=%s,
                          elec_amt=%s, sudo_amt=%s, sisul_amt=%s, gas_amt=%s, gita_amt=%s,
                          jungsan_amt=%s, g_suri_tot=%s,
                          g_napbu_gb=%s, g_ipju_dt=%s, g_ipju_nm=%s,
                          g_ipju_jumin_no=%s, g_ipju_tel=%s,
                          g_bojung_amt=%s, g_yechi_amt=%s, g_rent_amt=%s, g_manage_amt=%s,
                          uid=%s, sys_dt=NOW()
                        WHERE out_dt=%s AND out_seq=%s AND bunji1=%s AND bunji2=%s
                          AND hosu=%s AND ipju_seq=%s
                        """,
                        (
                            out_d,
                            data["mm"],
                            data["dd"],
                            data["ipkum_gijun"],
                            data["sukum_tot"],
                            data["h_amt"],
                            data["elec_amt"],
                            data["sudo_amt"],
                            data["sisul_amt"],
                            data["gas_amt"],
                            data["gita_amt"],
                            data["jungsan_amt"],
                            data["suri_amt"],
                            data["napbu_gb"],
                            data["ipju_dt"] or None,
                            data["ipju_nm"],
                            data["jumin"][:15],
                            data["tel"][:50],
                            data["bojung_amt"],
                            data["yechi_amt"],
                            data["rent_amt"],
                            data["manage_amt"],
                            uid,
                            out_d,
                            out_seq,
                            data["bunji1"],
                            data["bunji2"],
                            data["hosu"],
                            data["ipju_seq"],
                        ),
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO bd07_out (
                          out_dt, out_seq, gijun_dt, bunji1, bunji2, hosu, ipju_seq,
                          ipju_mm_cnt, ipju_dd_cnt, ipkum_gijun, sukum_tot, h_amt,
                          elec_amt, sudo_amt, sisul_amt, gas_amt, gita_amt, jungsan_amt,
                          g_suri_tot, g_napbu_gb, g_ipju_dt, g_ipju_nm, g_ipju_jumin_no,
                          g_ipju_tel, g_bojung_amt, g_yechi_amt, g_rent_amt, g_manage_amt,
                          uid, sys_dt
                        ) VALUES (
                          %s,%s,%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,
                          %s,NOW()
                        )
                        """,
                        (
                            out_d,
                            out_seq,
                            out_d,
                            data["bunji1"],
                            data["bunji2"],
                            data["hosu"],
                            data["ipju_seq"],
                            data["mm"],
                            data["dd"],
                            data["ipkum_gijun"],
                            data["sukum_tot"],
                            data["h_amt"],
                            data["elec_amt"],
                            data["sudo_amt"],
                            data["sisul_amt"],
                            data["gas_amt"],
                            data["gita_amt"],
                            data["jungsan_amt"],
                            data["suri_amt"],
                            data["napbu_gb"],
                            data["ipju_dt"] or None,
                            data["ipju_nm"],
                            data["jumin"][:15],
                            data["tel"][:50],
                            data["bojung_amt"],
                            data["yechi_amt"],
                            data["rent_amt"],
                            data["manage_amt"],
                            uid,
                        ),
                    )
                # 입주 이력 퇴실일 반영
                db.execute(
                    """
                    UPDATE bd03_det
                    SET out_dt=%s, out_seq=%s, out_jungsan_end=%s, sys_dt=NOW(), uid=%s
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                    """,
                    (
                        out_d,
                        out_seq,
                        data["jungsan_amt"],
                        uid,
                        data["bunji1"],
                        data["bunji2"],
                        data["hosu"],
                        data["ipju_seq"],
                    ),
                )
                flash(
                    f"퇴실 정산을 저장했습니다. ({data['ipju_nm']} · 정산 {money(data['jungsan_amt'])}원)",
                    "ok",
                )
            except Exception as e:
                flash(f"저장 실패: {e}", "err")
            return redirect(
                url_for(
                    "checkout",
                    bunji1=fmt_bunji(data["bunji1"]),
                    bunji2=fmt_bunji(data["bunji2"]),
                    hosu=data["hosu"],
                    ipju_seq=data["ipju_seq"],
                    out_dt=data["out_dt"],
                )
            )

        # action=calc → 같은 화면 재표시 (GET으로)
        return redirect(
            url_for(
                "checkout",
                bunji1=fmt_bunji(f["bunji1"]) if f["bunji1"] else "",
                bunji2=fmt_bunji(f["bunji2"]) if f["bunji2"] else "",
                hosu=f["hosu"],
                ipju_seq=f["ipju_seq"],
                out_dt=f["out_dt"],
                suri=f["suri"],
                elec=f["elec"],
                water=f["water"],
                restore=f["restore"],
                gas=f["gas"],
                etc=f["etc"],
            )
        )

    # GET
    f = _form_from_req(request.args)
    if not f["out_dt"]:
        f["out_dt"] = today
    data = None
    if f["bunji1"] and f["bunji2"] and f["hosu"]:
        extra = {
            "suri": f["suri"],
            "elec": f["elec"],
            "water": f["water"],
            "restore": f["restore"],
            "gas": f["gas"],
            "etc": f["etc"],
        }
        data = _checkout_build(
            f["bunji1"], f["bunji2"], f["hosu"], f["ipju_seq"], f["out_dt"], extra
        )
        if data.get("error"):
            flash(data["error"], "err")
            data = None
        elif data:
            f["ipju_seq"] = data.get("ipju_seq") or f["ipju_seq"]

    return render_template(
        "checkout.html",
        form=f,
        data=data,
        building_label=_building_label(f["bunji1"], f["bunji2"])
        if f["bunji1"] and f["bunji2"]
        else "",
    )


@app.route("/checkout/list", methods=["GET", "POST"])
@login_required
def checkout_list():
    """
    퇴실(예정)자 조회 및 변경처리 (XP「호수 퇴실자 내역 조회」).
    목록 조회 + 현입주자로 변환(퇴실일 취소).
    """
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        b1 = _pad_bunji(request.form.get("bunji1"))
        b2 = _pad_bunji(request.form.get("bunji2"))
        hosu = (request.form.get("hosu") or "").strip().upper()
        seq = (request.form.get("ipju_seq") or "").strip()
        if seq.isdigit():
            seq = seq.zfill(2)
        keep = {
            "bunji1": fmt_bunji(b1) if b1 else request.form.get("q_bunji1") or "",
            "bunji2": fmt_bunji(b2) if b2 else request.form.get("q_bunji2") or "",
            "hosu": (request.form.get("q_hosu") or request.form.get("hosu") or "").strip().upper(),
            "name": (request.form.get("q_name") or request.form.get("name") or "").strip(),
            "mode": request.form.get("mode") or "all",
        }
        if action == "restore":
            if not (b1 and b2 and hosu and seq):
                flash("변환할 행을 선택하세요.", "err")
                return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))
            # 같은 호에 이미 현거주가 있으면 차단
            other = db.query_one(
                f"""
                SELECT ipju_seq, ipju_nm FROM bd03_det
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                  AND ipju_seq<>%s
                  AND {_CURRENT_TENANT_SQL.replace('d.', '')}
                LIMIT 1
                """,
                (b1, b2, hosu, seq),
            )
            if other:
                flash(
                    f"같은 호에 현거주자({other.get('ipju_nm') or other.get('ipju_seq')})가 있어 "
                    "현입주자로 변환할 수 없습니다.",
                    "err",
                )
                return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))
            try:
                n = db.execute(
                    """
                    UPDATE bd03_det
                    SET out_dt=NULL, out_seq=NULL, plan_out_dt=NULL,
                        sys_dt=NOW(), uid=%s
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                    """,
                    (session.get("sabun") or "", b1, b2, hosu, seq),
                )
                if n:
                    flash("현입주자로 변환했습니다. (퇴실·예정일 취소)", "ok")
                else:
                    flash("대상 이력을 찾지 못했습니다.", "err")
            except Exception as e:
                flash(f"변환 실패: {e}", "err")
            return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))

        return redirect(url_for("checkout_list"))

    # GET
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    name = (request.args.get("name") or "").strip()
    mode = (request.args.get("mode") or "all").strip().lower()
    if mode not in ("all", "out", "plan"):
        mode = "all"
    per_page = 30
    try:
        page = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    # 최소 조건: 주소 · 호수 · 이름 중 하나 이상
    has_min_filter = bool(bunji1 or hosu or name)
    want_query = "q" in request.args
    ran = want_query and has_min_filter
    empty_msg = ""
    if want_query and not has_min_filter:
        empty_msg = "주소 · 호수 · 이름 중 하나 이상 입력한 뒤 조회하세요."

    results = []
    total = 0
    total_pages = 1
    if ran:
        where = ["1=1"]
        args = []
        if mode == "out":
            where.append("(out_dt IS NOT NULL AND out_dt >= '1000-01-01')")
        elif mode == "plan":
            where.append(
                "(plan_out_dt IS NOT NULL AND plan_out_dt >= '1000-01-01') "
                "AND (out_dt IS NULL OR out_dt < '1000-01-01')"
            )
        else:
            where.append(
                "((out_dt IS NOT NULL AND out_dt >= '1000-01-01') "
                "OR (plan_out_dt IS NOT NULL AND plan_out_dt >= '1000-01-01'))"
            )
        if bunji1:
            where.append("bunji1=%s")
            args.append(bunji1)
        if bunji2:
            where.append("bunji2=%s")
            args.append(bunji2)
        if hosu:
            where.append("UPPER(TRIM(hosu))=%s")
            args.append(hosu)
        if name:
            where.append("ipju_nm LIKE %s")
            args.append(f"%{name}%")
        where_sql = " AND ".join(where)

        cnt = db.query_one(
            f"SELECT COUNT(*) AS c FROM bd03_det WHERE {where_sql}",
            args,
        )
        total = int((cnt or {}).get("c") or 0)
        total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * per_page

        rows = db.query(
            f"""
            SELECT bunji1, bunji2, hosu, ipju_seq, ipju_nm, ipju_jumin_no,
                   ipju_dt, out_dt, plan_out_dt, bojung_amt, rent_amt, manage_amt, napbu_gb
            FROM bd03_det
            WHERE {where_sql}
            ORDER BY COALESCE(out_dt, plan_out_dt) DESC, bunji1, bunji2, hosu
            LIMIT %s OFFSET %s
            """,
            args + [per_page, offset],
        )
        for r in rows:
            has_out = r.get("out_dt") and (
                not isinstance(r["out_dt"], datetime) or r["out_dt"].year >= 1000
            )
            has_plan = r.get("plan_out_dt") and (
                not isinstance(r["plan_out_dt"], datetime) or r["plan_out_dt"].year >= 1000
            )
            if has_out:
                kind = "퇴실"
                kind_dt = r.get("out_dt")
            elif has_plan:
                kind = "퇴실예정"
                kind_dt = r.get("plan_out_dt")
            else:
                kind = "—"
                kind_dt = None
            results.append(
                {
                    **r,
                    "kind": kind,
                    "kind_dt": kind_dt,
                    # 퇴실일 / 퇴실예정일 분리 표시 (한 칸에 합치지 않음)
                    "out_dt_disp": fmt_date(r.get("out_dt")) if has_out else "",
                    "plan_out_dt_disp": fmt_date(r.get("plan_out_dt"))
                    if has_plan
                    else "",
                    "jumin_disp": mask_jumin(r.get("ipju_jumin_no")),
                }
            )

    building_label = (
        _building_label(bunji1, bunji2) if bunji1 and bunji2 else ""
    )

    # 결과 0건일 때 원인 구분 (주소/호수/이력)
    if ran and total == 0:
        mode_lab = {"all": "퇴실·예정", "out": "퇴실", "plan": "퇴실예정"}.get(
            mode, "퇴실·예정"
        )
        if bunji1 and bunji2:
            bld = db.query_one(
                "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
                (bunji1, bunji2),
            )
            if not bld:
                empty_msg = "등록되지 않은 주소입니다. 주소를 확인하세요."
            elif hosu:
                room = db.query_one(
                    """
                    SELECT hosu FROM bd03_m
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                    """,
                    (bunji1, bunji2, hosu),
                )
                if not room:
                    empty_msg = "등록되지 않은 호수입니다. 호수를 확인하세요."
                else:
                    empty_msg = f"해당 호수의 {mode_lab} 이력이 없습니다."
            else:
                empty_msg = f"해당 주소의 {mode_lab} 이력이 없습니다."
        elif bunji1 and not bunji2:
            empty_msg = f"주소 조건에 맞는 {mode_lab} 이력이 없습니다."
        elif hosu:
            empty_msg = f"호수 조건에 맞는 {mode_lab} 이력이 없습니다."
        elif name:
            empty_msg = f"「{name}」 이름에 맞는 {mode_lab} 이력이 없습니다."
        else:
            empty_msg = f"조건에 맞는 {mode_lab} 이력이 없습니다."

    # 페이지 번호: 6개 단위 블록 (1–6, 7–12 …) · 이전/다음은 블록 점프
    page_window = []
    prev_block_page = 1
    next_block_page = 1
    has_prev_block = False
    has_next_block = False
    page_block_size = 6
    if total_pages > 0:
        block = (page - 1) // page_block_size
        start_p = block * page_block_size + 1
        end_p = min(total_pages, start_p + page_block_size - 1)
        page_window = list(range(start_p, end_p + 1))
        # 이전 블록 첫 페이지 / 다음 블록 첫 페이지
        if start_p > 1:
            has_prev_block = True
            prev_block_page = max(1, start_p - page_block_size)
        if end_p < total_pages:
            has_next_block = True
            next_block_page = end_p + 1

    return render_template(
        "checkout_list.html",
        filters={
            "bunji1": bunji1,
            "bunji2": bunji2,
            "hosu": hosu,
            "name": name,
            "mode": mode,
            "page": page,
        },
        results=results,
        ran=ran,
        want_query=want_query,
        building_label=building_label,
        empty_msg=empty_msg,
        pager={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "page_window": page_window,
            "has_prev": has_prev_block,
            "has_next": has_next_block,
            "prev_page": prev_block_page,
            "next_page": next_block_page,
        },
    )


@app.route("/checkout/print")
@login_required
def checkout_print():
    """계약 해지 인쇄 — 퇴실 정산 데이터 바인딩."""
    f = {
        "out_dt": (request.args.get("out_dt") or date.today().isoformat())[:10],
        "bunji1": _pad_bunji(request.args.get("bunji1")),
        "bunji2": _pad_bunji(request.args.get("bunji2")),
        "hosu": (request.args.get("hosu") or "").strip().upper(),
        "ipju_seq": (request.args.get("ipju_seq") or "").strip(),
        "suri": request.args.get("suri") or "0",
        "elec": request.args.get("elec") or "0",
        "water": request.args.get("water") or "0",
        "restore": request.args.get("restore") or "0",
        "gas": request.args.get("gas") or "0",
        "etc": request.args.get("etc") or "0",
    }
    data = _checkout_build(
        f["bunji1"],
        f["bunji2"],
        f["hosu"],
        f["ipju_seq"],
        f["out_dt"],
        {
            "suri": f["suri"],
            "elec": f["elec"],
            "water": f["water"],
            "restore": f["restore"],
            "gas": f["gas"],
            "etc": f["etc"],
        },
    )
    if not data or data.get("error"):
        flash(data.get("error") if data else "인쇄할 데이터가 없습니다.", "err")
        return redirect(url_for("checkout"))
    doc, fac, rent, payments, util, settle = _checkout_to_print(data)
    return render_template(
        "contract_cancel_print.html",
        doc=doc,
        fac=fac,
        rent=rent,
        payments=payments,
        util=util,
        settle=settle,
    )


@app.route("/print/contract-cancel")
@app.route("/print/contract-cancel/sample")
@login_required
def contract_cancel_print_sample():
    """샘플 계약 해지 인쇄 (강현숙 PDF 수치)."""
    return redirect(
        url_for(
            "checkout_print",
            bunji1="1139",
            bunji2="4",
            hosu="707",
            ipju_seq="12",
            out_dt="2026-08-11",
        )
    )


if __name__ == "__main__":
    print("원룸 관리 웹: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
