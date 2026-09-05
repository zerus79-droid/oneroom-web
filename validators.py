"""입력값 검증 표준화.

폼 필드, 숫자, 날짜, 금액, 번지 등 공통 검증을 중앙화합니다.
"""
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from exceptions import (
    InvalidDateError,
    InvalidFormatError,
    InvalidMoneyError,
    MissingRequiredFieldError,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 필수 필드 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def require_field(data, field_name, field_label=None):
    """필수 필드 검증.

    Args:
        data: dict (request.form 등)
        field_name: 필드 키
        field_label: 사용자 표시 레이블 (기본값: field_name)

    Returns:
        str: 검증된 값 (stripped)

    Raises:
        MissingRequiredFieldError
    """
    value = (data.get(field_name) or "").strip()
    if not value:
        label = field_label or field_name
        raise MissingRequiredFieldError(
            f"필수 필드 누락: {label}",
            user_message=f"{label}을(를) 입력하세요.",
            field=field_name,
        )
    return value


def optional_field(data, field_name, default=""):
    """선택 필드 검증 (기본값 반환)."""
    return (data.get(field_name) or default).strip()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 날짜 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def validate_date(date_str, field_label="날짜"):
    """날짜 형식 검증.

    Args:
        date_str: 'YYYY-MM-DD' 형식의 문자열
        field_label: 사용자 표시 레이블

    Returns:
        str: 'YYYY-MM-DD' 형식의 검증된 날짜

    Raises:
        InvalidDateError
    """
    date_str = (date_str or "").strip()
    if not date_str:
        raise InvalidDateError(
            f"{field_label}: 날짜 미지정",
            user_message=f"{field_label}을(를) 입력하세요.",
        )

    try:
        if len(date_str) == 10 and date_str[4] == "-" and date_str[7] == "-":
            d = datetime.strptime(date_str, "%Y-%m-%d")
            return d.strftime("%Y-%m-%d")
        else:
            raise ValueError("Invalid format")
    except (ValueError, TypeError) as e:
        raise InvalidDateError(
            f"{field_label}: 형식 오류 ({date_str})",
            user_message=f"{field_label} 형식이 올바르지 않습니다 (YYYY-MM-DD).",
        )


def validate_date_optional(date_str, field_label="날짜", allow_empty=True):
    """날짜 형식 검증 (선택사항).

    Args:
        date_str: 날짜 문자열 또는 빈 문자열
        field_label: 사용자 표시 레이블
        allow_empty: True면 빈 문자열 허용, False면 필수

    Returns:
        str: 'YYYY-MM-DD' 형식 또는 빈 문자열
    """
    date_str = (date_str or "").strip()
    if not date_str:
        if allow_empty:
            return ""
        else:
            raise InvalidDateError(
                f"{field_label}: 필수",
                user_message=f"{field_label}을(를) 입력하세요.",
            )
    return validate_date(date_str, field_label)


def validate_date_range(from_date, to_date, field_label="기간"):
    """날짜 범위 검증.

    Returns:
        tuple: (from_date, to_date)

    Raises:
        InvalidDateError
    """
    from_validated = validate_date(from_date, f"{field_label} 시작")
    to_validated = validate_date(to_date, f"{field_label} 종료")

    try:
        from_d = datetime.strptime(from_validated, "%Y-%m-%d").date()
        to_d = datetime.strptime(to_validated, "%Y-%m-%d").date()
        if from_d > to_d:
            raise InvalidDateError(
                f"{field_label}: 범위 오류",
                user_message=f"시작일이 종료일보다 클 수 없습니다.",
            )
    except ValueError as e:
        raise InvalidDateError(
            f"{field_label}: 파싱 실패",
            user_message=f"{field_label}이 올바르지 않습니다.",
        )

    return from_validated, to_validated


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 금액 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def validate_money(value, field_label="금액", allow_zero=True, max_value=None):
    """금액 검증.

    Args:
        value: 금액 값 (str, int, Decimal)
        field_label: 사용자 표시 레이블
        allow_zero: True면 0 허용
        max_value: 최대값 (None이면 제한 없음)

    Returns:
        int: 정수 금액

    Raises:
        InvalidMoneyError
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_zero:
            return 0
        else:
            raise InvalidMoneyError(
                f"{field_label}: 필수",
                user_message=f"{field_label}을(를) 입력하세요.",
            )

    try:
        # 쉼표, 공백 제거
        clean_value = str(value).replace(",", "").replace(" ", "").strip()
        amount = int(Decimal(clean_value))

        if amount < 0:
            raise InvalidMoneyError(
                f"{field_label}: 음수 ({amount})",
                user_message=f"{field_label}은(는) 0 이상이어야 합니다.",
            )

        if not allow_zero and amount == 0:
            raise InvalidMoneyError(
                f"{field_label}: 0 불가",
                user_message=f"{field_label}은(는) 0보다 커야 합니다.",
            )

        if max_value is not None and amount > max_value:
            raise InvalidMoneyError(
                f"{field_label}: 초과 ({amount} > {max_value})",
                user_message=f"{field_label}이 최대값을 초과했습니다.",
            )

        return amount

    except (ValueError, TypeError, InvalidOperation) as e:
        raise InvalidMoneyError(
            f"{field_label}: 형식 오류 ({value})",
            user_message=f"{field_label} 형식이 올바르지 않습니다.",
        )


def validate_money_optional(value, field_label="금액", default=0):
    """금액 검증 (선택사항)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    return validate_money(value, field_label, allow_zero=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 번지 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def validate_bunji(bunji_str):
    """번지 검증 ('123-45' → ('0123', '0045')).

    Args:
        bunji_str: '123-45' 또는 '123' 또는 '45' 형식

    Returns:
        tuple: (bunji1, bunji2) - 4자리 패딩 또는 빈 문자열

    Raises:
        InvalidFormatError
    """
    bunji_str = (bunji_str or "").strip()
    if not bunji_str:
        raise InvalidFormatError(
            "번지: 미지정",
            user_message="번지를 입력하세요.",
        )

    parts = bunji_str.split("-")
    try:
        if len(parts) == 2:
            b1 = str(int(parts[0])).zfill(4)
            b2 = str(int(parts[1])).zfill(4)
        elif len(parts) == 1:
            num = int(bunji_str)
            if num < 1000:
                b1, b2 = str(num).zfill(4), ""
            else:
                b1, b2 = "", str(num).zfill(4)
        else:
            raise ValueError("Invalid format")
        return b1, b2
    except (ValueError, TypeError):
        raise InvalidFormatError(
            f"번지: 형식 오류 ({bunji_str})",
            user_message="번지 형식이 올바르지 않습니다 (예: 123-45).",
        )


def validate_hosu(hosu_str, field_label="호수"):
    """호수 검증 (숫자, 문자 혼합 가능).

    Returns:
        str: 검증된 호수 (uppercase)
    """
    hosu_str = (hosu_str or "").strip().upper()
    if not hosu_str:
        raise InvalidFormatError(
            f"{field_label}: 미지정",
            user_message=f"{field_label}을(를) 입력하세요.",
        )
    return hosu_str


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 주민번호 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def validate_jumin(jumin1, jumin2, field_label="주민번호"):
    """주민번호 검증.

    Args:
        jumin1: 앞 6자리 (yymmdd)
        jumin2: 뒤 7자리 (ggggggg)

    Returns:
        str: 전체 주민번호 (13자리) 또는 빈 문자열 (부분 입력)
    """
    j1 = re.sub(r"\D", "", (jumin1 or ""))[:6]
    j2 = re.sub(r"\D", "", (jumin2 or ""))[:7]

    if not j1 and not j2:
        return ""

    if len(j1) == 6 and len(j2) == 7:
        full = j1 + j2
        try:
            # 유효성 검사: 간단한 형식 체크
            if not (j1.isdigit() and j2.isdigit()):
                raise ValueError("Non-digit")
            return full
        except ValueError:
            raise InvalidFormatError(
                f"{field_label}: 형식 오류",
                user_message=f"{field_label} 형식이 올바르지 않습니다.",
            )

    # 부분 입력은 허용
    return (j1 + j2).rstrip() if (j1 or j2) else ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전화번호 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def validate_phone(phone_str, field_label="전화번호", allow_empty=True):
    """전화번호 검증 (숫자만 추출).

    Args:
        phone_str: 전화번호 (형식 상관없음)
        field_label: 사용자 표시 레이블
        allow_empty: True면 빈 문자열 허용

    Returns:
        str: 숫자만 추출한 전화번호 또는 빈 문자열
    """
    phone_str = re.sub(r"\D", "", (phone_str or ""))
    if not phone_str:
        if allow_empty:
            return ""
        else:
            raise InvalidFormatError(
                f"{field_label}: 필수",
                user_message=f"{field_label}을(를) 입력하세요.",
            )
    return phone_str


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 콤보박스 값 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def validate_choice(value, choices, field_label="옵션"):
    """콤보박스/선택지 검증.

    Args:
        value: 선택된 값
        choices: 허용된 값 리스트
        field_label: 사용자 표시 레이블

    Returns:
        str: 검증된 값

    Raises:
        InvalidFormatError
    """
    value = (value or "").strip()
    if not value:
        value = choices[0] if choices else ""

    if value not in choices:
        raise InvalidFormatError(
            f"{field_label}: 유효하지 않은 값 ({value})",
            user_message=f"{field_label}이 올바르지 않습니다.",
        )
    return value


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 복합 폼 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def validate_tenant_form(form_data):
    """입주 폼 통합 검증.

    Returns:
        dict: 검증된 데이터

    Raises:
        ValidationError (하위 클래스)
    """
    validated = {}

    # 필수: 번지, 호수
    b1, b2 = validate_bunji(form_data.get("bunji") or form_data.get("bunji1") or "")
    if not (b1 or b2):
        raise InvalidFormatError("번지 필수", user_message="번지를 입력하세요.")
    validated["bunji1"] = b1
    validated["bunji2"] = b2

    validated["hosu"] = validate_hosu(form_data.get("hosu"))

    # 입주 기본 정보
    validated["ipju_nm"] = require_field(form_data, "ipju_nm", "입주자명")
    validated["ipju_dt"] = validate_date(form_data.get("ipju_dt"), "입주일")
    validated["ipju_seq"] = optional_field(form_data, "ipju_seq")

    # 선택사항
    validated["ipju_tel1"] = validate_phone(form_data.get("ipju_tel1"), allow_empty=True)
    validated["ipju_tel2"] = validate_phone(form_data.get("ipju_tel2"), allow_empty=True)
    validated["ipju_tel3"] = validate_phone(form_data.get("ipju_tel3"), allow_empty=True)

    validated["jumin"] = validate_jumin(
        form_data.get("jumin1"), form_data.get("jumin2")
    )

    # 금액
    validated["bojung_amt"] = validate_money_optional(form_data.get("bojung_amt"))
    validated["rent_amt"] = validate_money_optional(form_data.get("rent_amt"))
    validated["manage_amt"] = validate_money_optional(form_data.get("manage_amt"))
    validated["yechi_amt"] = validate_money_optional(form_data.get("yechi_amt"))

    # 선택 콤보
    validated["lease_gb"] = validate_choice(
        form_data.get("lease_gb"), ["W", "J"], "계약형태"
    )
    validated["napbu_gb"] = validate_choice(
        form_data.get("napbu_gb"), ["A", "B"], "납부시기"
    )

    # 퇴실일 (선택사항)
    validated["out_dt"] = validate_date_optional(form_data.get("out_dt"), "퇴실일")

    return validated
