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
