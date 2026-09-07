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


def _dache_flag(sil_amt, dache_amt, rent_calc=None):
    sil, dache, rent = _to_int_amt(sil_amt), _to_int_amt(dache_amt), _to_int_amt(rent_calc)
    if dache <= 0 or (rent > 0 and sil >= rent):
        return ""
    return "대체"


def _dache_rent_remain(rent_calc, sil_amt, dache_amt):
    rent, sil, dache = _to_int_amt(rent_calc), _to_int_amt(sil_amt), _to_int_amt(dache_amt)
    if rent <= 0 or sil >= rent:
        return 0
    return max(0, rent - sil - dache)


def _rent_ipkum_for_pay(sil_amt, dache_amt, rent_calc):
    paid, rent = _to_int_amt(sil_amt) + _to_int_amt(dache_amt), _to_int_amt(rent_calc)
    if paid <= 0 or rent <= 0:
        return 0
    return min(paid, rent)


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
