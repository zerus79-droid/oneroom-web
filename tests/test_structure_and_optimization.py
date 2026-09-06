"""테스트 커버리지: 단위 테스트 및 통합 테스트.

validators, exceptions, response_handler 모듈의 단위 테스트.
"""
import unittest
from datetime import datetime, date

from app_instance import app
from exceptions import (
    ValidationError,
    TenantNotFoundError,
    InvalidMoneyError,
    InvalidDateError,
    InvalidFormatError,
)
from validators import (
    validate_date,
    validate_money,
    validate_bunji,
    validate_hosu,
    validate_phone,
    validate_choice,
    validate_jumin,
)
from response_handler import ApiResponse, PageResponse, Response


class TestValidators(unittest.TestCase):
    """validators.py 단위 테스트."""

    def test_validate_date_success(self):
        """날짜 검증 성공."""
        result = validate_date("2026-09-03", "입주일")
        self.assertEqual(result, "2026-09-03")

    def test_validate_date_invalid_format(self):
        """날짜 검증 실패 (형식 오류)."""
        with self.assertRaises(InvalidDateError):
            validate_date("2026/09/03", "입주일")

    def test_validate_date_empty(self):
        """날짜 검증 실패 (필수)."""
        with self.assertRaises(InvalidDateError):
            validate_date("", "입주일")

    def test_validate_money_success(self):
        """금액 검증 성공."""
        self.assertEqual(validate_money("1000"), 1000)
        self.assertEqual(validate_money("1,000"), 1000)
        self.assertEqual(validate_money("1 000"), 1000)

    def test_validate_money_invalid(self):
        """금액 검증 실패."""
        with self.assertRaises(InvalidMoneyError):
            validate_money("ABC", "금액")

    def test_validate_money_negative(self):
        """금액 검증 실패 (음수)."""
        with self.assertRaises(InvalidMoneyError):
            validate_money("-1000", "금액")

    def test_validate_bunji_success(self):
        """번지 검증 성공."""
        b1, b2 = validate_bunji("123-45")
        self.assertEqual(b1, "0123")
        self.assertEqual(b2, "0045")

    def test_validate_bunji_single(self):
        """번지 검증 성공 (한 개)."""
        b1, b2 = validate_bunji("123")
        self.assertEqual(b1, "0123")
        self.assertEqual(b2, "")

    def test_validate_bunji_empty(self):
        """번지 검증 실패 (필수)."""
        with self.assertRaises(InvalidFormatError):
            validate_bunji("")

    def test_validate_hosu_success(self):
        """호수 검증 성공."""
        self.assertEqual(validate_hosu("102"), "102")
        self.assertEqual(validate_hosu("a101"), "A101")

    def test_validate_phone_success(self):
        """전화번호 검증 성공."""
        self.assertEqual(validate_phone("010-1234-5678"), "01012345678")
        self.assertEqual(validate_phone("02-123-4567"), "0212345567")

    def test_validate_phone_empty(self):
        """전화번호 검증 (빈 값 허용)."""
        self.assertEqual(validate_phone(""), "")
        self.assertEqual(validate_phone("", allow_empty=True), "")

    def test_validate_choice_success(self):
        """선택지 검증 성공."""
        self.assertEqual(validate_choice("W", ["W", "B", "J"], "계약형태"), "W")

    def test_validate_choice_invalid(self):
        """선택지 검증 실패."""
        with self.assertRaises(InvalidFormatError):
            validate_choice("X", ["W", "B", "J"], "계약형태")

    def test_validate_jumin_success(self):
        """주민번호 검증 성공."""
        result = validate_jumin("900101", "1234567")
        self.assertEqual(result, "9001011234567")

    def test_validate_jumin_partial(self):
        """주민번호 검증 (부분 입력 허용)."""
        result = validate_jumin("900101", "")
        self.assertEqual(result, "900101")


class TestExceptions(unittest.TestCase):
    """exceptions.py 단위 테스트."""

    def test_validation_error_http_status(self):
        """ValidationError HTTP 상태 확인."""
        exc = ValidationError("필드 오류")
        self.assertEqual(exc.http_status, 422)

    def test_conflict_error_http_status(self):
        """ConflictError HTTP 상태 확인."""
        exc = TenantNotFoundError("입주자 없음")
        self.assertEqual(exc.http_status, 409)

    def test_exception_user_message(self):
        """Exception 사용자 메시지."""
        exc = TenantNotFoundError(
            "DB에서 없음",
            user_message="입주자를 찾을 수 없습니다."
        )
        self.assertEqual(exc.user_message, "입주자를 찾을 수 없습니다.")

    def test_exception_details(self):
        """Exception 상세정보."""
        exc = InvalidMoneyError("금액 형식", field="bojung_amt")
        self.assertEqual(exc.details["field"], "bojung_amt")


class TestResponseHandler(unittest.TestCase):
    """response_handler.py 단위 테스트."""

    def setUp(self):
        """테스트 시작."""
        self.app = app
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        """테스트 종료."""
        self.app_context.pop()

    def test_api_success_response(self):
        """API 성공 응답."""
        response, status = ApiResponse.success(
            data={"id": 1},
            message="성공"
        )
        self.assertEqual(status, 200)

    def test_api_created_response(self):
        """API 생성 응답 (201)."""
        response, status = ApiResponse.created(data={"id": 1})
        self.assertEqual(status, 201)

    def test_api_validation_error(self):
        """API 검증 에러 (422)."""
        response, status = ApiResponse.validation_error(
            "필드 누락",
            field="name"
        )
        self.assertEqual(status, 422)

    def test_api_not_found(self):
        """API 404 응답."""
        response, status = ApiResponse.not_found(
            "없음",
            resource_type="tenant"
        )
        self.assertEqual(status, 404)

    def test_api_conflict(self):
        """API 409 응답."""
        response, status = ApiResponse.conflict(
            "충돌",
            conflict_type="duplicate"
        )
        self.assertEqual(status, 409)

    def test_api_from_exception(self):
        """Exception에서 API 응답 생성."""
        exc = TenantNotFoundError("없음")
        response, status = ApiResponse.from_exception(exc)
        self.assertEqual(status, 409)


class TestIntegration(unittest.TestCase):
    """통합 테스트: 검증 → Exception → Response."""

    def setUp(self):
        """테스트 시작."""
        self.app = app
        self.app_context = self.app.app_context()
        self.app_context.push()

    def tearDown(self):
        """테스트 종료."""
        self.app_context.pop()

    def test_validation_to_response(self):
        """검증 실패 → Exception → API 응답."""
        try:
            validate_money("ABC", "금액")
        except InvalidMoneyError as e:
            response, status = ApiResponse.from_exception(e)
            self.assertEqual(status, 422)

    def test_tenant_not_found_to_response(self):
        """입주자 없음 → Exception → API 응답."""
        exc = TenantNotFoundError("입주자 없음")
        response, status = ApiResponse.from_exception(exc)
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
