import logging
import os
from datetime import date, datetime
from flask import (
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_from_directory,
)

import db
from app_instance import app
from nav import nav_context
from utils import (
    CURRENT_TENANT_SQL as _CURRENT_TENANT_SQL,
    fmt_bunji,
    fmt_bunji_pair,
    fmt_date,
    login_required,
    mask_jumin,
    mask_phone,
    money,
)

# 화면 모듈 import (라우트 자동 등록)
import building as building_routes  # noqa: F401
import checkout as checkout_routes  # noqa: F401
import docs as docs_routes  # noqa: F401
import sukum_import as sukum_import_routes  # noqa: F401
import jungsan as jungsan_routes  # noqa: F401
import jungke as jungke_routes  # noqa: F401
import misu as misu_routes  # noqa: F401
import payments as payments_routes  # noqa: F401
import repair as repair_routes  # noqa: F401
import search as search_routes  # noqa: F401
import tenants as tenants_routes  # noqa: F401
import users as users_routes  # noqa: F401

# 로깅 설정
logging.basicConfig(level=logging.INFO)

# Jinja2 템플릿 필터 등록
app.jinja_env.filters["money"] = money
app.jinja_env.filters["fmt_date"] = fmt_date
app.jinja_env.filters["bunji"] = fmt_bunji
app.jinja_env.filters["bunji_pair"] = fmt_bunji_pair
app.jinja_env.filters["mask_phone"] = mask_phone
app.jinja_env.filters["mask_jumin"] = mask_jumin


def asset_version(rel_path):
    """static/ 파일의 수정시각을 캐시버스터로. 파일 바뀌면 URL도 바뀌어서
    하드 리프레시 없이도 새 CSS가 반영됨."""
    try:
        full = os.path.join(app.static_folder, rel_path)
        return int(os.path.getmtime(full))
    except OSError:
        return 0


app.jinja_env.globals["asset_version"] = asset_version



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
    except Exception as e:
        app.logger.warning(f"[Nav Context Error] {e}")
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
    """홈: 메뉴 + 메인 요약 KPI (공실·입주 등)."""
    stats = {
        "building_total": 0,
        "room_total": 0,
        "occupied_total": 0,
        "vacant_total": 0,
        "occ_pct": 0,
        "vac_pct": 0,
        "misu_total": 0,
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
        if totals:
            room_total = int(totals.get("room_total") or 0)
            occupied_total = int(totals.get("occupied_total") or 0)
            vacant_total = max(0, room_total - occupied_total)
            stats["building_total"] = int(totals.get("building_total") or 0)
            stats["room_total"] = room_total
            stats["occupied_total"] = occupied_total
            stats["vacant_total"] = vacant_total

            if room_total > 0:
                stats["occ_pct"] = round(occupied_total * 100 / room_total)
                stats["vac_pct"] = max(0, 100 - stats["occ_pct"])

        # 미수금 건수 계산 (미수 페이지와 동일한 로직 사용)
        from calendar import monthrange
        from utils import months_elapsed as _months_elapsed, to_int_amt as _to_int_amt

        today = date.today()
        default_as_of = date(
            today.year, today.month, monthrange(today.year, today.month)[1]
        )
        as_of_s = default_as_of.isoformat()

        misu_results = db.query(
            f"""
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
            WHERE {_CURRENT_TENANT_SQL}
              AND (d.ipju_dt IS NULL OR d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY))
            ORDER BY d.bunji1, d.bunji2, d.hosu, d.ipju_seq
            LIMIT 2000
            """,
            [as_of_s, as_of_s]
        )

        misu_count = 0

        for r in misu_results:
            rent = _to_int_amt(r.get("rent_amt"))
            manage = _to_int_amt(r.get("manage_amt"))
            monthly = rent + manage
            ipju_dt = r.get("ipju_dt")
            if ipju_dt:
                try:
                    if isinstance(ipju_dt, str):
                        ipju_dt = datetime.strptime(ipju_dt[:10], "%Y-%m-%d").date()
                    elif isinstance(ipju_dt, datetime):
                        ipju_dt = ipju_dt.date()
                    else:
                        ipju_dt = datetime.strptime(str(ipju_dt)[:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue

                months = _months_elapsed(ipju_dt, default_as_of)
                expected = monthly * months
                paid = _to_int_amt(r.get("paid"))
                misu_amt = max(0, expected - paid)
                if misu_amt > 0:
                    misu_count += 1

        stats["misu_total"] = misu_count
    except Exception as e:
        app.logger.error(f"[Home Stats Error] {e}")

    return render_template("home.html", stats=stats)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        sabun = (request.form.get("sabun") or "").strip()
        password = (request.form.get("password") or "").strip()

        if not sabun or not password:
            flash("사번과 비밀번호를 모두 입력해주세요.", "err")
            return render_template("login.html")

        try:
            row = db.query_one(
                "SELECT sabun, s_name, grade, pass_wd FROM sawon_m WHERE sabun=%s",
                (sabun,),
            )

            if row and (row.get("pass_wd") or "").strip() == password:
                session.clear()
                session["sabun"] = row["sabun"]
                session["s_name"] = row["s_name"]
                session["grade"] = row["grade"]
                return redirect(url_for("home"))

            flash("사번 또는 비밀번호가 올바르지 않습니다.", "err")
        except Exception as e:
            app.logger.error(f"[Login DB Error] {e}")
            flash("로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.", "err")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("로그아웃했습니다.", "ok")
    return redirect(url_for("login"))


@app.route('/favicon.ico')
def favicon():
    """브라우저 파비콘 요청 시 static/app.ico 강제 반환"""
    return send_from_directory(os.path.join(app.root_path, 'static'), 'app.ico', mimetype='image/vnd.microsoft.icon')


if __name__ == "__main__":
    print("원룸 관리 웹: http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)