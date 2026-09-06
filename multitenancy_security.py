"""멀티테넌시 보안: 건물 접근 제어 강화.

사용자가 접근 가능한 건물만 조회/수정할 수 있도록 제한.
"""
from functools import wraps
from flask import session

import db
from exceptions import BuildingAccessDeniedError


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 사용자 권한 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def get_user_buildings(sabun):
    """사용자가 접근 가능한 건물 목록 조회.

    Args:
        sabun: 사번 (사용자 ID)

    Returns:
        list: 건물 (bunji1, bunji2) 리스트
    """
    # 권한 등급 확인
    user = db.query_one(
        "SELECT grade FROM sawon_m WHERE sabun=%s",
        (sabun,)
    )
    if not user:
        return []

    grade = user.get("grade", "C")

    # U(무제한) 또는 A(최고관리자) = 모든 건물 접근
    if grade in ("U", "A"):
        buildings = db.query("SELECT DISTINCT bunji1, bunji2 FROM bd01 ORDER BY bunji1, bunji2")
        return [(b["bunji1"], b["bunji2"]) for b in buildings]

    # B(일반관리자) = 배정된 건물만
    # (현재 세션에서 building_access 테이블이 없으면 모든 건물 허용)
    try:
        access = db.query(
            "SELECT bunji1, bunji2 FROM building_access WHERE sabun=%s",
            (sabun,)
        )
        return [(a["bunji1"], a["bunji2"]) for a in access]
    except:
        # 테이블 없음 = 기본 모든 건물 허용
        buildings = db.query("SELECT DISTINCT bunji1, bunji2 FROM bd01 ORDER BY bunji1, bunji2")
        return [(b["bunji1"], b["bunji2"]) for b in buildings]


def check_building_access(sabun, bunji1, bunji2):
    """사용자의 건물 접근 권한 확인.

    Args:
        sabun: 사번
        bunji1: 번지1
        bunji2: 번지2

    Returns:
        bool: 접근 가능하면 True

    Raises:
        BuildingAccessDeniedError: 접근 거부
    """
    user_buildings = get_user_buildings(sabun)

    # (bunji1, bunji2) 쌍이 권한 목록에 있는지 확인
    if (str(bunji1), str(bunji2)) in user_buildings:
        return True
    
    # 권한 없음
    raise BuildingAccessDeniedError(
        f"건물 접근 거부: {bunji1}-{bunji2}",
        user_message=f"이 건물에 대한 접근 권한이 없습니다.",
        building=(bunji1, bunji2)
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데코레이터: 건물 접근 제어
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def require_building_access(param_names=("bunji1", "bunji2")):
    """건물 접근 권한 검증 데코레이터.

    Usage:
        @app.route("/buildings/<bunji1>/<bunji2>")
        @require_building_access()
        def view_building(bunji1, bunji2):
            ...

        @app.route("/api/tenants")
        @require_building_access(param_names=("bunji1", "bunji2"))
        def api_list_tenants():
            # request.args에서 bunji1, bunji2 추출
            ...

    Args:
        param_names: 경로/쿼리에서 추출할 매개변수명
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            sabun = session.get("sabun")
            if not sabun:
                raise BuildingAccessDeniedError("로그인 필요")

            # 경로 매개변수에서 bunji1, bunji2 추출
            bunji1 = kwargs.get(param_names[0]) or args[0] if args else None
            bunji2 = kwargs.get(param_names[1]) or args[1] if len(args) > 1 else None

            # 또는 request.args에서 추출
            if not (bunji1 and bunji2):
                from flask import request
                bunji1 = bunji1 or request.args.get(param_names[0])
                bunji2 = bunji2 or request.args.get(param_names[1])

            if bunji1 and bunji2:
                check_building_access(sabun, bunji1, bunji2)

            return func(*args, **kwargs)
        return wrapper
    return decorator


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 쿼리 필터: 결과에서 접근 불가 건물 제거
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def filter_buildings_by_access(rows, sabun):
    """조회 결과에서 접근 가능한 건물만 필터링.

    Args:
        rows: 건물 정보 리스트 (bunji1, bunji2 포함)
        sabun: 사번

    Returns:
        list: 필터링된 행
    """
    user_buildings = set(get_user_buildings(sabun))
    return [r for r in rows if (str(r.get("bunji1")), str(r.get("bunji2"))) in user_buildings]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 사용 예시 및 모범 사례
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
## 멀티테넌시 보안 적용 패턴

### 1. 단일 건물 조회 (경로 매개변수)
@app.route("/buildings/<bunji1>/<bunji2>")
@require_building_access()
def view_building(bunji1, bunji2):
    # 자동으로 접근 권한 확인
    return {"building": {...}}

### 2. 목록 조회 (쿼리 매개변수)
@app.route("/tenants")
@login_required
def list_tenants():
    sabun = session.get("sabun")
    bunji1 = request.args.get("bunji1")
    bunji2 = request.args.get("bunji2")
    
    # 요청한 건물이 있으면 확인
    if bunji1 and bunji2:
        check_building_access(sabun, bunji1, bunji2)
    
    # 아니면 사용자의 모든 건물 조회
    buildings = get_user_buildings(sabun)
    return render_template("tenants.html", buildings=buildings)

### 3. 일괄 조회 + 필터링
@app.route("/api/buildings")
@login_required
def api_list_buildings():
    sabun = session.get("sabun")
    all_buildings = db.query("SELECT * FROM bd01 ORDER BY bunji1, bunji2")
    
    # 사용자가 접근 가능한 건물만
    filtered = filter_buildings_by_access(all_buildings, sabun)
    return ApiResponse.success(data=filtered)

### 4. 등록/수정 (권한 필수)
@app.route("/buildings/<bunji1>/<bunji2>", methods=["POST"])
@require_building_access()
@require_write_access
def update_building(bunji1, bunji2):
    # 이미 접근 권한 확인됨
    return ApiResponse.success(message="저장 완료")

## 권한 등급별 동작
- U (무제한): 모든 건물 접근
- A (최고관리자): 모든 건물 접근
- B (일반관리자): building_access 테이블에서 배정된 건물만
- C (조회전용): 조회만, 수정 불가 (@require_write_access로도 막힘)
"""
