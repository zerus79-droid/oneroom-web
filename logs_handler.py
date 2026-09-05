"""구조화된 로깅 핸들러 및 보안 이벤트 추적.

일반 애플리케이션 로그, 보안 이벤트(로그인 실패, 권한 거부 등),
액세스 로그를 체계적으로 분류하고 기록합니다.
"""
import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler


class StructuredFormatter(logging.Formatter):
    """JSON 구조화된 로깅 포맷터."""
    
    def format(self, record):
        """로그 레코드를 JSON으로 변환."""
        log_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        
        # 예외 정보 포함
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # 커스텀 필드 추가 (extra 딕셔너리에서)
        if hasattr(record, 'user_id'):
            log_data['user_id'] = record.user_id
        if hasattr(record, 'ip_address'):
            log_data['ip_address'] = record.ip_address
        if hasattr(record, 'endpoint'):
            log_data['endpoint'] = record.endpoint
        if hasattr(record, 'status_code'):
            log_data['status_code'] = record.status_code
        if hasattr(record, 'security_event'):
            log_data['security_event'] = record.security_event
        
        return json.dumps(log_data, ensure_ascii=False, default=str)


def setup_logging():
    """애플리케이션 로깅 초기화."""
    
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    # 일반 애플리케이션 로그
    app_logger = logging.getLogger('oneroom.app')
    app_logger.setLevel(logging.DEBUG)
    
    app_file_handler = RotatingFileHandler(
        'logs/app.log',
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    app_file_handler.setLevel(logging.DEBUG)
    app_file_handler.setFormatter(StructuredFormatter())
    app_logger.addHandler(app_file_handler)
    
    # 보안 이벤트 로그 (로그인, 권한 거부 등)
    security_logger = logging.getLogger('oneroom.security')
    security_logger.setLevel(logging.INFO)
    
    security_file_handler = RotatingFileHandler(
        'logs/security.log',
        maxBytes=10 * 1024 * 1024,
        backupCount=5
    )
    security_file_handler.setLevel(logging.INFO)
    security_file_handler.setFormatter(StructuredFormatter())
    security_logger.addHandler(security_file_handler)
    
    # 액세스 로그 (HTTP 요청/응답)
    access_logger = logging.getLogger('oneroom.access')
    access_logger.setLevel(logging.INFO)
    
    access_file_handler = RotatingFileHandler(
        'logs/access.log',
        maxBytes=10 * 1024 * 1024,
        backupCount=5
    )
    access_file_handler.setLevel(logging.INFO)
    access_file_handler.setFormatter(StructuredFormatter())
    access_logger.addHandler(access_file_handler)
    
    # 콘솔 핸들러 (개발 환경용 - 구조화되지 않은 간단한 포맷)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    
    # 콘솔 핸들러는 모든 로거에 추가
    app_logger.addHandler(console_handler)
    security_logger.addHandler(console_handler)
    access_logger.addHandler(console_handler)
    
    return app_logger, security_logger, access_logger


# 전역 로거 인스턴스
app_logger, security_logger, access_logger = setup_logging()


def log_security_event(event_type, **kwargs):
    """보안 이벤트를 구조화된 형태로 기록.
    
    Args:
        event_type: 'login_success', 'login_failure', 'permission_denied', 
                    'csrf_violation', 'file_upload_invalid' 등
        **kwargs: user_id, ip_address, details 등 추가 정보
    """
    security_logger.info(
        f"[{event_type}] {kwargs.get('details', '')}",
        extra={
            'security_event': event_type,
            'user_id': kwargs.get('user_id'),
            'ip_address': kwargs.get('ip_address'),
            'details': kwargs.get('details'),
        }
    )


def log_access(ip_address, method, endpoint, status_code, user_id=None):
    """HTTP 요청/응답을 액세스 로그에 기록.
    
    Args:
        ip_address: 클라이언트 IP
        method: HTTP 메소드 (GET, POST 등)
        endpoint: 요청 경로
        status_code: HTTP 상태 코드
        user_id: 로그인한 사용자 ID (없으면 None)
    """
    access_logger.info(
        f"{method} {endpoint} - {status_code}",
        extra={
            'ip_address': ip_address,
            'endpoint': endpoint,
            'status_code': status_code,
            'user_id': user_id,
        }
    )
