"""서식 및 자료 게시판 (도움말 메뉴).

법률 근거 메모, 서식 링크 등 나중에도 다시 찾아볼 참고자료를 등록해두는
간단한 게시판입니다. 조회는 로그인만 하면 누구나, 등록/수정/삭제는
관리자(U/A 등급)만 가능합니다.
"""
from flask import flash, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import fmt_date, login_required, make_pager as _make_pager


def _empty_doc_form():
    return {
        "mode": "new",
        "id": "",
        "title": "",
        "url": "",
        "content": "",
    }


def _extract_doc_form(form):
    return {
        "title": (form.get("title") or "").strip(),
        "url": (form.get("url") or "").strip(),
        "content": (form.get("content") or "").strip(),
        "mode": (form.get("mode") or "new").strip(),
        "orig_id": (form.get("orig_id") or "").strip(),
    }


def _is_admin():
    return (session.get("grade") or "").strip().upper() in ("U", "A")


@app.route("/docs", methods=["GET", "POST"])
@login_required
def docs():
    """서식 및 자료 게시판."""
    if request.method == "POST":
        if not _is_admin():
            flash("관리자 권한이 필요합니다.", "err")
            return redirect(url_for("docs"))

        action = (request.form.get("action") or "save").strip()
        if action == "new":
            return redirect(url_for("docs"))

        data = _extract_doc_form(request.form)
        if action == "delete":
            if data["mode"] != "edit" or not data["orig_id"]:
                flash("삭제할 자료를 목록에서 선택한 뒤 삭제하세요.", "err")
                return redirect(url_for("docs"))
            try:
                n = db.execute("DELETE FROM doc_board WHERE id=%s", (data["orig_id"],))
                flash("삭제했습니다." if n else "삭제할 자료를 찾지 못했습니다.", "ok" if n else "err")
            except Exception as e:
                flash(f"삭제 실패: {e}", "err")
            return redirect(url_for("docs"))

        if not data["title"]:
            flash("제목을 입력하세요.", "err")
            return redirect(url_for("docs"))
        if not data["url"] and not data["content"]:
            flash("링크나 내용 중 하나는 입력하세요.", "err")
            return redirect(url_for("docs"))

        writer = session.get("s_name") or session.get("sabun") or ""
        try:
            if data["mode"] == "edit" and data["orig_id"]:
                db.execute(
                    """
                    UPDATE doc_board
                    SET title=%s, url=%s, content=%s
                    WHERE id=%s
                    """,
                    (data["title"][:200], data["url"][:500], data["content"], data["orig_id"]),
                )
                flash("수정 저장했습니다.", "ok")
            else:
                db.execute(
                    """
                    INSERT INTO doc_board (title, url, content, writer)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (data["title"][:200], data["url"][:500], data["content"], writer[:50]),
                )
                flash("등록했습니다.", "ok")
        except Exception as e:
            flash(f"저장 실패: {e}", "err")
        return redirect(url_for("docs"))

    # GET
    form = _empty_doc_form()
    edit_id = (request.args.get("edit_id") or "").strip()
    if edit_id and _is_admin():
        row = db.query_one("SELECT * FROM doc_board WHERE id=%s", (edit_id,))
        if row:
            form = {
                "mode": "edit",
                "id": str(row.get("id") or ""),
                "title": row.get("title") or "",
                "url": row.get("url") or "",
                "content": row.get("content") or "",
            }

    total = (db.query_one("SELECT COUNT(*) AS c FROM doc_board") or {}).get("c") or 0
    pager = _make_pager(total)
    rows = db.query(
        """
        SELECT id, title, url, content, writer, created_at, updated_at
        FROM doc_board
        ORDER BY id DESC
        LIMIT %s OFFSET %s
        """,
        (pager["per_page"], pager["offset"]),
    )
    for r in rows:
        r["created_disp"] = fmt_date(r.get("created_at"))

    can_write = _is_admin()
    want_new = (request.args.get("new") or "").strip() == "1"
    show_form = can_write and (form["mode"] == "edit" or want_new)
    return render_template(
        "docs.html",
        form=form,
        can_write=can_write,
        show_form=show_form,
        rows=rows,
        pager=pager,
    )
