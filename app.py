from flask import (
    flash,
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
    fmt_bunji,
    fmt_bunji_pair,
    fmt_date,
    login_required,
    mask_jumin,
    mask_phone,
    money,
)

# 화면 모듈 import 순간 @app.route 가 같은 app 에 등록됨.
import building as building_routes  # noqa: F401
import repair as repair_routes  # noqa: F401
import misu as misu_routes  # noqa: F401
import jungsan as jungsan_routes  # noqa: F401
import jungke as jungke_routes  # noqa: F401
import tenants as tenants_routes  # noqa: F401
import users as users_routes  # noqa: F401
import search as search_routes  # noqa: F401
import payments as payments_routes  # noqa: F401
import checkout as checkout_routes  # noqa: F401

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


if __name__ == "__main__":
    print("원룸 관리 웹: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
