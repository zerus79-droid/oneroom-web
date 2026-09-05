"""쿼리 캐싱 (Query Caching).

자주 조회되는 건물/호실/입주자 정보를 메모리에 캐싱합니다.
flask-caching을 사용한 TTL 기반 캐싱.
"""
from flask_caching import Cache


def create_cache(app):
    """캐시 객체 생성 및 설정.

    Args:
        app: Flask 앱 인스턴스

    Returns:
        Cache: 설정된 캐시 객체
    """
    cache = Cache(
        app,
        config={
            "CACHE_TYPE": "SimpleCache",  # 개발: 메모리, 프로덕션: redis 권장
            "CACHE_DEFAULT_TIMEOUT": 300,  # 기본 5분
        },
    )
    return cache


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 캐시 타임아웃 프리셋
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 변경 빈도가 낮은 데이터
CACHE_TIMEOUT_BUILDING = 600  # 건물 정보: 10분
CACHE_TIMEOUT_ROOM = 600  # 호실 정보: 10분
CACHE_TIMEOUT_CODE = 3600  # 코드 정보 (연/월/일 등): 1시간
CACHE_TIMEOUT_READONLY = 1800  # 읽기 전용 데이터: 30분

# 변경 빈도가 높은 데이터
CACHE_TIMEOUT_TENANT = 300  # 입주자 정보: 5분
CACHE_TIMEOUT_PAYMENT = 300  # 수금 정보: 5분
CACHE_TIMEOUT_LIST = 300  # 목록 조회: 5분


def cached_query(cache, timeout=None, key_prefix=None):
    """캐싱 데코레이터 래퍼.

    Usage:
        @app.route("/api/buildings")
        @cached_query(cache, timeout=CACHE_TIMEOUT_BUILDING, key_prefix="buildings")
        def api_list_buildings():
            return {"buildings": [...]}

    Args:
        cache: Cache 객체
        timeout: 캐시 타임아웃 (초)
        key_prefix: 캐시 키 접두어

    Returns:
        function: 데코레이터
    """
    def decorator(f):
        # timeout 기본값
        _timeout = timeout or CACHE_TIMEOUT_LIST
        # key_prefix 기본값 = 함수명
        _key_prefix = key_prefix or f.__name__

        def wrapper(*args, **kwargs):
            # 캐시 키 = prefix + 요청 매개변수
            from flask import request
            cache_key = f"{_key_prefix}:{request.query_string.decode()}"
            
            # 캐시 조회
            result = cache.get(cache_key)
            if result is not None:
                return result
            
            # 캐시 미스: 함수 실행
            result = f(*args, **kwargs)
            cache.set(cache_key, result, timeout=_timeout)
            return result
        
        return wrapper
    return decorator


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 캐시 무효화 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def invalidate_building_cache(cache, bunji1, bunji2=None):
    """건물 캐시 무효화.

    Args:
        cache: Cache 객체
        bunji1: 번지1
        bunji2: 번지2 (선택)
    """
    if bunji2:
        cache.delete(f"buildings:{bunji1}-{bunji2}")
    cache.delete("buildings:*")
    cache.delete("vacancies:*")


def invalidate_tenant_cache(cache, bunji1, bunji2, hosu):
    """입주자 캐시 무효화.

    Args:
        cache: Cache 객체
        bunji1: 번지1
        bunji2: 번지2
        hosu: 호수
    """
    cache.delete(f"tenants:{bunji1}-{bunji2}-{hosu}")
    cache.delete("tenants:*")


def invalidate_payment_cache(cache, bunji1, bunji2=None):
    """수금 캐시 무효화.

    Args:
        cache: Cache 객체
        bunji1: 번지1
        bunji2: 번지2 (선택)
    """
    cache.delete("payments:*")
    if bunji2:
        cache.delete(f"payments:{bunji1}-{bunji2}")


def invalidate_all_cache(cache):
    """전체 캐시 무효화."""
    cache.clear()
