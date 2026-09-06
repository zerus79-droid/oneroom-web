"""API 레이트 제한 (Rate Limiting).

flask-limiter를 사용한 엔드포인트별 요청 수 제한.
주요 엔드포인트 (로그인, API, 파일 업로드 등)에 적용.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


def create_limiter(app):
    """레이트 제한기 생성 및 설정.

    Args:
        app: Flask 앱 인스턴스

    Returns:
        Limiter: 설정된 레이트 제한기
    """
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"],  # 전역 기본값
        storage_uri="memory://",  # 개발: 메모리, 프로덕션: Redis 권장
    )
    return limiter


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 레이트 제한 프리셋 (엔드포인트별)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 로그인/인증 관련
RATE_LIMIT_LOGIN = "5 per 10 minutes"  # 로그인: 5회/10분 (보안)
RATE_LIMIT_AUTH = "10 per hour"  # 비밀번호 변경, 회원가입: 10회/시간

# API 엔드포인트
RATE_LIMIT_API_READ = "100 per minute"  # 조회: 100회/분
RATE_LIMIT_API_WRITE = "30 per minute"  # 등록/수정/삭제: 30회/분
RATE_LIMIT_API_EXPORT = "10 per hour"  # 내보내기: 10회/시간

# 파일 업로드
RATE_LIMIT_UPLOAD = "20 per hour"  # 파일 업로드: 20회/시간
RATE_LIMIT_IMPORT = "5 per hour"  # 일괄 임포트: 5회/시간

# 페이지 조회
RATE_LIMIT_PAGE = "200 per minute"  # 웹 페이지: 200회/분

# 검색
RATE_LIMIT_SEARCH = "60 per minute"  # 검색: 60회/분


def apply_rate_limits(limiter):
    """라우트별 레이트 제한 데코레이터 설정.

    현재는 app.py에서 수동으로 적용하는 방식을 사용하므로,
    이 함수는 참조용입니다. 향후 자동화 가능.

    Usage:
        @app.route("/login", methods=["POST"])
        @limiter.limit(RATE_LIMIT_LOGIN)
        def login():
            ...

        @app.route("/api/tenants", methods=["GET"])
        @limiter.limit(RATE_LIMIT_API_READ)
        def api_list_tenants():
            ...
    """
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 에러 핸들러 (레이트 제한 초과)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def handle_rate_limit_exceeded(e):
    """429 Too Many Requests 에러 핸들러.

    Args:
        e: RateLimitExceeded 예외

    Returns:
        tuple: (에러 응답, 상태 코드)
    """
    from response_handler import ApiResponse
    return ApiResponse.error(
        message="요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
        error_code="RATE_LIMIT_EXCEEDED",
        status_code=429,
        details={"retry_after": e.get_retry_after() if hasattr(e, 'get_retry_after') else None}
    )
