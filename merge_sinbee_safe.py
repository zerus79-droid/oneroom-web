"""Safely merge an old sinbee staging schema into the live schema.

The helper shipped with the old-data archive uses ``INSERT IGNORE`` directly.
That is unsafe for this application because several legacy tables use a
date/sequence primary key that is global (not per building), and MyISAM does
not roll back a transaction.  This module builds an explicit, read-only plan
first and only writes when ``--apply`` is supplied.

It intentionally does not merge ``sawon_m`` (passwords/users).  Shared
``gicho_code`` rows are left untouched; source-only codes are safe to add and
are included.  Existing building/master rows are never overwritten.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import decimal
import json
import os
import re
import sys
from typing import Any, Iterable

import pymysql
import pymysql.cursors

import config


TABLES = [
    "bd01",
    "bd03_m",
    "bd03_det",
    "bd05_suri",
    "bd07_out",
    "jungsan_m",
    "jungsan_det",
    "sukum01",
    "sunkub01",
    "sjungke01",
    "gicho_code",
]
REVIEW_TABLES = ("sawon_m",)
READ_TABLES = tuple(TABLES) + REVIEW_TABLES

KEYS = {
    "bd01": ("bunji1", "bunji2"),
    "bd03_m": ("bunji1", "bunji2", "hosu"),
    "bd03_det": ("bunji1", "bunji2", "hosu", "ipju_seq"),
    "bd05_suri": ("suri_dt", "suri_seq"),
    "bd07_out": ("out_dt", "out_seq"),
    "sukum01": ("sukum_dt", "bunji1", "bunji2", "hosu", "sukum_seq"),
    "sunkub01": ("sunkub_dt", "sunkub_seq"),
    "sjungke01": ("jungke_dt", "jungke_seq"),
    # Logical keys for the two legacy tables that have no physical key.
    "jungsan_m": ("jungsan_dt", "jungsan_seq", "bunji1", "bunji2"),
    "jungsan_det": (
        "jungsan_dt",
        "jungsan_seq",
        "bunji1",
        "bunji2",
        "hosu",
        "ipju_seq",
    ),
    "gicho_code": ("g_cd", "g_sub_cd"),
}

TENANT_REF_TABLES = ("bd05_suri", "bd07_out", "sukum01", "sunkub01", "jungsan_det")
AUDIT_COLUMNS = {"sys_dt", "uid"}
ZERO_DATE_PREFIX = "0000-00-00"

GLOBAL_SEQ = {
    "bd05_suri": ("suri_dt", "suri_seq", 2),
    "bd07_out": ("out_dt", "out_seq", 2),
    "sunkub01": ("sunkub_dt", "sunkub_seq", 2),
    "sjungke01": ("jungke_dt", "jungke_seq", 2),
}

GLOBAL_OWNER_COLUMNS = {
    # If the global date/sequence collision belongs to the same building, it
    # is the same legacy record with later corrections in the live DB.  Keep
    # live.  A different building is a genuine cross-database key collision
    # and needs a new sequence.
    "bd05_suri": ("bunji1", "bunji2"),
    "bd07_out": ("bunji1", "bunji2"),
    "sunkub01": ("bunji1", "bunji2"),
    "sjungke01": ("bunji1", "bunji2"),
}


def qi(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def connect(db: str | None = None, *, dict_cursor: bool = True):
    kwargs: dict[str, Any] = {
        "host": config.DB_HOST,
        "port": config.DB_PORT,
        "user": config.DB_USER,
        "password": config.DB_PASSWORD,
        "charset": "utf8mb4",
        "autocommit": True,
    }
    if db:
        kwargs["database"] = db
    if dict_cursor:
        kwargs["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**kwargs)


def table_columns(conn, schema: str, table: str, *, include_generated: bool = False) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COLUMN_NAME, EXTRA
                 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                ORDER BY ORDINAL_POSITION""",
            (schema, table),
        )
        rows = cur.fetchall()
    if include_generated:
        return [r["COLUMN_NAME"] for r in rows]
    return [
        r["COLUMN_NAME"]
        for r in rows
        if "GENERATED" not in (r.get("EXTRA") or "").upper()
    ]


def fetch_rows(conn, schema: str, table: str, columns: list[str]) -> list[dict[str, Any]]:
    if not columns:
        return []
    sql = "SELECT " + ",".join(qi(c) for c in columns) + " FROM " + qi(schema) + "." + qi(table)
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value).strip()


def cmp_value(column: str, value: Any) -> Any:
    """Return a stable comparison value across old and MariaDB types."""
    if value is None:
        return None
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, (dt.datetime, dt.date)):
        text = value.isoformat()
    else:
        text = value
    if isinstance(text, str):
        text = text.rstrip()
        if column.endswith("_dt") and (not text or text.startswith(ZERO_DATE_PREFIX)):
            # Old XP uses both NULL and the zero date for "not set".
            return None
        if column == "hosu":
            return text.strip().upper()
        return text
    return text


def key_value(column: str, value: Any) -> Any:
    value = cmp_value(column, value)
    if value is None:
        return None
    if column.endswith("_seq") or column in {"ipju_seq", "hosu"}:
        text = str(value).strip()
        if text.isdigit():
            return str(int(text))
        return text.upper()
    return value


def row_key(table: str, row: dict[str, Any], *, override: dict[str, Any] | None = None) -> tuple[Any, ...]:
    override = override or {}
    return tuple(
        key_value(c, override[c] if c in override else row.get(c)) for c in KEYS[table]
    )


def signature(row: dict[str, Any], columns: Iterable[str], *, exclude_key: bool = False, table: str = "") -> tuple[Any, ...]:
    key_cols = set(KEYS.get(table, ())) if exclude_key else set()
    return tuple(
        (c, cmp_value(c, row.get(c)))
        for c in columns
        if c not in AUDIT_COLUMNS and c not in key_cols
    )


def nonkey_signature(
    row: dict[str, Any],
    columns: Iterable[str],
    *,
    table: str,
    ignore_columns: Iterable[str] = (),
) -> tuple[Any, ...]:
    """Compare a row while ignoring only its physical/logical key columns.

    Unlike :func:`signature`, audit fields are retained here.  A remapped
    legacy row keeps its original ``sys_dt``/``uid`` values, so this signature
    lets a later run recognize that row even though its sequence was changed.
    """
    key_cols = set(KEYS.get(table, ())) | set(ignore_columns)
    return tuple(
        (c, cmp_value(c, row.get(c)))
        for c in columns
        if c not in key_cols
    )


def same_row(src: dict[str, Any], dst: dict[str, Any], columns: list[str], *, table: str = "") -> bool:
    # Include the key when comparing a complete row.  For collision decisions
    # the caller can use signature(..., exclude_key=True).
    return all(cmp_value(c, src.get(c)) == cmp_value(c, dst.get(c)) for c in columns if c not in AUDIT_COLUMNS)


def same_semantics(src: dict[str, Any], dst: dict[str, Any], columns: list[str], *, table: str = "") -> bool:
    return signature(src, columns, exclude_key=True, table=table) == signature(
        dst, columns, exclude_key=True, table=table
    )


def same_tenant_identity(src: dict[str, Any], dst: dict[str, Any]) -> bool:
    """Recognize the same tenant even when the live row has later corrections."""
    src_name = str(src.get("ipju_nm") or "").strip()
    dst_name = str(dst.get("ipju_nm") or "").strip()
    if not src_name or src_name != dst_name:
        return False
    src_jumin = str(src.get("ipju_jumin_no") or "").strip()
    dst_jumin = str(dst.get("ipju_jumin_no") or "").strip()
    if src_jumin and dst_jumin:
        return src_jumin == dst_jumin
    return cmp_value("ipju_dt", src.get("ipju_dt")) == cmp_value("ipju_dt", dst.get("ipju_dt"))


def group_map(rows: Iterable[dict[str, Any]], key_fn):
    result: dict[tuple[Any, ...], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        result[key_fn(row)].append(row)
    return result


def numeric_seq(value: Any) -> int | None:
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def allocate_seq(used: set[int], width: int) -> str:
    for number in range(1, 10**width):
        if number not in used:
            used.add(number)
            return f"{number:0{width}d}"
    raise RuntimeError(f"순번 공간이 부족합니다 (폭 {width})")


def make_tenant_map(src: list[dict[str, Any]], dst: list[dict[str, Any]], columns: list[str]):
    dst_by = group_map(dst, lambda r: row_key("bd03_det", r))
    all_src_by_room: dict[tuple[Any, ...], set[int]] = collections.defaultdict(set)
    used: dict[tuple[Any, ...], set[int]] = collections.defaultdict(set)
    for row in dst:
        room = row_key("bd03_m", row)
        n = numeric_seq(row.get("ipju_seq"))
        if n is not None:
            used[room].add(n)
    for row in src:
        room = row_key("bd03_m", row)
        n = numeric_seq(row.get("ipju_seq"))
        if n is not None:
            # Reserve every source number before allocating a new number so a
            # remapped row cannot collide with a source row processed later.
            all_src_by_room[room].add(n)
            used[room].add(n)

    mapping: dict[tuple[Any, ...], str] = {}
    insert_rows: list[dict[str, Any]] = []
    stats = collections.Counter()
    conflicts: list[tuple[Any, ...]] = []
    for row in src:
        old_key = row_key("bd03_det", row)
        candidates = dst_by.get(old_key, [])
        if candidates and any(same_tenant_identity(row, d) for d in candidates):
            mapping[old_key] = str(row.get("ipju_seq") or "").strip()
            stats["duplicate"] += 1
            continue
        if not candidates:
            mapping[old_key] = str(row.get("ipju_seq") or "").strip()
            insert_rows.append(dict(row))
            stats["insert"] += 1
            continue
        room = row_key("bd03_m", row)
        new_seq = allocate_seq(used[room], 2)
        mapping[old_key] = new_seq
        transformed = dict(row)
        transformed["ipju_seq"] = new_seq
        insert_rows.append(transformed)
        conflicts.append(old_key)
        stats["remapped"] += 1
    return mapping, insert_rows, stats, conflicts


def make_global_map(
    table: str,
    src: list[dict[str, Any]],
    dst: list[dict[str, Any]],
    columns: list[str],
    date_col: str,
    seq_col: str,
    width: int,
):
    dst_by = group_map(dst, lambda r: row_key(table, r))
    # A first run may have remapped a source sequence because another
    # building already owned the global (date, sequence) key.  Index rows by
    # their complete non-key contents so a subsequent run can find that
    # remapped row and avoid allocating a second sequence.
    remapped_index: dict[tuple[Any, ...], list[dict[str, Any]]] = collections.defaultdict(list)
    # The running application canonicalizes legacy brokerage descriptions to
    # ``중개보수`` and stores the room in its added ``hosu`` column.  Keep a
    # second, deliberately narrow index for that table so a later run still
    # recognizes rows after this normalizer has run.
    relaxed_index: dict[tuple[Any, ...], list[dict[str, Any]]] = collections.defaultdict(list)
    owner_cols = GLOBAL_OWNER_COLUMNS[table]
    for row in dst:
        scope = key_value(date_col, row.get(date_col))
        owner = tuple(key_value(c, row.get(c)) for c in owner_cols)
        remapped_index[(scope, owner, nonkey_signature(row, columns, table=table))].append(row)
        if table == "sjungke01":
            relaxed_index[
                (scope, owner, nonkey_signature(row, columns, table=table, ignore_columns={"jungke_desc"}))
            ].append(row)
    used: dict[Any, set[int]] = collections.defaultdict(set)
    for row in list(dst) + list(src):
        n = numeric_seq(row.get(seq_col))
        if n is not None:
            used[key_value(date_col, row.get(date_col))].add(n)
    mapping: dict[tuple[Any, ...], str] = {}
    insert_rows: list[dict[str, Any]] = []
    stats = collections.Counter()
    conflicts: list[tuple[Any, ...]] = []
    for row in src:
        old_key = row_key(table, row)
        scope = key_value(date_col, row.get(date_col))
        owner = tuple(key_value(c, row.get(c)) for c in owner_cols)
        existing_remap = remapped_index.get(
            (scope, owner, nonkey_signature(row, columns, table=table)), []
        )
        if existing_remap:
            # Prefer the lowest sequence if an old dump contains an exact
            # duplicate row more than once.  The source tables normally have
            # unique global keys, but this keeps the operation deterministic.
            chosen = min(
                existing_remap,
                key=lambda item: (numeric_seq(item.get(seq_col)) is None, numeric_seq(item.get(seq_col)) or 0, str(item.get(seq_col) or "")),
            )
            mapping[old_key] = str(chosen.get(seq_col) or "").strip()
            stats["duplicate"] += 1
            continue
        if table == "sjungke01":
            existing_relaxed = relaxed_index.get(
                (scope, owner, nonkey_signature(row, columns, table=table, ignore_columns={"jungke_desc"})),
                [],
            )
            if existing_relaxed:
                chosen = min(
                    existing_relaxed,
                    key=lambda item: (
                        numeric_seq(item.get(seq_col)) is None,
                        numeric_seq(item.get(seq_col)) or 0,
                        str(item.get(seq_col) or ""),
                    ),
                )
                mapping[old_key] = str(chosen.get(seq_col) or "").strip()
                stats["duplicate"] += 1
                continue
        candidates = dst_by.get(old_key, [])
        if candidates:
            same_owner = any(
                all(key_value(c, row.get(c)) == key_value(c, d.get(c)) for c in owner_cols)
                for d in candidates
            )
            if same_owner:
                mapping[old_key] = str(row.get(seq_col) or "").strip()
                if any(same_semantics(row, d, columns, table=table) for d in candidates):
                    stats["duplicate"] += 1
                else:
                    stats["conflict_kept_target"] += 1
                continue
        if not candidates:
            mapping[old_key] = str(row.get(seq_col) or "").strip()
            insert_rows.append(dict(row))
            stats["insert"] += 1
            continue
        new_seq = allocate_seq(used[scope], width)
        mapping[old_key] = new_seq
        transformed = dict(row)
        transformed[seq_col] = new_seq
        insert_rows.append(transformed)
        remapped_index[(scope, owner, nonkey_signature(transformed, columns, table=table))].append(transformed)
        if table == "sjungke01":
            relaxed_index[
                (scope, owner, nonkey_signature(transformed, columns, table=table, ignore_columns={"jungke_desc"}))
            ].append(transformed)
        conflicts.append(old_key)
        stats["remapped"] += 1
    return mapping, insert_rows, stats, conflicts


def make_payment_map(src, dst, columns):
    # The modern payment PK includes date/building/room/sequence.  A collision
    # is therefore the same payment from the two snapshots, not a global key
    # collision.  Keep the corrected live row and never double-count money.
    table = "sukum01"
    dst_by = group_map(dst, lambda r: row_key(table, r))
    mapping: dict[tuple[Any, ...], str] = {}
    insert_rows: list[dict[str, Any]] = []
    stats = collections.Counter()
    conflicts: list[tuple[Any, ...]] = []
    for row in src:
        old_key = row_key(table, row)
        candidates = dst_by.get(old_key, [])
        mapping[old_key] = str(row.get("sukum_seq") or "").strip()
        if candidates:
            if any(same_semantics(row, d, columns, table=table) for d in candidates):
                stats["duplicate"] += 1
            else:
                stats["conflict_kept_target"] += 1
                conflicts.append(old_key)
            continue
        insert_rows.append(dict(row))
        stats["insert"] += 1
    return mapping, insert_rows, stats, conflicts


def make_settlement_plan(src, dst, columns):
    """Keep the live snapshot for a colliding monthly settlement."""
    table = "jungsan_m"
    dst_keys = {row_key(table, row) for row in dst}
    mapping: dict[tuple[Any, ...], str] = {}
    rows: list[dict[str, Any]] = []
    skipped: set[tuple[Any, ...]] = set()
    stats = collections.Counter()
    for row in src:
        old_key = row_key(table, row)
        mapping[old_key] = str(row.get("jungsan_seq") or "").strip()
        if old_key in dst_keys:
            skipped.add(old_key)
            stats["conflict_kept_target"] += 1
            continue
        rows.append(dict(row))
        stats["insert"] += 1
    return mapping, rows, stats, skipped


def make_scoped_map(table, src, dst, columns, *, scope_cols, seq_col, width):
    dst_by = group_map(dst, lambda r: row_key(table, r))
    used: dict[tuple[Any, ...], set[int]] = collections.defaultdict(set)
    for row in list(dst) + list(src):
        n = numeric_seq(row.get(seq_col))
        if n is not None:
            used[tuple(key_value(c, row.get(c)) for c in scope_cols)].add(n)
    mapping: dict[tuple[Any, ...], str] = {}
    insert_rows: list[dict[str, Any]] = []
    stats = collections.Counter()
    conflicts: list[tuple[Any, ...]] = []
    for row in src:
        old_key = row_key(table, row)
        candidates = dst_by.get(old_key, [])
        if candidates and any(same_semantics(row, d, columns, table=table) for d in candidates):
            mapping[old_key] = str(row.get(seq_col) or "").strip()
            stats["duplicate"] += 1
            continue
        if not candidates:
            mapping[old_key] = str(row.get(seq_col) or "").strip()
            insert_rows.append(dict(row))
            stats["insert"] += 1
            continue
        scope = tuple(key_value(c, row.get(c)) for c in scope_cols)
        new_seq = allocate_seq(used[scope], width)
        mapping[old_key] = new_seq
        transformed = dict(row)
        transformed[seq_col] = new_seq
        insert_rows.append(transformed)
        conflicts.append(old_key)
        stats["remapped"] += 1
    return mapping, insert_rows, stats, conflicts


def transform_tenant_refs(row: dict[str, Any], tenant_map):
    if all(c in row for c in ("bunji1", "bunji2", "hosu", "ipju_seq")):
        key = row_key("bd03_det", row)
        if key in tenant_map:
            row["ipju_seq"] = tenant_map[key]
    return row


def transform_out_ref(row: dict[str, Any], out_map):
    if row.get("out_dt") is not None and str(row.get("out_seq") or "").strip():
        old = row_key("bd07_out", {"out_dt": row.get("out_dt"), "out_seq": row.get("out_seq")})
        mapped = out_map.get(old)
        if mapped:
            row["out_seq"] = mapped
    return row


def transform_repair_ref(row: dict[str, Any], repair_map):
    if row.get("suri_dt") is not None and str(row.get("suri_seq") or "").strip():
        old = row_key("bd05_suri", {"suri_dt": row.get("suri_dt"), "suri_seq": row.get("suri_seq")})
        mapped = repair_map.get(old)
        if mapped:
            row["suri_seq"] = mapped
    return row


def make_master_plan(table, src, dst, columns):
    dst_by = group_map(dst, lambda r: row_key(table, r))
    rows = []
    stats = collections.Counter()
    conflicts = []
    for row in src:
        key = row_key(table, row)
        candidates = dst_by.get(key, [])
        if candidates:
            if any(same_semantics(row, d, columns, table=table) for d in candidates):
                stats["duplicate"] += 1
            else:
                stats["conflict"] += 1
                conflicts.append(key)
            continue
        rows.append(dict(row))
        stats["insert"] += 1
    return rows, stats, conflicts


def make_no_key_plan(table, src, dst, columns, *, transformed=None):
    # These tables intentionally have no key in the legacy schema.  Avoid
    # exact duplicate snapshots, but retain distinct rows (including source
    # duplicate details) because they can represent separate history entries.
    existing = {
        signature(row, columns, exclude_key=False, table=table)
        for row in dst
    }
    rows = []
    stats = collections.Counter()
    for original in src:
        row = dict(original)
        if transformed:
            row = transformed(row)
        sig = signature(row, columns, exclude_key=False, table=table)
        if sig in existing:
            stats["duplicate"] += 1
            continue
        rows.append(row)
        stats["insert"] += 1
        # Do not add to existing here: source duplicate rows are retained.
    return rows, stats


def source_only_codes(src, dst, columns):
    key_cols = ("g_cd", "g_sub_cd")
    dst_keys = {row_key("gicho_code", row) for row in dst}
    rows = [dict(r) for r in src if row_key("gicho_code", r) not in dst_keys]
    return rows


def build_plan(source_db: str, target_db: str) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    src_conn = connect(source_db)
    dst_conn = connect(target_db)
    try:
        src_data: dict[str, list[dict[str, Any]]] = {}
        dst_data: dict[str, list[dict[str, Any]]] = {}
        common_cols: dict[str, list[str]] = {}
        for table in READ_TABLES:
            src_cols = table_columns(src_conn, source_db, table)
            dst_cols = table_columns(dst_conn, target_db, table)
            if not src_cols or not dst_cols:
                raise RuntimeError(f"필수 테이블/컬럼이 없습니다: {table}")
            common = [c for c in src_cols if c in dst_cols]
            common_cols[table] = common
            src_data[table] = fetch_rows(src_conn, source_db, table, common)
            dst_data[table] = fetch_rows(dst_conn, target_db, table, common)

        report: dict[str, Any] = {
            "source_db": source_db,
            "target_db": target_db,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "tables": {},
            "quality": {},
            "mappings": {},
        }
        plan_rows: dict[str, list[dict[str, Any]]] = {}

        # Building/room masters are authoritative in the live DB.
        for table in ("bd01", "bd03_m"):
            rows, stats, conflicts = make_master_plan(
                table, src_data[table], dst_data[table], common_cols[table]
            )
            plan_rows[table] = rows
            report["tables"][table] = {
                "source": len(src_data[table]),
                "target_before": len(dst_data[table]),
                **dict(stats),
                "conflict_keys": [list(k) for k in conflicts[:100]],
            }

        tenant_map, tenant_rows, tenant_stats, tenant_conflicts = make_tenant_map(
            src_data["bd03_det"], dst_data["bd03_det"], common_cols["bd03_det"]
        )
        report["mappings"]["tenant_seq_remapped"] = len(tenant_conflicts)
        report["mappings"]["tenant_seq_conflict_samples"] = [list(k) for k in tenant_conflicts[:100]]
        report["tables"]["bd03_det"] = {
            "source": len(src_data["bd03_det"]),
            "target_before": len(dst_data["bd03_det"]),
            **dict(tenant_stats),
        }
        plan_rows["bd03_det"] = tenant_rows

        # Global sequence tables must be mapped before any reference columns
        # are transformed.
        global_maps: dict[str, dict[tuple[Any, ...], str]] = {}
        global_rows: dict[str, list[dict[str, Any]]] = {}
        for table, (date_col, seq_col, width) in GLOBAL_SEQ.items():
            mapping, rows, stats, conflicts = make_global_map(
                table,
                src_data[table],
                dst_data[table],
                common_cols[table],
                date_col,
                seq_col,
                width,
            )
            global_maps[table] = mapping
            global_rows[table] = rows
            report["tables"][table] = {
                "source": len(src_data[table]),
                "target_before": len(dst_data[table]),
                **dict(stats),
                "conflict_keys": [list(k) for k in conflicts[:100]],
            }
        payment_map, payment_rows, payment_stats, payment_conflicts = make_payment_map(
            src_data["sukum01"], dst_data["sukum01"], common_cols["sukum01"]
        )
        report["tables"]["sukum01"] = {
            "source": len(src_data["sukum01"]),
            "target_before": len(dst_data["sukum01"]),
            **dict(payment_stats),
            "conflict_keys": [list(k) for k in payment_conflicts[:100]],
        }

        # A monthly settlement is a derived snapshot.  When the same logical
        # settlement exists in both DBs, keep the newer live snapshot and do
        # not mix old detail rows into it.
        settle_map, settle_rows, settle_stats, settle_skipped = make_settlement_plan(
            src_data["jungsan_m"], dst_data["jungsan_m"], common_cols["jungsan_m"]
        )
        report["tables"]["jungsan_m"] = {
            "source": len(src_data["jungsan_m"]),
            "target_before": len(dst_data["jungsan_m"]),
            **dict(settle_stats),
            "conflict_keys": [list(k) for k in sorted(settle_skipped)[:100]],
        }
        plan_rows["jungsan_m"] = settle_rows

        # Transform references and build rows for global tables.
        transformed_global: dict[str, list[dict[str, Any]]] = {}
        for table, rows in global_rows.items():
            out = []
            for original in rows:
                row = transform_tenant_refs(dict(original), tenant_map)
                out.append(row)
            transformed_global[table] = out
        plan_rows["bd05_suri"] = transformed_global["bd05_suri"]
        plan_rows["bd07_out"] = transformed_global["bd07_out"]
        plan_rows["sunkub01"] = transformed_global["sunkub01"]
        plan_rows["sjungke01"] = transformed_global["sjungke01"]

        # Tenant rows may contain an out-event reference.  Payment rows may
        # contain both a tenant and a repair reference.
        plan_rows["bd03_det"] = [
            transform_out_ref(transform_tenant_refs(dict(r), tenant_map), global_maps["bd07_out"])
            for r in plan_rows["bd03_det"]
        ]
        plan_rows["sukum01"] = [
            transform_repair_ref(
                transform_tenant_refs(dict(r), tenant_map), global_maps["bd05_suri"]
            )
            for r in payment_rows
        ]
        # Replace source payment sequence with the mapped value.  payment_rows
        # already contains the transformed sequence from make_payment_map.

        # jungsan_det has no DB key; map its settlement and tenant references,
        # then remove exact rows already present in the live DB.
        def transform_det(row):
            row = dict(row)
            old_settle = row_key(
                "jungsan_m",
                {
                    "jungsan_dt": row.get("jungsan_dt"),
                    "jungsan_seq": row.get("jungsan_seq"),
                    "bunji1": row.get("bunji1"),
                    "bunji2": row.get("bunji2"),
                },
            )
            mapped_settle = settle_map.get(old_settle)
            if mapped_settle:
                row["jungsan_seq"] = mapped_settle
            return transform_tenant_refs(row, tenant_map)

        det_source = []
        det_parent_skipped = 0
        for row in src_data["jungsan_det"]:
            parent_key = row_key(
                "jungsan_m",
                {
                    "jungsan_dt": row.get("jungsan_dt"),
                    "jungsan_seq": row.get("jungsan_seq"),
                    "bunji1": row.get("bunji1"),
                    "bunji2": row.get("bunji2"),
                },
            )
            if parent_key in settle_skipped:
                det_parent_skipped += 1
                continue
            det_source.append(row)
        det_rows, det_stats = make_no_key_plan(
            "jungsan_det", det_source, dst_data["jungsan_det"], common_cols["jungsan_det"], transformed=transform_det
        )
        det_stats["parent_conflict_kept_target"] = det_parent_skipped
        plan_rows["jungsan_det"] = det_rows
        report["tables"]["jungsan_det"] = {
            "source": len(src_data["jungsan_det"]),
            "target_before": len(dst_data["jungsan_det"]),
            **dict(det_stats),
        }

        # Source-only common codes can be added; credentials are report-only.
        code_rows = source_only_codes(src_data["gicho_code"], dst_data["gicho_code"], common_cols["gicho_code"])
        plan_rows["gicho_code"] = code_rows
        report["tables"]["gicho_code"] = {
            "source": len(src_data["gicho_code"]),
            "target_before": len(dst_data["gicho_code"]),
            "insert": len(code_rows),
            "source_only_keys": [list(row_key("gicho_code", r)) for r in code_rows],
        }
        report["quality"]["sawon_m"] = {
            "source_rows": len(src_data["sawon_m"]),
            "target_rows": len(dst_data["sawon_m"]),
            "action": "not imported (users/passwords require manual review)",
        }

        # Data quality signals useful after the merge.  Do not include names,
        # phone numbers, account numbers, or other personal fields in reports.
        bad_buildings = [
            list(row_key("bd01", r))
            for r in src_data["bd01"]
            if not str(r.get("bunji1") or "").strip().isdigit()
            or not str(r.get("bunji2") or "").strip().isdigit()
        ]
        orphan = []
        building_keys = {row_key("bd01", r) for r in src_data["bd01"]}
        for table in ("bd03_m", "bd03_det", "bd05_suri", "bd07_out", "jungsan_m", "jungsan_det", "sukum01", "sunkub01", "sjungke01"):
            missing = {
                row_key("bd01", r) for r in src_data[table]
                if row_key("bd01", r) not in building_keys
            }
            if missing:
                orphan.append({"table": table, "count": len(missing), "keys": [list(x) for x in sorted(missing)[:20]]})
        replacement_counts = collections.Counter()
        for table, rows in src_data.items():
            for row in rows:
                for col, value in row.items():
                    if isinstance(value, str) and "\ufffd" in value:
                        replacement_counts[f"{table}.{col}"] += value.count("\ufffd")
        report["quality"].update(
            {
                "invalid_building_keys": bad_buildings,
                "orphan_history_keys": orphan,
                "unicode_replacement_chars": dict(replacement_counts),
                "source_sukum_corruption_note": "원본 MyISAM datafile 크기 불일치로 REPAIR 후 136635→136634행; 누락 1행은 원본에서 복구 불가",
                "new_building_management_fields": "source schema lacks mgmt_gb/account/cost extras; inserted rows leave these target-only fields NULL",
            }
        )
        return report, plan_rows, common_cols
    finally:
        src_conn.close()
        dst_conn.close()


def apply_rows(target_db: str, plan_rows: dict[str, list[dict[str, Any]]], common_cols: dict[str, list[str]]) -> dict[str, int]:
    conn = connect(target_db)
    inserted: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION sql_mode=''")
            cur.execute("SET NAMES utf8mb4")
        for table in TABLES:
            rows = plan_rows.get(table) or []
            if not rows:
                inserted[table] = 0
                continue
            cols = common_cols[table]
            sql = (
                "INSERT IGNORE INTO "
                + qi(target_db)
                + "."
                + qi(table)
                + " ("
                + ",".join(qi(c) for c in cols)
                + ") VALUES ("
                + ",".join(["%s"] * len(cols))
                + ")"
            )
            count = 0
            with conn.cursor() as cur:
                for start in range(0, len(rows), 1000):
                    batch = rows[start : start + 1000]
                    cur.executemany(sql, [[r.get(c) for c in cols] for r in batch])
                    count += max(cur.rowcount, 0)
            inserted[table] = count
    finally:
        conn.close()
    return inserted


def json_default(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", default="sinbee_other", help="old-data staging schema")
    parser.add_argument("--target-db", default=config.DB_NAME, help="live schema")
    parser.add_argument("--apply", action="store_true", help="perform inserts; default is read-only")
    parser.add_argument("--report", help="JSON report path")
    args = parser.parse_args()
    if args.source_db == args.target_db:
        print("source/target DB가 같을 수 없습니다", file=sys.stderr)
        return 2
    try:
        report, plan_rows, common_cols = build_plan(args.source_db, args.target_db)
    except Exception as exc:
        print(f"계획 생성 실패: {exc}", file=sys.stderr)
        return 1
    report["mode"] = "apply" if args.apply else "dry-run"
    if args.apply:
        try:
            report["inserted"] = apply_rows(args.target_db, plan_rows, common_cols)
        except Exception as exc:
            report["apply_error"] = str(exc)
            if args.report:
                with open(args.report, "w", encoding="utf-8") as f:
                    json.dump(report, f, ensure_ascii=False, indent=2, default=json_default)
            print(f"병합 중 오류: {exc}", file=sys.stderr)
            return 1
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=json_default)
    for table in TABLES:
        info = report["tables"].get(table, {})
        print(
            f"[{table}] source={info.get('source', 0)} "
            f"insert={info.get('insert', 0)} duplicate={info.get('duplicate', 0)} "
            f"remapped={info.get('remapped', 0)} conflict={info.get('conflict', 0)} "
            f"kept_target={info.get('conflict_kept_target', 0) + info.get('parent_conflict_kept_target', 0)}"
        )
    if args.apply:
        print("병합 완료")
    else:
        print("읽기 전용 사전 점검 완료 (--apply를 붙여 실행하면 기록합니다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
