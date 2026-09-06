"""성능 최적화: 느린 쿼리 분석 및 개선.

데이터베이스 쿼리 성능을 모니터링하고 최적화합니다.
"""
import time
import logging
from functools import wraps
from sqlalchemy import event
from sqlalchemy.engine import Engine


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 쿼리 성능 모니터링
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class QueryProfiler:
    """쿼리 성능 프로파일링 도구."""

    def __init__(self, slow_query_threshold_ms=100):
        """
        Args:
            slow_query_threshold_ms: 느린 쿼리 기준 (밀리초)
        """
        self.slow_query_threshold_ms = slow_query_threshold_ms
        self.queries = []

    def log_query(self, query, duration_ms):
        """쿼리 실행 시간 기록.

        Args:
            query: SQL 쿼리 문자열
            duration_ms: 실행 시간 (밀리초)
        """
        is_slow = duration_ms > self.slow_query_threshold_ms
        entry = {
            "query": query[:200],  # 처음 200자만
            "duration_ms": duration_ms,
            "is_slow": is_slow,
        }
        self.queries.append(entry)

        if is_slow:
            logger = logging.getLogger("oneroom.performance")
            logger.warning(
                f"Slow query: {duration_ms:.2f}ms\n{query[:500]}"
            )

    def get_summary(self):
        """성능 요약 반환.

        Returns:
            dict: 평균 시간, 느린 쿼리 수 등
        """
        if not self.queries:
            return {}

        total = sum(q["duration_ms"] for q in self.queries)
        slow_count = sum(1 for q in self.queries if q["is_slow"])

        return {
            "total_queries": len(self.queries),
            "avg_duration_ms": total / len(self.queries),
            "total_duration_ms": total,
            "slow_query_count": slow_count,
            "max_duration_ms": max(q["duration_ms"] for q in self.queries),
        }


profiler = QueryProfiler(slow_query_threshold_ms=100)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 성능 최적화 제안
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def detect_n_plus_one_queries(queries):
    """N+1 쿼리 문제 감지.

    Args:
        queries: 쿼리 리스트

    Returns:
        list: 의심되는 N+1 쿼리 정보
    """
    query_patterns = {}
    for q in queries:
        # SQL 정규화 (상수 제거)
        normalized = q["query"].split("WHERE")[0] if "WHERE" in q["query"] else q["query"]
        if normalized not in query_patterns:
            query_patterns[normalized] = 0
        query_patterns[normalized] += 1

    # 5회 이상 반복되는 쿼리 = N+1 의심
    return [pattern for pattern, count in query_patterns.items() if count > 5]


def suggest_indexes(queries):
    """인덱스 추가 제안.

    Args:
        queries: 쿼리 리스트

    Returns:
        list: 제안되는 인덱스 (예: "bd03_det(bunji1, bunji2)")
    """
    # 느린 쿼리에서 WHERE/JOIN 조건 분석
    suggestions = []
    
    # 실제 구현: 쿼리 파싱 → 인덱스 추천
    # 현재는 샘플 제안만 반환
    slow_queries = [q for q in queries if q["is_slow"]]
    
    if slow_queries:
        # bd03_det: bunji1, bunji2 조합 많이 사용
        suggestions.append("bd03_det(bunji1, bunji2)")
        # sukum01: 날짜 범위 조회 많음
        suggestions.append("sukum01(sukum_dt)")
        # bd03_det: 현세입자 조회 많음
        suggestions.append("bd03_det(out_dt)")
    
    return suggestions


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데코레이터: 함수 실행 시간 측정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def measure_performance(func):
    """함수 실행 시간 측정 데코레이터.

    Usage:
        @measure_performance
        def some_function():
            ...
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        duration_ms = (time.time() - start) * 1000

        logger = logging.getLogger("oneroom.performance")
        if duration_ms > 100:  # 느린 함수 (100ms 이상)
            logger.warning(f"{func.__name__}: {duration_ms:.2f}ms")
        else:
            logger.debug(f"{func.__name__}: {duration_ms:.2f}ms")

        return result

    return wrapper


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 성능 개선 팁
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
## 느린 쿼리 최적화 체크리스트

1️⃣  SELECT 최소화
   Bad:  SELECT * FROM bd03_det
   Good: SELECT bunji1, bunji2, hosu, ipju_nm FROM bd03_det

2️⃣  JOIN 최적화
   Bad:  LEFT JOIN에서 필터링 (LEFT JOIN 후 WHERE)
   Good: INNER JOIN 또는 사전 필터링

3️⃣  서브쿼리 → JOIN
   Bad:  WHERE bunji1 IN (SELECT bunji1 FROM bd01 WHERE ...)
   Good: JOIN bd01 ON ...

4️⃣  인덱스 활용
   CREATE INDEX idx_bd03_det_bunji ON bd03_det(bunji1, bunji2);
   CREATE INDEX idx_sukum01_dt ON sukum01(sukum_dt);
   CREATE INDEX idx_bd03_det_out ON bd03_det(out_dt);

5️⃣  페이징 + LIMIT
   Bad:  모든 행 조회 후 애플리케이션에서 필터
   Good: DB에서 LIMIT/OFFSET 사용

6️⃣  EXPLAIN 분석
   EXPLAIN SELECT ... FROM bd03_det WHERE ...
   → key 없음 = 풀 테이블 스캔 = 인덱스 필요

7️⃣  N+1 쿼리 제거
   Bad:  for tenant in tenants: query_tenant_details(tenant.id)
   Good: JOIN으로 한 번에 조회
"""
