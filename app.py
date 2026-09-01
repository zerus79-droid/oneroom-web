import logging
import os
import time
from collections import defaultdict
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
    fmt_bunji,
    fmt_bunji_pair,
    fmt_date,
    fmt_ipju_short,
    login_required,
    mask_jumin,
    mask_phone,
    money,
)

_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCK_SECONDS = 600
_login_attempts = defaultdict(lambda: {"failures": 0, "locked_until": 0.0})


@app.context_processor
def csrf_context():
    import secrets as _secrets
    token = session.get("csrf_token")
    if not token:
        token = _secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return {"csrf_token": token}


@app.before_request
def validate_csrf():
    if request.method != "POST":
        return None
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("csrf_token")
    if not expected or not supplied or not __import__("hmac").compare_digest(str(supplied), str(expected)):
        return "CSRF token validation failed", 400
    return None

# 화면 모듈 import (라우트 자동 등록)
import building_access as building_access_routes  # noqa: F401
import building as building_routes  # noqa: F401
import checkout as checkout_routes  # noqa: F401
import docs as docs_routes  # noqa: F401
import sukum_import as sukum_import_routes  # noqa: F401
import jungsan as jungsan_routes  # noqa: F401
import jungke as jungke_routes  # noqa: F401
import misu as misu_routes  # noqa: F401
import payments as payments_routes  # noqa: F401
import payment_register as payment_register_routes  # noqa: F401
import payments_api as payments_api_routes  # noqa: F401
import repair as repair_routes  # noqa: F401
import search as search_routes  # noqa: F401
import tenants as tenants_routes  # noqa: F401
import users as users_routes  # noqa: F401

# 로깅 설정
logging.basicConfig(level=logging.INFO)

# Jinja2 템플릿 필터 등록
app.jinja_env.filters["money"] = money
app.jinja_env.filters["fmt_date"] = fmt_date
app.jinja_env.filters["fmt_ipju_short"] = fmt_ipju_short
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
    """홈: 메뉴 + 자주 쓰는 등록 바로가기."""
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        sabun = (request.form.get("sabun") or "").strip()
        password = (request.form.get("password") or "").strip()
        attempt_key = (request.remote_addr or "unknown", sabun.lower())
        attempt = _login_attempts[attempt_key]
        if attempt["locked_until"] > time.monotonic():
            flash("로그인 시도 횟수를 초과했습니다. 잠시 후 다시 시도하세요.", "err")
            return render_template("login.html")

        if not sabun or not password:
            flash("사번과 비밀번호를 모두 입력해주세요.", "err")
            return render_template("login.html")

        try:
            row = db.query_one(
                "SELECT sabun, s_name, grade, pass_wd FROM sawon_m WHERE sabun=%s",
                (sabun,),
            )

            if row and (row.get("pass_wd") or "").strip() == password:
                _login_attempts.pop(attempt_key, None)
                session.clear()
                session["sabun"] = row["sabun"]
                session["s_name"] = row["s_name"]
                session["grade"] = row["grade"]
                return redirect(url_for("home"))

            attempt["failures"] += 1
            if attempt["failures"] >= _LOGIN_MAX_FAILURES:
                attempt["locked_until"] = time.monotonic() + _LOGIN_LOCK_SECONDS
                flash("로그인 5회 실패로 10분간 로그인이 차단됩니다.", "err")
            else:
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
    print("원룸 관리 웹: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
