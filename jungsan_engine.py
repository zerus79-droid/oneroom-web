"""월정산 계산 엔진의 독립적인 순수 함수 모음."""
import math
from calendar import monthrange
from datetime import date, datetime

from utils import to_int_amt as _to_int_amt


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
    tag = "선불" if str(napbu_gb or "").upper() == "A" else "후불"
    return f"{tag} {int(round(n / 10000)) if n else 0}"
