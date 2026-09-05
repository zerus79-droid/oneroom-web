"""비즈니스 로직 커스텀 Exception.

일반적인 HTTP 에러와 비즈니스 로직 에러를 분리하여 관리합니다.
각 예외는 자동으로 적절한 HTTP 상태 코드와 사용자 메시지를 제공합니다.
"""


class OneRoomException(Exception):
    """모든 비즈니스 로직 예외의 기본 클래스."""

    http_status = 400
    user_message = "요청 처리 중 오류가 발생했습니다."

    def __init__(self, message=None, http_status=None, user_message=None, **details):
        """
        Args:
            message: 로그 메시지 (개발자용)
            http_status: HTTP 상태 코드 (기본값: 클래스 기본값)
            user_message: 사용자 표시 메시지 (기본값: 클래스 기본값)
            **details: 추가 정보 (보안 로깅용)
        """
        super().__init__(message or self.user_message)
        self.message = message or self.user_message
        self.http_status = http_status or self.__class__.http_status
        self.user_message = user_message or self.__class__.user_message
        self.details = details


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 입력 검증 예외 (422 Unprocessable Entity)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ValidationError(OneRoomException):
    """입력값 검증 실패."""

    http_status = 422
    user_message = "입력값이 올바르지 않습니다."


class MissingRequiredFieldError(ValidationError):
    """필수 필드 누락."""

    user_message = "필수 입력 필드가 누락되었습니다."


class InvalidFormatError(ValidationError):
    """형식 오류 (번지, 전화번호, 주민번호 등)."""

    user_message = "입력 형식이 올바르지 않습니다."


class InvalidMoneyError(ValidationError):
    """금액 형식 오류."""

    user_message = "금액이 올바르지 않습니다."


class InvalidDateError(ValidationError):
    """날짜 형식 오류."""

    user_message = "날짜가 올바르지 않습니다."


class DuplicateRecordError(ValidationError):
    """중복 레코드."""

    user_message = "이미 존재하는 데이터입니다."


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데이터 상태 예외 (409 Conflict)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ConflictError(OneRoomException):
    """데이터 상태 충돌."""

    http_status = 409
    user_message = "현재 상태에서는 이 작업을 수행할 수 없습니다."


class TenantAlreadyOutError(ConflictError):
    """퇴실 완료된 입주자."""

    user_message = "이미 퇴실한 입주자는 수정할 수 없습니다."


class TenantNotFoundError(ConflictError):
    """입주자 정보 없음."""

    user_message = "입주자 정보를 찾을 수 없습니다."


class PaymentAlreadyDeletedError(ConflictError):
    """삭제된 수금."""

    user_message = "이미 삭제된 수금입니다."


class ContractTermsConflictError(ConflictError):
    """계약 조건 변경 불가."""

    user_message = "계약 조건을 변경할 수 없습니다."


class SettlementAlreadyPostedError(ConflictError):
    """이미 확정된 정산."""

    user_message = "이미 확정된 정산은 수정할 수 없습니다."


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 리소스 찾기 실패 (404 Not Found)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class NotFoundError(OneRoomException):
    """리소스 없음."""

    http_status = 404
    user_message = "요청한 정보를 찾을 수 없습니다."


class BuildingNotFoundError(NotFoundError):
    """건물 없음."""

    user_message = "해당 건물 정보를 찾을 수 없습니다."


class RoomNotFoundError(NotFoundError):
    """호실 없음."""

    user_message = "해당 호실 정보를 찾을 수 없습니다."


class PaymentNotFoundError(NotFoundError):
    """수금 없음."""

    user_message = "해당 수금 정보를 찾을 수 없습니다."


class UserNotFoundError(NotFoundError):
    """사용자 없음."""

    user_message = "해당 사용자를 찾을 수 없습니다."


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 권한/인증 예외 (403 Forbidden, 401 Unauthorized)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class AuthorizationError(OneRoomException):
    """접근 권한 없음."""

    http_status = 403
    user_message = "이 작업을 수행할 권한이 없습니다."


class InsufficientPermissionError(AuthorizationError):
    """권한 부족 (C 등급)."""

    user_message = "이 작업은 관리자 권한이 필요합니다."


class BuildingAccessDeniedError(AuthorizationError):
    """건물 접근 거부 (권한 관리)."""

    user_message = "이 건물에 대한 접근 권한이 없습니다."


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 파일 업로드 예외 (400 Bad Request)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class FileUploadError(OneRoomException):
    """파일 업로드 오류."""

    http_status = 400
    user_message = "파일 업로드에 실패했습니다."


class FileSizeError(FileUploadError):
    """파일 크기 초과 (10MB)."""

    user_message = "파일 크기가 너무 큽니다 (최대 10MB)."


class InvalidFileExtensionError(FileUploadError):
    """파일 확장자 오류."""

    user_message = "지원하지 않는 파일 형식입니다."


class FileContentError(FileUploadError):
    """파일 내용 오류 (읽기 실패)."""

    user_message = "파일을 읽을 수 없습니다."


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데이터베이스 예외 (500 Internal Server Error)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class DatabaseError(OneRoomException):
    """데이터베이스 작업 실패."""

    http_status = 500
    user_message = "데이터 처리 중 오류가 발생했습니다."


class DatabaseConnectionError(DatabaseError):
    """DB 연결 실패."""

    user_message = "데이터베이스에 연결할 수 없습니다."


class DatabaseIntegrityError(DatabaseError):
    """데이터 무결성 오류."""

    user_message = "데이터 저장 중 오류가 발생했습니다."


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 비즈니스 로직 예외 (500 Internal Server Error)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class BusinessLogicError(OneRoomException):
    """비즈니스 로직 오류."""

    http_status = 500
    user_message = "요청 처리 중 오류가 발생했습니다."


class InvalidCalculationError(BusinessLogicError):
    """정산/계산 오류."""

    user_message = "정산 계산 중 오류가 발생했습니다."


class PaymentCalculationError(BusinessLogicError):
    """수금 계산 오류."""

    user_message = "수금 계산 중 오류가 발생했습니다."


class SettlementCalculationError(BusinessLogicError):
    """월정산 계산 오류."""

    user_message = "정산 계산 중 오류가 발생했습니다."
