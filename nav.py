"""상단 메뉴(네비게이션) 표시용 로직.

현재 요청이 어느 화면(endpoint)에 해당하는지 보고, 상단 메뉴에서
어떤 대메뉴·하위 화면명을 강조해서 보여줄지 계산합니다.
"""
from flask import request


def nav_context():
    """상단 메뉴: 현재 페이지가 속한 대메뉴·하위 메뉴 표시용."""
    ep = (request.endpoint or "").strip()
    # endpoint → (section_id, 대메뉴명, 현재 화면명)
    # section_id: base | tenant | month | checkout | repair | payment | home
    table = {
        "home": ("home", "주택관리 시스템", "홈"),
        "buildings": ("base", "기초 내역 관리", "건물 내역 조회"),
        "vacancies": ("base", "기초 내역 관리", "공실 현황 조회"),
        "users": ("base", "기초 내역 관리", "사용자관리"),
        "password_change": ("base", "기초 내역 관리", "비밀번호변경"),
        "building_detail": ("base", "기초 내역 관리", "건물 정보"),
        "building_rooms": ("base", "기초 내역 관리", "호수 내역 조회"),
        "building_new": ("base", "기초 내역 관리", "건물 등록"),
        "building_edit": ("base", "기초 내역 관리", "건물 수정"),
        "room_new": ("base", "기초 내역 관리", "호수 등록"),
        "search": ("tenant", "입주자관리", "입주자 이력 조회"),
        "tenant_manage": ("tenant", "입주자관리", "입주자 이력 관리"),
        "jungsan": ("month", "월정기보고", "주소별 정산서 작성"),
        "jungsan_print": ("month", "월정기보고", "결산현황 인쇄"),
        "jungsan_list": ("month", "월정기보고", "월별 정산서 조회"),
        "checkout": ("checkout", "퇴실 정산 관리", "퇴실 정산 관리"),
        "checkout_list": ("checkout", "퇴실 정산 관리", "퇴실(예정)자 조회"),
        "checkout_print": ("checkout", "퇴실 정산 관리", "계약 해지 인쇄"),
        "repairs": ("repair", "수리", "수리내역조회"),
        "repair_new": ("repair", "수리", "수리내역등록"),
        "payment_new": ("payment", "수금관리", "수금(대체) 등록"),
        "payments": ("payment", "수금관리", "기간별 수금(대체) 현황"),
        "jungke": ("payment", "수금관리", "중개수수료등록"),
        "misu": ("payment", "수금관리", "미수금 현황 조회"),
        "docs": ("help", "도움말", "서식 및 자료"),
    }
    # buildings?next=rooms 는 같은 endpoint — 화면명만 보정
    sec = table.get(ep)
    if not sec:
        # 접두 추정
        if ep.startswith("building") or ep.startswith("room"):
            sec = ("base", "기초 내역 관리", "기초 내역")
        elif ep.startswith("tenant"):
            sec = ("tenant", "입주자관리", "입주자관리")
        elif ep.startswith("jungsan"):
            sec = ("month", "월정기보고", "월정기보고")
        elif ep.startswith("checkout"):
            sec = ("checkout", "퇴실 정산 관리", "퇴실 정산")
        elif ep.startswith("repair"):
            sec = ("repair", "수리", "수리")
        elif ep.startswith("payment") or ep in ("jungke", "misu"):
            sec = ("payment", "수금관리", "수금관리")
        elif ep == "home":
            sec = ("home", "주택관리 시스템", "홈")
        else:
            sec = (None, None, None)
    section_id, section_label, page_label = sec
    if ep == "buildings" and (request.args.get("next") or "") == "rooms":
        page_label = "호수 내역 조회"
    return {
        "nav_section": section_id,
        "nav_section_label": section_label,
        "nav_page_label": page_label,
        "nav_endpoint": ep,
    }
