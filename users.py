"""사용자관리·비밀번호변경 화면.

기초 내역 관리 메뉴의 사용자(sawon_m) 등록/수정/삭제와
본인 비밀번호 변경 라우트를 모아둔 모듈입니다.
"""
import re

from flask import flash, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import login_required, require_admin

_SABUN_RE = re.compile(r"^[0-9A-Za-z가-힣]+$")


# 사용자 등급 (XP 사용자관리: A / B / C / 무제한)
GRADE_OPTIONS = [
    ("A", "최고관리자"),
    ("B", "일반관리자"),
    ("C", "조회전용"),
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


def _normalize_user_grade(raw):
    grade = (raw or "B").strip().upper()
    if grade not in ("A", "B", "C", "U"):
        return "B"
    return grade


def _extract_user_form(form):
    return {
        "sabun": (form.get("sabun") or "").strip(),
        "s_name": (form.get("s_name") or "").strip(),
        "grade": _normalize_user_grade(form.get("grade")),
        "password": (form.get("password") or "").strip(),
        "password2": (form.get("password2") or "").strip(),
        "mode": (form.get("mode") or "new").strip(),
        "orig_sabun": (form.get("orig_sabun") or "").strip(),
    }


def _validate_user_form(data, *, require_password):
    if not data["sabun"]:
        return "사용자 ID(사번)를 입력하세요."
    if len(data["sabun"]) > 5:
        return "사용자 ID는 5자 이내입니다."
    if not _SABUN_RE.match(data["sabun"]):
        return "사용자 ID는 숫자·영문·한글만 입력하세요 (특수문자 불가)."
    if not data["s_name"]:
        return "사용자명을 입력하세요."
    if data["password"] and len(data["password"]) > 10:
        return "비밀번호는 10자 이내입니다."
    if require_password and not data["password"]:
        return "신규 사용자는 비밀번호를 입력하세요."
    if data["password"] or data["password2"]:
        if data["password"] != data["password2"]:
            return "비밀번호와 확인이 일치하지 않습니다."
    return None


@app.route("/users", methods=["GET", "POST"])
@login_required
@require_admin
def users():
    """기초 내역 · 사용자관리 (sawon_m). XP「사용자」화면."""
    uid = session.get("sabun") or ""

    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        if action == "new":
            return redirect(url_for("users"))

        form = _extract_user_form(request.form)
        if action == "delete":
            if form.get("mode") != "edit" or not form.get("orig_sabun"):
                flash("삭제할 사용자를 목록에서 선택한 뒤 삭제하세요.", "err")
                return _render_users_page(form)
            target = form["orig_sabun"]
            if target == uid:
                flash("현재 로그인 중인 계정은 삭제할 수 없습니다.", "err")
                return _render_users_page(form)
            try:
                n = db.execute("DELETE FROM sawon_m WHERE sabun=%s", (target,))
                if n:
                    flash(f"삭제했습니다. ({target})", "ok")
                else:
                    flash("삭제할 사용자를 찾지 못했습니다.", "err")
            except Exception as e:
                flash(f"삭제 실패: {e}", "err")
            return redirect(url_for("users"))

        err = _validate_user_form(
            form,
            require_password=(form["mode"] != "edit" or not form["orig_sabun"]),
        )
        if err:
            form["password"] = ""
            form["password2"] = ""
            flash(err, "err")
            return _render_users_page(form)

        try:
            if form["mode"] == "edit" and form["orig_sabun"]:
                if form["password"]:
                    db.execute(
                        """
                        UPDATE sawon_m
                        SET sabun=%s, s_name=%s, grade=%s, pass_wd=%s, sys_dt=NOW()
                        WHERE sabun=%s
                        """,
                        (form["sabun"], form["s_name"][:50], form["grade"], form["password"][:10], form["orig_sabun"]),
                    )
                else:
                    db.execute(
                        """
                        UPDATE sawon_m
                        SET sabun=%s, s_name=%s, grade=%s, sys_dt=NOW()
                        WHERE sabun=%s
                        """,
                        (form["sabun"], form["s_name"][:50], form["grade"], form["orig_sabun"]),
                    )
                if form["orig_sabun"] == uid or form["sabun"] == uid:
                    session["sabun"] = form["sabun"]
                    session["s_name"] = form["s_name"][:50]
                    session["grade"] = form["grade"]
                flash("수정 저장했습니다.", "ok")
            else:
                exists = db.query_one(
                    "SELECT 1 AS x FROM sawon_m WHERE sabun=%s", (form["sabun"],)
                )
                if exists:
                    flash("이미 등록된 사용자 ID입니다.", "err")
                    return redirect(url_for("users"))
                db.execute(
                    """
                    INSERT INTO sawon_m (sabun, s_name, grade, pass_wd, sys_dt)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (form["sabun"], form["s_name"][:50], form["grade"], form["password"][:10]),
                )
                flash(f"등록했습니다. ({form['sabun']})", "ok")
        except Exception as e:
            flash(f"저장 실패: {e}", "err")
        return redirect(url_for("users"))

    # GET
    form = _empty_user_form()
    edit_sabun = (request.args.get("sabun") or "").strip()
    # sabun 파라미터가 없으면 현재 로그인한 사용자를 기본으로 선택
    if not edit_sabun:
        edit_sabun = (session.get("sabun") or "").strip()
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
                "password2": "",
                "orig_sabun": row.get("sabun") or "",
            }

    return _render_users_page(form)


def _user_rows():
    rows = db.query(
        """
        SELECT sabun, s_name, grade, sys_dt
        FROM sawon_m
        ORDER BY sabun
        """
    )
    for r in rows:
        r["grade_label"] = _grade_label(r.get("grade"))
    return rows


def _render_users_page(form):
    return render_template(
        "users.html",
        form=form,
        users=_user_rows(),
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
            return render_template(
                "password.html",
                sabun=row.get("sabun") or sabun,
                s_name=row.get("s_name") or s_name,
            )
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
