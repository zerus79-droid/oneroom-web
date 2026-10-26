"""사용자별 담당 건물 접근 제한.

건물 키는 기존 DB와 동일하게 bunji1/bunji2 쌍을 사용한다.
"""
from flask import abort, has_request_context, request, session

import db
from app_instance import app


def ensure_table():
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS sawon_building (
            sabun VARCHAR(5) NOT NULL,
            bunji1 VARCHAR(4) NOT NULL,
            bunji2 VARCHAR(4) NOT NULL,
            PRIMARY KEY (sabun, bunji1, bunji2),
            INDEX ix_sawon_building_bunji (bunji1, bunji2)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def assigned_buildings(sabun=None):
    if not has_request_context() and not sabun:
        return set()
    uid = (sabun if sabun is not None else session.get("sabun") or "").strip()
    if not uid:
        return set()
    rows = db.query(
        "SELECT bunji1, bunji2 FROM sawon_building WHERE sabun=%s",
        (uid,),
        apply_building_access=False,
    )
    return {(str(r["bunji1"]), str(r["bunji2"])) for r in rows}


def unrestricted_user():
    return (session.get("grade") or "").strip().upper() in {"U", "A"}


def building_allowed(bunji1, bunji2):
    """현재 사용자가 해당 건물을 다룰 권한이 있는지 서버에서 직접 확인한다."""
    if not has_request_context() or not session.get("sabun"):
        return False
    if unrestricted_user():
        return True
    from utils import pad_bunji

    return (pad_bunji(bunji1), pad_bunji(bunji2)) in assigned_buildings()


def scoped_cache_key(prefix):
    """사용자 권한 범위와 전체 쿼리스트링을 포함한 캐시 키."""
    scope = "all" if unrestricted_user() else (session.get("sabun") or "").strip()
    return f"{prefix}:{scope}:{request.full_path}"


def _refresh_session_user():
    """세션의 등급/이름을 DB 기준으로 갱신한다. 삭제된 계정이면 세션을 비운다."""
    uid = (session.get("sabun") or "").strip()
    rows = db.query(
        "SELECT s_name, grade FROM sawon_m WHERE sabun=%s",
        (uid,),
        apply_building_access=False,
    )
    if not rows:
        session.clear()
        return False
    session["grade"] = rows[0]["grade"]
    session["s_name"] = rows[0]["s_name"]
    return True


def filter_query_rows(rows):
    """건물 키가 포함된 조회 결과에서 담당 외 건물을 제거한다."""
    if not has_request_context() or not session.get("sabun") or unrestricted_user():
        return rows
    if request.endpoint == "users":
        return rows
    allowed = assigned_buildings()
    filtered = []
    for row in rows:
        if "bunji1" not in row or "bunji2" not in row:
            filtered.append(row)
            continue
        key = (str(row.get("bunji1") or ""), str(row.get("bunji2") or ""))
        if key in allowed:
            filtered.append(row)
    return filtered


@app.before_request
def block_unassigned_building_request():
    """URL이나 폼으로 다른 건물을 직접 지정하는 우회를 차단한다."""
    if not session.get("sabun") or request.endpoint in {"login", "logout", "static"}:
        return None
    if not _refresh_session_user():
        return None
    if unrestricted_user() or request.endpoint == "users":
        return None
    from utils import pad_bunji

    values = request.view_args or {}
    pairs = [
        (
            values.get("bunji1") or request.values.get("bunji1") or request.values.get("q_bunji1"),
            values.get("bunji2") or request.values.get("bunji2") or request.values.get("q_bunji2"),
        ),
        (request.values.get("orig_bunji1"), request.values.get("orig_bunji2")),
    ]
    allowed = None
    for b1, b2 in pairs:
        if not b1 or not b2:
            continue
        if allowed is None:
            allowed = assigned_buildings()
        if (pad_bunji(b1), pad_bunji(b2)) not in allowed:
            abort(403)
    return None


ensure_table()
