import pymysql
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB
import config

# DB 연결 풀 생성 (최대 5개 연결 유지)
_pool = PooledDB(
    creator=pymysql,
    maxconnections=5,
    mincached=2,
    maxcached=2,
    maxshared=0,
    blocking=True,
    host=config.DB_HOST,
    port=config.DB_PORT,
    user=config.DB_USER,
    password=config.DB_PASSWORD,
    database=config.DB_NAME,
    charset="utf8mb4",
    cursorclass=DictCursor,
    autocommit=True,
)


def get_conn():
    return _pool.connection()


def query(sql, args=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchall()
    finally:
        conn.close()


def query_one(sql, args=None):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql, args=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.rowcount
    finally:
        conn.close()
