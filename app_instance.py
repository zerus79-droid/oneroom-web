"""Flask 앱 인스턴스 생성.

app.py 와 화면 모듈(building/tenants/payments 등)이 같은 Flask `app` 객체를
공유해야 하는데, 서로가 서로를 import하면 순환 참조(circular import)가
생깁니다. 그래서 `app` 객체 생성만 이 작은 파일에 따로 두고, 다른
모든 모듈은 여기서 `app`을 가져다 씁니다.
"""
import os
from flask import Flask

import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# 프로덕션 모드 감지
IS_PRODUCTION = os.getenv('FLASK_ENV') == 'production'

# 세션 보안 설정
app.config.update(
    SESSION_COOKIE_SECURE=IS_PRODUCTION,  # 프로덕션에서는 HTTPS 강제
    SESSION_COOKIE_HTTPONLY=True,         # JavaScript 접근 차단
    SESSION_COOKIE_SAMESITE='Strict',     # CSRF 방지 (Strict로 강화)
    PERMANENT_SESSION_LIFETIME=3600,      # 세션 만료 1시간
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 파일 업로드 최대 10MB
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 레이트 제한 및 캐싱 통합
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

try:
    from rate_limiter import create_limiter
    limiter = create_limiter(app)
except Exception as e:
    print(f"[Warning] Rate limiter not initialized: {e}")
    limiter = None

try:
    from query_cache import create_cache
    cache = create_cache(app)
except Exception as e:
    print(f"[Warning] Cache not initialized: {e}")
    cache = None

# API 문서화 (선택사항)
try:
    from api_docs import create_api
    api = create_api(app)
except Exception as e:
    print(f"[Warning] API docs not initialized: {e}")
    api = None
