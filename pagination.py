"""페이지네이션 최적화.

커서 기반 페이징 및 오프셋 기반 페이징의 성능 최적화.
"""
from typing import Optional, Dict, List, Any


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 오프셋 기반 페이징 (기존)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class OffsetPaginator:
    """오프셋 기반 페이징.

    문제점:
    - 데이터 삽입/삭제 시 페이지가 엇갈릴 수 있음
    - 큰 오프셋 값에서는 성능 저하
    - 정렬 안 함시 일관성 없음

    장점:
    - 사용자가 페이지 번호로 이동 가능
    - 구현 간단

    사용: 수정이 적은 마스터 데이터 (건물, 코드 등)
    """

    def __init__(self, per_page: int = 20):
        self.per_page = per_page

    def paginate(self, query: str, count_query: str, args: tuple, page: int = 1) -> Dict[str, Any]:
        """페이징 실행.

        Args:
            query: 데이터 조회 SQL (LIMIT, OFFSET 미포함)
            count_query: 전체 행 수 조회 SQL
            args: SQL 매개변수
            page: 페이지 번호 (1부터 시작)

        Returns:
            dict: {
                "items": [...],
                "page": 현재 페이지,
                "per_page": 한 페이지 항목 수,
                "total": 전체 행 수,
                "pages": 전체 페이지 수,
                "has_next": 다음 페이지 여부,
                "has_prev": 이전 페이지 여부
            }
        """
        import db

        # 전체 행 수
        count_result = db.query_one(count_query, args)
        total = int(count_result.get("c") or 0)

        # 계산
        pages = (total + self.per_page - 1) // self.per_page
        page = max(1, min(page, pages)) if pages > 0 else 1
        offset = (page - 1) * self.per_page

        # 데이터 조회 (LIMIT, OFFSET 추가)
        paginated_query = f"{query} LIMIT %s OFFSET %s"
        items = db.query(paginated_query, args + (self.per_page, offset))

        return {
            "items": items,
            "page": page,
            "per_page": self.per_page,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1,
            "offset": offset,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 커서 기반 페이징 (최적화)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class CursorPaginator:
    """커서 기반 페이징.

    원리:
    - 마지막 행의 ID/값을 "커서"로 사용
    - WHERE id > cursor로 다음 행 조회
    - 정렬 필수, 추가 가능한 열 필요

    장점:
    - 데이터 삽입/삭제 영향 최소
    - 대용량 데이터셋에서 성능 우수 (인덱스 활용)
    - 오프셋 계산 불필요

    단점:
    - "처음"/"끝" 페이지로의 임의 접근 불가
    - 구현 복잡

    사용: 수정이 많은 데이터 (수금, 입금 기록 등)
    """

    def __init__(self, per_page: int = 20, cursor_column: str = "id"):
        """
        Args:
            per_page: 한 페이지 항목 수
            cursor_column: 커서로 사용할 열명 (정렬된 고유 값)
        """
        self.per_page = per_page
        self.cursor_column = cursor_column

    def paginate(
        self,
        query: str,
        args: tuple,
        cursor: Optional[str] = None,
        direction: str = "next"
    ) -> Dict[str, Any]:
        """페이징 실행.

        Args:
            query: 기본 SQL (ORDER BY 포함해야 함)
            args: SQL 매개변수
            cursor: 커서 값 (다음 페이지를 위한 마지막 행의 cursor_column 값)
            direction: "next" 또는 "prev"

        Returns:
            dict: {
                "items": [...],
                "next_cursor": 다음 페이지 커서,
                "has_next": 다음 페이지 여부
            }
        """
        import db

        # 커서 기반 WHERE 절 추가
        if cursor:
            if direction == "next":
                cursor_clause = f"AND {self.cursor_column} > %s"
            else:  # prev
                cursor_clause = f"AND {self.cursor_column} < %s"
                # 역순 정렬 (나중에 다시 뒤집음)
                query = query.replace("ORDER BY", "ORDER BY") + " DESC"
            query = query.replace("FROM", f"FROM ") + f" {cursor_clause}"
            args = args + (cursor,)

        # per_page + 1개 조회 (has_next 판단용)
        fetch_query = f"{query} LIMIT %s"
        items = db.query(fetch_query, args + (self.per_page + 1,))

        # has_next 판단
        has_next = len(items) > self.per_page
        if has_next:
            items = items[:-1]  # 마지막 행 제거

        # 역순 방향인 경우 다시 뒤집기
        if direction == "prev" and items:
            items.reverse()

        # 다음 커서값
        next_cursor = None
        if has_next and items:
            next_cursor = str(items[-1].get(self.cursor_column))

        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_next": has_next,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 페이지네이션 선택 기준
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
## 페이지네이션 선택 기준

### 오프셋 기반 (OffsetPaginator)
✅ 건물 목록 (변경 적음)
✅ 호실 목록 (변경 적음)
✅ 사용자 목록
✅ 권한 관리

### 커서 기반 (CursorPaginator)
✅ 수금 기록 (시간순 조회)
✅ 입금 기록 (날짜순)
✅ 정산 기록 (월별)
✅ 활동 로그

## 사용 예시

### 오프셋 (건물 목록)
from pagination import OffsetPaginator

paginator = OffsetPaginator(per_page=20)
page = request.args.get("page", 1, type=int)

result = paginator.paginate(
    query="SELECT * FROM bd01 WHERE ...",
    count_query="SELECT COUNT(*) AS c FROM bd01 WHERE ...",
    args=(...,),
    page=page
)

return render_template(
    "buildings.html",
    buildings=result["items"],
    pagination=result
)

### 커서 (수금 기록)
from pagination import CursorPaginator

paginator = CursorPaginator(per_page=20, cursor_column="sukum_seq")
cursor = request.args.get("cursor")

result = paginator.paginate(
    query="SELECT * FROM sukum01 WHERE ... ORDER BY sukum_dt DESC, sukum_seq DESC",
    args=(...,),
    cursor=cursor,
    direction="next"
)

return render_template(
    "payments.html",
    payments=result["items"],
    next_cursor=result["next_cursor"],
    has_next=result["has_next"]
)
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 성능 최적화 팁
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
1️⃣  ORDER BY 인덱스 확인
   CREATE INDEX idx_sukum01_dt_seq ON sukum01(sukum_dt DESC, sukum_seq DESC);

2️⃣  커서 값 인덱싱
   CREATE INDEX idx_payment_seq ON sukum01(sukum_seq);

3️⃣  COUNT(*) 최적화
   - 정확한 수가 필요 없으면 approximate count 사용
   - 큰 테이블의 COUNT는 비용이 높음

4️⃣  페이지 크기 조정
   - 기본 20개는 대부분의 경우 적절
   - 리스트가 매우 크면 50-100 고려
   - 상세 조회는 작게 (5-10)

5️⃣  클라이언트 캐싱
   - "이전/다음" 버튼은 캐시 활용
   - cursor 값 저장 (뒤로 가기 시)
"""
