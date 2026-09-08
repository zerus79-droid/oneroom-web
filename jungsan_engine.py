"""월정산 계산 엔진의 독립적인 순수 함수 모음."""
import math
from calendar import monthrange
from datetime import date, datetime

from utils import to_int_amt as _to_int_amt
import db


def _month_bounds(as_of):
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    start = as_of.replace(day=1)
    end = date(as_of.year, as_of.month, monthrange(as_of.year, as_of.month)[1])
    return start, end


def _as_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _valid_out_dt(v):
    d = _as_date(v)
    return d if d and d.year >= 1000 else None


def _ceil_100(v):
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return 0
    return int(math.ceil(n / 100.0) * 100) if n > 0 else 0


def _prorate_amt(amt, days, month_days):
    amt = _to_int_amt(amt)
    if amt <= 0 or days <= 0:
        return 0
    if month_days > 0 and days >= month_days:
        return amt
    return _ceil_100(amt * days / float(month_days))


def _dache_flag(sil_amt, dache_amt, due_amt=None):
    """대체 표시. due_amt=당월 월세+관리비. 실입이 due 이상이면 대체 해제."""
    sil, dache, due = _to_int_amt(sil_amt), _to_int_amt(dache_amt), _to_int_amt(due_amt)
    if dache <= 0 or (due > 0 and sil >= due):
        return ""
    return "대체"


def _dache_rent_remain(due_amt, sil_amt, dache_amt):
    """신규 대체 가능액. due_amt=당월 월세+관리비."""
    due, sil, dache = _to_int_amt(due_amt), _to_int_amt(sil_amt), _to_int_amt(dache_amt)
    if due <= 0 or sil >= due:
        return 0
    return max(0, due - sil - dache)


def _rent_ipkum_for_pay(sil_amt, dache_amt, due_amt):
    """실입+대체를 due(월세+관리비)까지만 입금/지급에 반영."""
    paid, due = _to_int_amt(sil_amt) + _to_int_amt(dache_amt), _to_int_amt(due_amt)
    if paid <= 0 or due <= 0:
        return 0
    return min(paid, due)


def _cap_dache_to_rent_shortfall(due_amt, sil_amt, dache_amt):
    """대체는 당월 (월세+관리비) 부족분(due - sil)까지만."""
    due = _to_int_amt(due_amt)
    sil = _to_int_amt(sil_amt)
    raw = _to_int_amt(dache_amt)
    if raw <= 0:
        return 0
    return min(raw, max(0, due - sil))


def _shift_month(year_month, delta):
    """(year, month) 튜플을 delta개월 이동."""
    y, m = year_month
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


def _jungsan_calendar_misu_amt(
    rent_amt,
    manage_amt,
    ipju_dt,
    as_of,
    napbu_gb="B",
    *,
    paid_sil=0,
):
    """월정산 라이브 미수: 금액 running balance. (occupancy cycle 미사용)

    - 선불(A): 입주월~기준월 inclusive 청구개월
    - 후불(B): 입주 다음달~기준월 inclusive
    - due = monthly * n_due (monthly = rent_amt + manage_amt, 현재 계약 금액)
    - paid_sil = as_of까지 01 su_sil_amt 합 (대체 dache는 미수에서 차감 안 함)
    - misu = max(0, due - paid_sil)
    """
    monthly = _to_int_amt(rent_amt) + _to_int_amt(manage_amt)
    if monthly <= 0:
        return 0
    start = _as_date(ipju_dt)
    end = _as_date(as_of)
    if not start or not end or end < start:
        return 0
    first = (start.year, start.month)
    last = (end.year, end.month)
    if str(napbu_gb or "B").strip().upper() != "A":
        first = _shift_month(first, 1)
    if first > last:
        return 0
    n_due = (last[0] - first[0]) * 12 + (last[1] - first[1]) + 1
    due = monthly * n_due
    return max(0, due - _to_int_amt(paid_sil))


def _sil01_paid_months_map(b1, b2, as_of):
    """{(hosu, seq): set((y, m), ...)} — 01 실입금이 있는 달력월."""
    if hasattr(as_of, "isoformat"):
        as_of_s = as_of.isoformat()
    else:
        as_of_s = str(as_of)[:10]
    rows = db.query(
        """
        SELECT hosu_norm AS hosu, ipju_seq,
               YEAR(sukum_dt) AS yy, MONTH(sukum_dt) AS mm,
               COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS sil
          FROM sukum01
         WHERE bunji1=%s AND bunji2=%s
           AND sukum_char='01'
           AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
           AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
         GROUP BY hosu_norm, ipju_seq, YEAR(sukum_dt), MONTH(sukum_dt)
        """,
        (b1, b2, as_of_s),
    ) or []
    out = {}
    for r in rows:
        if _to_int_amt(r.get("sil")) <= 0:
            continue
        key = (_hosu_key(r.get("hosu")), _seq_key(r.get("ipju_seq")))
        out.setdefault(key, set()).add((int(r.get("yy")), int(r.get("mm"))))
    return out


def _sil01_paid_months_map_all(as_of, keys=None):
    """건물별 sil01 실입 달력월 {(b1,b2): {(hosu,seq): set((y,m))}}."""
    as_of_s = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)[:10]
    ksql, kargs = _bunji_keys_sql(keys)
    rows = db.query(
        f"""
        SELECT bunji1, bunji2, hosu_norm AS hosu, ipju_seq,
               YEAR(sukum_dt) AS yy, MONTH(sukum_dt) AS mm,
               COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS sil
          FROM sukum01
         WHERE sukum_char='01'
           AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
           AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
           {ksql}
         GROUP BY bunji1, bunji2, hosu_norm, ipju_seq, YEAR(sukum_dt), MONTH(sukum_dt)
        """,
        (as_of_s, *kargs),
        apply_building_access=False,
    ) or []
    out = {}
    for r in rows:
        if _to_int_amt(r.get("sil")) <= 0:
            continue
        bkey = (r.get("bunji1"), r.get("bunji2"))
        tkey = (_hosu_key(r.get("hosu")), _seq_key(r.get("ipju_seq")))
        out.setdefault(bkey, {}).setdefault(tkey, set()).add(
            (int(r.get("yy")), int(r.get("mm")))
        )
    return out


def _jungsan_month_rent_split(napbu, rent, ipju_dt, out_dt, month_start, month_end):
    rent = _to_int_amt(rent)
    out_d = _valid_out_dt(out_dt)
    month_days = (month_end - month_start).days + 1
    if not out_d or not (month_start <= out_d <= month_end) or rent <= 0 or month_days <= 0:
        return rent, 0
    if str(napbu or "B").strip().upper() == "A":
        return rent, _prorate_amt(rent, (month_end - out_d).days, month_days)
    ipju = _as_date(ipju_dt) or month_start
    occ = max(0, (min(out_d, month_end) - max(ipju, month_start)).days + 1)
    return _prorate_amt(rent, occ, month_days), 0


def _jungsan_out_settle_amt(napbu, rent, ipju_dt, out_dt, month_start, month_end):
    out_d, rent = _valid_out_dt(out_dt), _to_int_amt(rent)
    if not out_d or not (month_start <= out_d <= month_end) or rent <= 0:
        return None
    days = (month_end - month_start).days + 1
    prorate = lambda n: int(rent * max(0, n) / float(days))
    if str(napbu or "B").strip().upper() == "A":
        return -prorate((month_end - out_d).days)
    ipju = _as_date(ipju_dt) or month_start
    occ = max(0, (out_d - max(ipju, month_start)).days + 1)
    return (rent if ipju < month_start else 0) + prorate(occ)




def _hosu_key(hosu):
    return (hosu or "").strip().upper()


def _seq_key(seq):
    return str(seq or "").zfill(2)


def _month_sukum_breakdown_map(b1, b2, month_start, month_end_s):
    """한 건물·한 달 수금을 한 번에 읽어 {(hosu, seq): {char: {sil,dache}}}."""
    ms = month_start.isoformat() if hasattr(month_start, "isoformat") else str(month_start)
    rows = db.query(
        """
        SELECT hosu_norm AS hosu, ipju_seq, sukum_char,
               COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS sil,
               COALESCE(SUM(COALESCE(su_dache_amt,0)),0) AS dache
          FROM sukum01
         WHERE bunji1=%s AND bunji2=%s
           AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
           AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
         GROUP BY hosu_norm, ipju_seq, sukum_char
        """,
        (b1, b2, ms, month_end_s),
    ) or []
    out = {}
    for row in rows:
        key = (_hosu_key(row.get("hosu")), _seq_key(row.get("ipju_seq")))
        char = str(row.get("sukum_char") or "").strip().zfill(2)
        out.setdefault(key, {})[char] = {
            "sil": _to_int_amt(row.get("sil")),
            "dache": _to_int_amt(row.get("dache")),
        }
    return out


def _lifetime_sil01_map(b1, b2, as_of):
    """미수 계산용: 당일까지 월세(01) 실입금 합 {(hosu, seq): paid}."""
    if hasattr(as_of, "isoformat"):
        as_of_s = as_of.isoformat()
    else:
        as_of_s = str(as_of)[:10]
    rows = db.query(
        """
        SELECT hosu_norm AS hosu, ipju_seq,
               COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS paid
          FROM sukum01
         WHERE bunji1=%s AND bunji2=%s
           AND sukum_char='01'
           AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
           AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
         GROUP BY hosu_norm, ipju_seq
        """,
        (b1, b2, as_of_s),
    ) or []
    return {
        (_hosu_key(r.get("hosu")), _seq_key(r.get("ipju_seq"))): _to_int_amt(r.get("paid"))
        for r in rows
    }


def _terms_hist_map(b1, b2, as_of):
    """계약변경 이력 {(hosu, seq): [rows...]} — 미수 기간청구용."""
    if hasattr(as_of, "isoformat"):
        as_of_s = as_of.isoformat()
    else:
        as_of_s = str(as_of)[:10]
    try:
        rows = db.query(
            """
            SELECT hosu, ipju_seq, effective_dt, rent_amt, manage_amt
              FROM bd03_terms_hist
             WHERE bunji1=%s AND bunji2=%s
               AND effective_dt <= %s
             ORDER BY effective_dt, hist_id
            """,
            (b1, b2, as_of_s),
        ) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        key = (_hosu_key(r.get("hosu")), _seq_key(r.get("ipju_seq")))
        out.setdefault(key, []).append(r)
    return out


def _month_sukum_sil_dache(b1, b2, hosu, seq, month_start, month_end_s):
    row = db.query_one(
        """SELECT COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS sil,
                  COALESCE(SUM(COALESCE(su_dache_amt,0)),0) AS dache
             FROM sukum01
            WHERE bunji1=%s AND bunji2=%s
              AND hosu_norm=%s AND ipju_seq=%s
              AND sukum_char='01'
              AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
              AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)""",
        (
            b1, b2, (hosu or "").strip().upper(),
            str(seq or "").zfill(2) if seq else "",
            month_start.isoformat() if hasattr(month_start, "isoformat") else str(month_start),
            month_end_s,
        ),
    )
    return _to_int_amt((row or {}).get("sil")), _to_int_amt((row or {}).get("dache"))


def _month_sukum_breakdown(b1, b2, hosu, seq, month_start, month_end_s, *, pay_map=None):
    """월별 수금 성격별 실입금·대체금 집계.

    sukum_char는 수금 방식(sukum_gb)이 아니라 수금 성격이다.
    01=월세+관리비, 02=보증금, 03=예치금, 04=수리비,
    05=중개보수, 06=퇴실정산 임대료, 07=퇴실정산 관리비.
    기존 호환을 위해 값이 없는 성격도 0으로 반환한다.
    pay_map이 있으면 DB 재조회 없이 맵에서 꺼낸다.
    """
    if pay_map is not None:
        return dict(pay_map.get((_hosu_key(hosu), _seq_key(seq))) or {})
    rowset = db.query(
        """SELECT sukum_char,
                  COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS sil,
                  COALESCE(SUM(COALESCE(su_dache_amt,0)),0) AS dache
             FROM sukum01
            WHERE bunji1=%s AND bunji2=%s
              AND hosu_norm=%s AND ipju_seq=%s
              AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
              AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
            GROUP BY sukum_char""",
        (
            b1, b2, (hosu or "").strip().upper(), str(seq or "").zfill(2),
            month_start.isoformat() if hasattr(month_start, "isoformat") else str(month_start),
            month_end_s,
        ),
    ) or []
    result = {}
    for row in rowset:
        key = str(row.get("sukum_char") or "").strip().zfill(2)
        result[key] = {
            "sil": _to_int_amt(row.get("sil")),
            "dache": _to_int_amt(row.get("dache")),
        }
    return result


def _month_out_adjustment(b1, b2, hosu, seq, month_start, month_end_s, *, pay_map=None):
    if pay_map is not None:
        part = (pay_map.get((_hosu_key(hosu), _seq_key(seq))) or {}).get("06") or {}
        amt = _to_int_amt(part.get("sil"))
        return amt > 0, amt, ""
    row = db.query_one(
        """SELECT COUNT(*) AS cnt,
                  COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS amt,
                  MAX(COALESCE(manage_desc,'')) AS manage_desc
             FROM sukum01
            WHERE bunji1=%s AND bunji2=%s
              AND hosu_norm=%s AND ipju_seq=%s
              AND sukum_char='06'
              AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
              AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)""",
        (
            b1, b2, (hosu or "").strip().upper(),
            str(seq or "").zfill(2) if seq else "",
            month_start.isoformat() if hasattr(month_start, "isoformat") else str(month_start),
            month_end_s,
        ),
    )
    return (
        _to_int_amt((row or {}).get("cnt")) > 0,
        _to_int_amt((row or {}).get("amt")),
        ((row or {}).get("manage_desc") or "").strip(),
    )





def _bunji_keys_sql(keys, col1="bunji1", col2="bunji2"):
    """Optional (bunji1,bunji2) filter. keys=None → no filter; [] → match nothing."""
    if keys is None:
        return "", []
    keys = list(keys or [])
    if not keys:
        return f" AND 1=0", []
    parts, args = [], []
    for b1, b2 in keys:
        parts.append(f"({col1}=%s AND {col2}=%s)")
        args.extend([b1, b2])
    return " AND (" + " OR ".join(parts) + ")", args


def _month_sukum_breakdown_map_all(month_start, month_end_s, keys=None):
    """당월 수금 {(b1,b2): {(hosu,seq): {char:{sil,dache}}}}. keys면 해당 건물만."""
    ms = month_start.isoformat() if hasattr(month_start, "isoformat") else str(month_start)
    ksql, kargs = _bunji_keys_sql(keys)
    rows = db.query(
        f"""
        SELECT bunji1, bunji2, hosu_norm AS hosu, ipju_seq, sukum_char,
               COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS sil,
               COALESCE(SUM(COALESCE(su_dache_amt,0)),0) AS dache
          FROM sukum01
         WHERE (del_yn IS NULL OR del_yn='N' OR del_yn='')
           AND sukum_dt >= %s AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
           {ksql}
         GROUP BY bunji1, bunji2, hosu_norm, ipju_seq, sukum_char
        """,
        (ms, month_end_s, *kargs),
        apply_building_access=False,
    ) or []
    out = {}
    for row in rows:
        bkey = (row.get("bunji1"), row.get("bunji2"))
        tkey = (_hosu_key(row.get("hosu")), _seq_key(row.get("ipju_seq")))
        char = str(row.get("sukum_char") or "").strip().zfill(2)
        out.setdefault(bkey, {}).setdefault(tkey, {})[char] = {
            "sil": _to_int_amt(row.get("sil")),
            "dache": _to_int_amt(row.get("dache")),
        }
    return out


def _lifetime_sil01_map_all(as_of, keys=None):
    """누적 월세실입 {(b1,b2): {(hosu,seq): paid}}. keys면 해당 건물만."""
    as_of_s = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)[:10]
    ksql, kargs = _bunji_keys_sql(keys)
    rows = db.query(
        f"""
        SELECT bunji1, bunji2, hosu_norm AS hosu, ipju_seq,
               COALESCE(SUM(COALESCE(su_sil_amt,0)),0) AS paid
          FROM sukum01
         WHERE sukum_char='01'
           AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
           AND sukum_dt < DATE_ADD(%s, INTERVAL 1 DAY)
           {ksql}
         GROUP BY bunji1, bunji2, hosu_norm, ipju_seq
        """,
        (as_of_s, *kargs),
        apply_building_access=False,
    ) or []
    out = {}
    for r in rows:
        bkey = (r.get("bunji1"), r.get("bunji2"))
        tkey = (_hosu_key(r.get("hosu")), _seq_key(r.get("ipju_seq")))
        out.setdefault(bkey, {})[tkey] = _to_int_amt(r.get("paid"))
    return out


def _terms_hist_map_all(as_of, keys=None):
    as_of_s = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)[:10]
    ksql, kargs = _bunji_keys_sql(keys)
    try:
        rows = db.query(
            f"""
            SELECT bunji1, bunji2, hosu, ipju_seq, effective_dt, rent_amt, manage_amt
              FROM bd03_terms_hist
             WHERE effective_dt <= %s
               {ksql}
             ORDER BY bunji1, bunji2, effective_dt, hist_id
            """,
            (as_of_s, *kargs),
            apply_building_access=False,
        ) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        bkey = (r.get("bunji1"), r.get("bunji2"))
        tkey = (_hosu_key(r.get("hosu")), _seq_key(r.get("ipju_seq")))
        out.setdefault(bkey, {}).setdefault(tkey, []).append(r)
    return out


def _jungsan_month_tenants_all(month_start, month_end, keys=None):
    """당월 거주자 {(b1,b2): [tenant rows]}. keys면 해당 건물만."""
    ksql, kargs = _bunji_keys_sql(keys, "m.bunji1", "m.bunji2")
    rows = db.query(
        f"""
        SELECT m.bunji1, m.bunji2, m.hosu, d.ipju_seq, d.ipju_nm, d.ipju_dt, d.out_dt,
               d.bojung_amt, d.yechi_amt, d.rent_amt, d.manage_amt, d.napbu_gb
          FROM bd03_m m
          LEFT JOIN bd03_det d
            ON d.bunji1=m.bunji1 AND d.bunji2=m.bunji2
           AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu))
           AND (d.del_yn IS NULL OR d.del_yn='N' OR d.del_yn='')
           AND d.ipju_dt IS NOT NULL
           AND d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY)
           AND (d.out_dt IS NULL OR d.out_dt < '1000-01-01' OR d.out_dt >= %s)
         WHERE 1=1
           {ksql}
         ORDER BY m.bunji1, m.bunji2, m.hosu, d.ipju_dt
        """,
        (month_end.isoformat(), month_start.isoformat(), *kargs),
        apply_building_access=False,
    ) or []
    out = {}
    for r in rows:
        out.setdefault((r.get("bunji1"), r.get("bunji2")), []).append(r)
    return out


def _month_cost_maps(month_start, month_end_s, keys=None):
    """수리·중개 건물별 합 {(b1,b2): amt}. keys면 해당 건물만."""
    ms = month_start.isoformat() if hasattr(month_start, "isoformat") else str(month_start)
    ksql, kargs = _bunji_keys_sql(keys)
    suri = {}
    jungke = {}
    try:
        for r in db.query(
            f"""
            SELECT bunji1, bunji2, COALESCE(SUM(COALESCE(owner_budam,0)),0) AS a
              FROM bd05_suri
             WHERE suri_dt >= %s AND suri_dt < DATE_ADD(%s, INTERVAL 1 DAY)
               {ksql}
             GROUP BY bunji1, bunji2
            """,
            (ms, month_end_s, *kargs),
            apply_building_access=False,
        ) or []:
            suri[(r.get("bunji1"), r.get("bunji2"))] = _to_int_amt(r.get("a"))
    except Exception:
        pass
    try:
        for r in db.query(
            f"""
            SELECT bunji1, bunji2, COALESCE(SUM(COALESCE(jungke_amt,0)),0) AS a
              FROM sjungke01
             WHERE jungke_dt >= %s AND jungke_dt < DATE_ADD(%s, INTERVAL 1 DAY)
               {ksql}
             GROUP BY bunji1, bunji2
            """,
            (ms, month_end_s, *kargs),
            apply_building_access=False,
        ) or []:
            jungke[(r.get("bunji1"), r.get("bunji2"))] = _to_int_amt(r.get("a"))
    except Exception:
        pass
    return suri, jungke

def _jungsan_month_tenants(b1, b2, month_start, month_end):
    return db.query("""SELECT m.hosu,d.ipju_seq,d.ipju_nm,d.ipju_dt,d.out_dt,d.bojung_amt,d.yechi_amt,d.rent_amt,d.manage_amt,d.napbu_gb FROM bd03_m m LEFT JOIN bd03_det d ON d.bunji1=m.bunji1 AND d.bunji2=m.bunji2 AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu)) AND (d.del_yn IS NULL OR d.del_yn='N' OR d.del_yn='') AND d.ipju_dt IS NOT NULL AND d.ipju_dt < DATE_ADD(%s, INTERVAL 1 DAY) AND (d.out_dt IS NULL OR d.out_dt < '1000-01-01' OR d.out_dt >= %s) WHERE m.bunji1=%s AND m.bunji2=%s ORDER BY m.hosu,d.ipju_dt""", (month_end.isoformat(),month_start.isoformat(),b1,b2))


def _fmt_man_int(v):
    n = _to_int_amt(v)
    return "" if n <= 0 else str(int(round(n / 10000)))


def _fmt_man_dec(v):
    n = _to_int_amt(v)
    man = n / 10000.0
    return f"{int(round(man))}.0" if abs(man - round(man)) < 1e-9 else f"{man:.1f}"


def _fmt_wolse_cell(napbu_gb, rent_amt):
    n = _to_int_amt(rent_amt)
    if n <= 0 and not napbu_gb:
        return ""
    tag = "선" if str(napbu_gb or "").upper() == "A" else "후"
    return f"{tag} {int(round(n / 10000)) if n else 0}"
