import logging
import os
from datetime import date, datetime
from calendar import monthrange
from flask import (
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_from_directory,
    jsonify,
)
from werkzeug.datastructures import MultiDict

import db
from app_instance import app
from logs_handler import app_logger, security_logger, log_security_event, log_access
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
    months_elapsed,
    to_int_amt,
    hash_password,
    verify_password,
    record_login_attempt,
    get_recent_failed_attempts,
    resolve_hosu as _resolve_hosu,
)

_CURRENT_TENANT_SQL = "(d.out_dt IS NULL OR d.out_dt < '1000-01-01')"

# 로그인 보안 상수
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_MINUTES = 10


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
        log_security_event(
            'csrf_violation',
            ip_address=request.remote_addr,
            user_id=session.get('sabun'),
            details=f"CSRF token mismatch on {request.endpoint}"
        )
        return "CSRF token validation failed", 400
    return None


@app.before_request
def normalize_hosu_param():
    """호수(hosu) 파라미터 전역 정규화: ㅠ→B, 지하호 자동완성.
    CSRF 검증 이후 실행됨.
    """
    # hosu 파라미터가 없으면 바로 반환 (성능)
    if "hosu" not in request.values:
        return

    hosu_raw = (request.values.get("hosu") or "").strip().upper()
    if not hosu_raw:
        return

    # ㅠ → B 치환
    hosu = hosu_raw.replace("ㅠ", "B")

    # bunji1, bunji2가 있으면 지하호 자동완성 (예: "01" → "B01")
    bunji1 = (request.values.get("bunji1") or "").strip()
    bunji2 = (request.values.get("bunji2") or "").strip()
    if bunji1 and bunji2:
        hosu = _resolve_hosu(bunji1, bunji2, hosu)

    # 값이 변경되지 않았으면 수정 불필요
    if hosu == hosu_raw:
        return

    # GET 파라미터 (request.args) 수정
    if request.args and "hosu" in request.args:
        args_dict = request.args.to_dict()
        args_dict["hosu"] = hosu
        request.args = MultiDict(args_dict)

    # POST 파라미터 (request.form) 수정
    if request.form and "hosu" in request.form:
        form_dict = request.form.to_dict()
        form_dict["hosu"] = hosu
        request.form = MultiDict(form_dict)


@app.after_request
def add_security_headers(response):
    """모든 응답에 보안 헤더 추가."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains' if os.getenv('FLASK_ENV') == 'production' else ''
    
    # 액세스 로그 기록
    log_access(
        ip_address=request.remote_addr,
        method=request.method,
        endpoint=request.endpoint or 'unknown',
        status_code=response.status_code,
        user_id=session.get('sabun')
    )
    
    return response

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
    """홈: 메뉴 + 실제 DB 기반 요약 통계."""
    stats = {"building_total": 0, "room_total": 0, "occupied_total": 0,
             "vacant_total": 0, "occ_pct": 0, "vac_pct": 0, "misu_total": 0}
    try:
        # 기본 통계 - 별도 쿼리로 분리해서 최적화
        room_total = db.query_one("SELECT COUNT(*) AS c FROM bd03_m") or {}
        occupied_total = db.query_one(f"SELECT COUNT(*) AS c FROM bd03_det d WHERE {_CURRENT_TENANT_SQL}") or {}
        building_total = db.query_one("SELECT COUNT(*) AS c FROM bd01") or {}

        room = int(room_total.get("c") or 0)
        occupied = int(occupied_total.get("c") or 0)
        stats.update(building_total=int(building_total.get("c") or 0), room_total=room,
                     occupied_total=occupied, vacant_total=max(0, room - occupied))
        if room:
            stats["occ_pct"] = round(occupied * 100 / room)
            stats["vac_pct"] = 100 - stats["occ_pct"]

        # 미수금 계산 - 단순화된 쿼리 + 인덱스 활용
        as_of = date.today().replace(day=monthrange(date.today().year, date.today().month)[1])
        misu_rows = db.query(f"""SELECT d.ipju_dt, d.rent_amt, d.manage_amt,
            COALESCE(SUM(s.su_sil_amt), 0) AS paid
          FROM bd03_det d
          LEFT JOIN sukum01 s ON s.bunji1=d.bunji1 AND s.bunji2=d.bunji2
            AND s.hosu_norm=d.hosu_norm AND s.ipju_seq=d.ipju_seq
            AND s.sukum_char='01' AND (s.del_yn IS NULL OR s.del_yn IN ('N',''))
            AND s.sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
          WHERE {_CURRENT_TENANT_SQL}
            AND (d.ipju_dt IS NULL OR d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY))
          GROUP BY d.bunji1, d.bunji2, d.hosu_norm, d.ipju_seq, d.ipju_dt, d.rent_amt, d.manage_amt""", [as_of.isoformat(), as_of.isoformat()])

        for row in misu_rows:
            if row.get("ipju_dt"):
                dt = row["ipju_dt"] if not isinstance(row["ipju_dt"], str) else datetime.strptime(row["ipju_dt"][:10], "%Y-%m-%d").date()
                if to_int_amt(row.get("rent_amt")) + to_int_amt(row.get("manage_amt")):
                    if max(0, (to_int_amt(row.get("rent_amt")) + to_int_amt(row.get("manage_amt"))) * months_elapsed(dt, as_of) - to_int_amt(row.get("paid"))) > 0:
                        stats["misu_total"] += 1
    except Exception as exc:
        app.logger.error("[Home Stats Error] %s", exc)
    return render_template("home.html", stats=stats)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        sabun = (request.form.get("sabun") or "").strip()
        password = (request.form.get("password") or "").strip()
        ip_address = request.remote_addr or "unknown"

        # 최근 실패 횟수 확인
        recent_failures = get_recent_failed_attempts(ip_address, sabun, LOGIN_LOCK_MINUTES)
        if recent_failures >= LOGIN_MAX_FAILURES:
            flash(f"로그인 시도 횟수를 초과했습니다. {LOGIN_LOCK_MINUTES}분 후 다시 시도하세요.", "err")
            return render_template("login.html")

        if not sabun or not password:
            flash("사번과 비밀번호를 모두 입력해주세요.", "err")
            return render_template("login.html")

        try:
            row = db.query_one(
                "SELECT sabun, s_name, grade, pass_wd FROM sawon_m WHERE sabun=%s",
                (sabun,),
            )

            if row:
                stored_password = (row.get("pass_wd") or "").strip()
                # 해시된 비밀번호인지 확인 (bcrypt 해시는 $2b$로 시작)
                if stored_password.startswith("$2b$"):
                    if verify_password(password, stored_password):
                        record_login_attempt(ip_address, sabun, True)
                        # 보안 이벤트 로깅
                        log_security_event(
                            'login_success',
                            user_id=sabun,
                            ip_address=ip_address,
                            details=f"User {sabun} logged in successfully"
                        )
                        session.clear()
                        session["sabun"] = row["sabun"]
                        session["s_name"] = row["s_name"]
                        session["grade"] = row["grade"]
                        session.permanent = True
                        return redirect(url_for("home"))
                else:
                    # 레거시 평문 비밀번호
                    if stored_password == password:
                        # 로그인 성공 시 해시로 업그레이드
                        try:
                            hashed = hash_password(password)
                            db.execute("UPDATE sawon_m SET pass_wd=%s WHERE sabun=%s", (hashed, sabun))
                        except Exception as e:
                            app.logger.warning(f"[Password Upgrade] 해시 업그레이드 실패: {e}")
                        record_login_attempt(ip_address, sabun, True)
                        # 보안 이벤트 로깅
                        log_security_event(
                            'login_success',
                            user_id=sabun,
                            ip_address=ip_address,
                            details=f"User {sabun} logged in (legacy password upgraded to bcrypt)"
                        )
                        session.clear()
                        session["sabun"] = row["sabun"]
                        session["s_name"] = row["s_name"]
                        session["grade"] = row["grade"]
                        session.permanent = True
                        return redirect(url_for("home"))

            # 로그인 실패 기록
            record_login_attempt(ip_address, sabun, False)
            # 보안 이벤트 로깅
            log_security_event(
                'login_failure',
                user_id=sabun,
                ip_address=ip_address,
                details=f"Failed login attempt for {sabun}"
            )
            remaining_attempts = LOGIN_MAX_FAILURES - recent_failures - 1
            if remaining_attempts <= 0:
                flash(f"로그인 {LOGIN_MAX_FAILURES}회 실패로 {LOGIN_LOCK_MINUTES}분간 로그인이 차단됩니다.", "err")
            else:
                flash(f"사번 또는 비밀번호가 올바르지 않습니다. 남은 시도 횟수: {remaining_attempts}", "err")
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


@app.errorhandler(404)
def not_found_error(error):
    """404 에러 핸들러"""
    app_logger.warning(f"[404] {request.method} {request.path} not found")
    return render_template('error.html', error_code=404, error_message="페이지를 찾을 수 없습니다."), 404


@app.errorhandler(500)
def internal_error(error):
    """500 에러 핸들러"""
    app_logger.error(f"[500] Internal server error on {request.method} {request.path}", exc_info=error)
    return render_template('error.html', error_code=500, error_message="서버 내부 오류가 발생했습니다."), 500


@app.errorhandler(403)
def forbidden_error(error):
    """403 에러 핸들러"""
    log_security_event(
        'permission_denied',
        user_id=session.get('sabun'),
        ip_address=request.remote_addr,
        details=f"Access denied to {request.path}"
    )
    return render_template('error.html', error_code=403, error_message="접근 권한이 없습니다."), 403


@app.errorhandler(400)
def bad_request_error(error):
    """400 에러 핸들러"""
    app_logger.warning(f"[400] Bad request on {request.method} {request.path}: {error}")
    return render_template('error.html', error_code=400, error_message="잘못된 요청입니다."), 400


if __name__ == "__main__":
    print("원룸 관리 웹: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
