"""마이그레이션 SQL 파일을 실행하는 스크립트.

oneroom-web 폴더 안에 이 파일을 넣고, 그 폴더에서 실행하세요.
config.py 에 있는 DB 접속정보를 그대로 재사용합니다 (새로 입력할 필요 없음).

사용법 (VS Code 터미널에서, oneroom-web 폴더 위치):
    C:\\Users\\someb\\AppData\\Local\\Programs\\Python\\Python312\\python.exe run_migration.py migrations/001_search_speed_indexes.sql
    C:\\Users\\someb\\AppData\\Local\\Programs\\Python\\Python312\\python.exe run_migration.py migrations/002_sukum01_indexes.sql
"""
import sys
import re

import pymysql
import config


def split_statements(sql_text: str):
    # 주석(-- ...) 제거 후 세미콜론 기준으로 문장 분리
    lines = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    statements = [s.strip() for s in cleaned.split(";")]
    return [s for s in statements if s]


def main():
    if len(sys.argv) != 2:
        print("사용법: python run_migration.py <sql파일경로>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        sql_text = f.read()

    statements = split_statements(sql_text)
    if not statements:
        print("실행할 SQL 문장이 없습니다 (파일 내용 확인 필요).")
        sys.exit(1)

    conn = pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            for i, stmt in enumerate(statements, 1):
                print(f"[{i}/{len(statements)}] 실행 중...")
                print(stmt[:120].replace("\n", " ") + ("..." if len(stmt) > 120 else ""))
                try:
                    cur.execute(stmt)
                    print("  -> 성공")
                except pymysql.err.OperationalError as e:
                    # 이미 컬럼/인덱스가 있으면 에러 코드 1060(중복 컬럼)/1061(중복 키)
                    if e.args and e.args[0] in (1060, 1061):
                        print(f"  -> 이미 적용되어 있어 건너뜀 ({e.args[1]})")
                    else:
                        raise
        conn.commit()
        print("\n완료. SHOW INDEX FROM <테이블명>; 으로 확인해보세요.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
