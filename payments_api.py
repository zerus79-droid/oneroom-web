"""수금 관련 공용 JSON API.

`/api/building` `/api/current_tenant` `/api/payments/delete` — 수금 등록
화면뿐 아니라 `base.html`의 전역 주소 드롭다운 등 여러 화면이 함께 쓰는
API라서 화면 파일(`payments.py`/`payment_register.py`)과 분리했습니다.
"""
from datetime import date, datetime

from flask import jsonify, request, session

import db
from app_instance import app
from utils import (
    calc_misu_amt as _calc_misu_amt,
    calc_month_misu_amt as _calc_month_misu_amt,
    clamp_date_str,
    fmt_bunji,
    fmt_bunji_pair,
    login_required,
    lookup_current_tenant as _lookup_current_tenant,
    money,
    pad_bunji as _pad_bunji,
    parse_bunji_src as _parse_bunji_src,
    require_write_access,
    to_int_amt as _to_int_amt,
    building_label as _building_label,
)


@app.route("/api/building")
@login_required
def api_building():
    """주소-주소2 가 bd01 에 등록된 건물인지 확인"""
    bunji1, bunji2 = _parse_bunji_src(request.args)
    display = fmt_bunji_pair(bunji1, bunji2)
    hosu = (request.args.get("hosu") or "").strip().upper()
    # 주소1(번지1)만 검사: 해당 앞자리로 등록 건물이 하나라도 있는지
    if bunji1 and not bunji2:
        hit = db.query_one(
            "SELECT bunji1 FROM bd01 WHERE bunji1=%s LIMIT 1",
            (bunji1,),
        )
        d1 = fmt_bunji(bunji1)
        if hit:
            return jsonify(
                {
                    "ok": True,
                    "found": True,
                    "bunji1_only": True,
                    "bunji1": bunji1,
                    "bunji2": "",
                    "display": d1,
                    "label": "",
                    "juso": "",
                    "message": "",
                    "room_found": None,
                    "hosu": hosu,
                }
            )
        return jsonify(
            {
                "ok": True,
                "found": False,
                "bunji1_only": True,
                "bunji1": bunji1,
                "bunji2": "",
                "display": d1,
                "label": "미등록 주소",
                "juso": "",
                "message": f"주소 「{d1}」 은(는) 등록된 건물이 없습니다.\n주소를 다시 확인하세요.",
                "room_found": None,
                "hosu": hosu,
            }
        )
    if not bunji1 or not bunji2:
        return jsonify(
            {
                "ok": True,
                "found": False,
                "bunji1": bunji1,
                "bunji2": bunji2,
                "display": display,
                "label": "",
                "juso": "",
                "message": "주소·주소2를 입력하세요.",
            }
        )
    b = db.query_one(
        "SELECT bunji1, bunji2, juso, owner_nm FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (bunji1, bunji2),
    )
    if b:
        juso = (b.get("juso") or "").strip()
        payload = {
            "ok": True,
            "found": True,
            "bunji1": bunji1,
            "bunji2": bunji2,
            "display": display,
            "juso": juso,
            "label": _building_label(bunji1, bunji2),
            "message": "",
            "room_found": None,
            "hosu": hosu,
        }
        # 호수까지 넘기면 호수 등록 여부도 검사
        if hosu:
            room = db.query_one(
                """
                SELECT hosu FROM bd03_m
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                """,
                (bunji1, bunji2, hosu),
            )
            if room:
                payload["room_found"] = True
            else:
                payload["room_found"] = False
                payload["message"] = (
                    f"호수 「{hosu}」 은(는) 주소 {display} 건물에 등록되어 있지 않습니다.\n"
                    "호수를 다시 확인해 주세요."
                )
        return jsonify(payload)
    return jsonify(
        {
            "ok": True,
            "found": False,
            "bunji1": bunji1,
            "bunji2": bunji2,
            "display": display,
            "juso": "",
            "label": "미등록 주소",
            "room_found": False if hosu else None,
            "hosu": hosu,
            "message": f"주소 {display} 은(는) 등록된 건물이 없습니다.\n주소를 다시 확인하세요.",
        }
    )


@app.route("/api/current_tenant")
@login_required
def api_current_tenant():
    """수금 등록: 호실 입력 시 현재 입주 순번 조회"""
    bunji1, bunji2 = _parse_bunji_src(request.args)
    hosu = (request.args.get("hosu") or "").strip().upper()
    row = _lookup_current_tenant(bunji1, bunji2, hosu)
    if not row:
        return jsonify(
            {
                "ok": False,
                "hosu": hosu,
                "ipju_seq": "",
                "ipju_nm": "",
                "bunji1": bunji1,
                "bunji2": bunji2,
                "misu_amt": 0,
                "misu_display": "",
                "month_misu_amt": 0,
                "month_misu_display": "",
                "prev_misu_amt": 0,
                "prev_misu_display": "",
                "rent_amt": 0,
                "manage_amt": 0,
                "monthly_amt": 0,
                "bojung_amt": 0,
                "yechi_amt": 0,
                "ipju_dt": "",
                "ipju_tel": "",
            }
        )
    seq = str(row.get("ipju_seq") or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    h = (row.get("hosu") or hosu).strip()
    rent = _to_int_amt(row.get("rent_amt"))
    manage = _to_int_amt(row.get("manage_amt"))
    monthly = rent + manage
    # 전월미수총액 ≈ 누적 미수
    prev_misu = _calc_misu_amt(
        bunji1,
        bunji2,
        h,
        seq,
        rent_amt=rent,
        manage_amt=manage,
        ipju_dt=row.get("ipju_dt"),
    )
    # 미수총액 = 이번 달 미입금액
    month_misu = _calc_month_misu_amt(
        bunji1,
        bunji2,
        h,
        seq,
        rent_amt=rent,
        manage_amt=manage,
    )
    tel = (row.get("ipju_tel1") or row.get("ipju_tel2") or "").strip()
    ipju_dt = row.get("ipju_dt")
    if isinstance(ipju_dt, datetime):
        ipju_dt_s = ipju_dt.strftime("%Y-%m-%d")
    elif isinstance(ipju_dt, date):
        ipju_dt_s = ipju_dt.isoformat()
    else:
        ipju_dt_s = str(ipju_dt or "")[:10]
    out_dt = row.get("out_dt")
    is_current = not out_dt or (
        isinstance(out_dt, datetime) and out_dt.year < 1000
    ) or (isinstance(out_dt, date) and out_dt.year < 1000)
    return jsonify(
        {
            "ok": True,
            "hosu": h,
            "ipju_seq": seq,
            "ipju_nm": (row.get("ipju_nm") or "").strip(),
            "current": bool(is_current),
            "bunji1": bunji1,
            "bunji2": bunji2,
            "rent_amt": rent,
            "manage_amt": manage,
            "monthly_amt": monthly,
            "bojung_amt": _to_int_amt(row.get("bojung_amt")),
            "yechi_amt": _to_int_amt(row.get("yechi_amt")),
            "ipju_dt": ipju_dt_s,
            "ipju_tel": tel,
            # 미수총액 = 전월 누적 + 금월 미입금 (지금 받을 금액)
            "misu_amt": prev_misu + month_misu,
            "misu_display": money(prev_misu + month_misu),
            "prev_misu_amt": prev_misu + month_misu,
            "prev_misu_display": money(prev_misu + month_misu),
            "arrears_amt": prev_misu,
            "month_misu_amt": month_misu,
            "month_misu_display": money(month_misu),
        }
    )


@app.route("/api/payments/delete", methods=["POST"])
@login_required
@require_write_access
def api_payments_delete():
    """수금 내역 삭제(del_yn='Y'). body: { items: [{sukum_dt,sukum_seq,bunji1,bunji2,hosu}, ...] }"""
    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"ok": False, "message": "삭제할 항목이 없습니다.", "deleted": 0})

    # 배치 처리: 단일 트랜잭션으로 모든 삭제 수행
    conn = db.get_conn()
    try:
        deleted = 0
        errors = []
        sabun = session.get("sabun") or ""

        with conn.cursor() as cur:
            for it in items:
                try:
                    sukum_dt = clamp_date_str(str(it.get("sukum_dt") or "")[:10])
                    sukum_seq = str(it.get("sukum_seq") or "").strip()
                    bunji1 = _pad_bunji(str(it.get("bunji1") or "").strip())
                    bunji2 = _pad_bunji(str(it.get("bunji2") or "").strip())
                    hosu = str(it.get("hosu") or "").strip().upper()
                    if not (sukum_dt and sukum_seq and bunji1 and bunji2 and hosu):
                        errors.append("키 누락")
                        continue
                    n = cur.execute(
                        """
                        UPDATE sukum01
                           SET del_yn='Y',
                               uid=%s,
                               sys_dt=NOW()
                         WHERE sukum_dt >= %s AND sukum_dt < %s + INTERVAL 1 DAY
                           AND sukum_seq=%s
                           AND bunji1=%s AND bunji2=%s
                           AND UPPER(TRIM(hosu))=%s
                           AND (del_yn IS NULL OR del_yn='' OR del_yn='N')
                        """,
                        (
                            sabun,
                            sukum_dt + " 00:00:00",
                            sukum_dt,
                            sukum_seq,
                            bunji1,
                            bunji2,
                            hosu,
                        ),
                    )
                    deleted += int(n or 0)
                except Exception as e:
                    errors.append(str(e))

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "message": f"DB 오류: {str(e)}", "deleted": 0})
    finally:
        conn.close()

    return jsonify(
        {
            "ok": deleted > 0,
            "deleted": deleted,
            "message": f"{deleted}건 삭제했습니다."
            if deleted
            else ("삭제 실패: " + ("; ".join(errors[:3]) if errors else "대상 없음")),
        }
    )
