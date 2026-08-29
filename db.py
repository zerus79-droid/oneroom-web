import pymysql
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB
import config
import time

# Retry logic for DB connection
_pool = None
max_retries = 30
retry_delay = 2

def _init_pool():
    global _pool
    for attempt in range(max_retries):
        try:
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
            print("Database connection pool initialized successfully")
            return
        except Exception as e:
            print(f"DB connection attempt {attempt+1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise Exception(f"Failed to connect to database after {max_retries} attempts")

_init_pool()


def get_conn():
    return _pool.connection()


def query(sql, args=None, *, apply_building_access=True):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            rows = cur.fetchall()
            if apply_building_access:
                try:
                    from building_access import filter_query_rows
                    rows = filter_query_rows(rows)
                except ImportError:
                    pass
            return rows
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
