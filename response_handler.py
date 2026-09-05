"""응답 표준화 핸들러.

JSON API 응답과 웹 페이지 응답을 일관되게 처리합니다.
"""
from flask import jsonify, render_template, redirect, url_for, flash

from exceptions import OneRoomException


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# JSON 응답 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class ApiResponse:
    """표준 JSON API 응답."""

    @staticmethod
    def success(data=None, message="성공", status_code=200):
        """성공 응답.

        Args:
            data: 응답 데이터 (dict, list, 또는 None)
            message: 메시지
            status_code: HTTP 상태 코드 (기본값: 200)

        Returns:
            tuple: (response, status_code)
        """
        response = {
            "success": True,
            "message": message,
        }
        if data is not None:
            response["data"] = data
        return jsonify(response), status_code

    @staticmethod
    def created(data=None, message="생성됨"):
        """리소스 생성 성공 (201).

        Args:
            data: 생성된 리소스 정보

        Returns:
            tuple: (response, 201)
        """
        return ApiResponse.success(data, message, status_code=201)

    @staticmethod
    def no_content():
        """204 No Content."""
        return "", 204

    @staticmethod
    def error(message="오류 발생", error_code=None, status_code=400, details=None):
        """에러 응답.

        Args:
            message: 에러 메시지
            error_code: 에러 코드 (문자열, 선택사항)
            status_code: HTTP 상태 코드
            details: 추가 상세정보 (dict)

        Returns:
            tuple: (response, status_code)
        """
        response = {
            "success": False,
            "message": message,
        }
        if error_code:
            response["error_code"] = error_code
        if details:
            response["details"] = details
        return jsonify(response), status_code

    @staticmethod
    def validation_error(message, field=None, status_code=422):
        """검증 에러 (422).

        Args:
            message: 에러 메시지
            field: 필드명 (선택사항)
            status_code: HTTP 상태 코드

        Returns:
            tuple: (response, status_code)
        """
        details = {}
        if field:
            details["field"] = field
        return ApiResponse.error(message, error_code="VALIDATION_ERROR", status_code=status_code, details=details)

    @staticmethod
    def not_found(message="찾을 수 없음", resource_type=None):
        """404 Not Found.

        Args:
            message: 에러 메시지
            resource_type: 리소스 타입 (선택사항)

        Returns:
            tuple: (response, 404)
        """
        details = {}
        if resource_type:
            details["resource_type"] = resource_type
        return ApiResponse.error(message, error_code="NOT_FOUND", status_code=404, details=details)

    @staticmethod
    def conflict(message="충돌", conflict_type=None):
        """409 Conflict.

        Args:
            message: 에러 메시지
            conflict_type: 충돌 타입 (선택사항)

        Returns:
            tuple: (response, 409)
        """
        details = {}
        if conflict_type:
            details["conflict_type"] = conflict_type
        return ApiResponse.error(message, error_code="CONFLICT", status_code=409, details=details)

    @staticmethod
    def from_exception(exc):
        """Exception에서 JSON 응답 생성.

        Args:
            exc: OneRoomException 인스턴스 (또는 일반 Exception)

        Returns:
            tuple: (response, status_code)
        """
        if isinstance(exc, OneRoomException):
            return ApiResponse.error(
                message=exc.user_message,
                error_code=exc.__class__.__name__,
                status_code=exc.http_status,
                details=exc.details if exc.details else None,
            )
        else:
            # 일반 Exception
            return ApiResponse.error(
                message=str(exc) or "알 수 없는 오류",
                error_code="INTERNAL_ERROR",
                status_code=500,
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 웹 페이지 응답 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class PageResponse:
    """웹 페이지 응답 래퍼."""

    @staticmethod
    def render(template_name, **context):
        """템플릿 렌더.

        Args:
            template_name: 템플릿 파일명
            **context: 템플릿 변수

        Returns:
            str: 렌더된 HTML
        """
        return render_template(template_name, **context)

    @staticmethod
    def redirect_with_message(endpoint, message, category="ok", **kwargs):
        """리다이렉트 + 플래시 메시지.

        Args:
            endpoint: 리다이렉트 대상 엔드포인트
            message: 플래시 메시지
            category: 메시지 카테고리 ('ok', 'err', 'warn', 'info')
            **kwargs: url_for 인자

        Returns:
            Response: 리다이렉트 응답
        """
        flash(message, category)
        return redirect(url_for(endpoint, **kwargs))

    @staticmethod
    def redirect_back_with_message(message, category="ok"):
        """이전 페이지로 리다이렉트 + 메시지.

        Args:
            message: 플래시 메시지
            category: 메시지 카테고리

        Returns:
            Response: 리다이렉트 응답
        """
        flash(message, category)
        return redirect("/")  # 기본값: 홈으로 (실제로는 HTTP Referer 사용 가능)

    @staticmethod
    def error_page(message, status_code=400, template="error.html", **context):
        """에러 페이지 렌더.

        Args:
            message: 에러 메시지
            status_code: HTTP 상태 코드
            template: 에러 템플릿 파일명
            **context: 추가 템플릿 변수

        Returns:
            tuple: (HTML, status_code)
        """
        context["error_message"] = message
        context["status_code"] = status_code
        return render_template(template, **context), status_code

    @staticmethod
    def from_exception(exc, redirect_endpoint=None):
        """Exception에서 웹 응답 생성.

        Args:
            exc: OneRoomException 인스턴스 (또는 일반 Exception)
            redirect_endpoint: 리다이렉트 대상 엔드포인트 (None이면 에러 페이지)

        Returns:
            Response 또는 tuple: 웹 페이지 응답
        """
        if isinstance(exc, OneRoomException):
            message = exc.user_message
            status_code = exc.http_status
        else:
            message = str(exc) or "알 수 없는 오류"
            status_code = 500

        if redirect_endpoint:
            return PageResponse.redirect_with_message(
                redirect_endpoint, message, category="err"
            )
        else:
            return PageResponse.error_page(message, status_code=status_code)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 통합 응답 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class Response:
    """요청 타입에 따라 자동으로 API/웹 응답을 선택."""

    @staticmethod
    def is_json_request(request):
        """JSON 요청 여부 판단.

        Args:
            request: Flask request 객체

        Returns:
            bool: JSON 요청이면 True
        """
        # 1. X-Requested-With: XMLHttpRequest
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return True
        # 2. Accept 헤더
        if "application/json" in request.headers.get("Accept", ""):
            return True
        # 3. 요청 경로가 /api/로 시작
        if request.path.startswith("/api/"):
            return True
        return False

    @staticmethod
    def success(request, data=None, message="성공", endpoint=None, **redirect_kwargs):
        """자동 응답: 성공.

        Args:
            request: Flask request 객체
            data: 응답 데이터 (JSON) 또는 context (웹)
            message: 메시지
            endpoint: 리다이렉트 엔드포인트 (웹 페이지일 때)
            **redirect_kwargs: url_for 인자

        Returns:
            Response: JSON 또는 리다이렉트
        """
        if Response.is_json_request(request):
            return ApiResponse.success(data, message)
        else:
            if endpoint:
                return PageResponse.redirect_with_message(endpoint, message, **redirect_kwargs)
            else:
                return PageResponse.render("success.html", message=message)

    @staticmethod
    def error(request, message="오류 발생", status_code=400, redirect_endpoint=None):
        """자동 응답: 에러.

        Args:
            request: Flask request 객체
            message: 메시지
            status_code: HTTP 상태 코드
            redirect_endpoint: 리다이렉트 엔드포인트 (웹 페이지일 때)

        Returns:
            Response: JSON 또는 에러 페이지/리다이렉트
        """
        if Response.is_json_request(request):
            return ApiResponse.error(message, status_code=status_code)
        else:
            return PageResponse.error_page(message, status_code=status_code)

    @staticmethod
    def from_exception(request, exc, redirect_endpoint=None):
        """Exception에서 자동 응답 생성.

        Args:
            request: Flask request 객체
            exc: Exception 인스턴스
            redirect_endpoint: 리다이렉트 엔드포인트 (웹 페이지일 때)

        Returns:
            Response: JSON 또는 웹 응답
        """
        if Response.is_json_request(request):
            return ApiResponse.from_exception(exc)
        else:
            return PageResponse.from_exception(exc, redirect_endpoint=redirect_endpoint)
