"""수금(대체) 등록 화면.

`/payments/new` 라우트와 그 전용 도우미 함수들을 모아둔 모듈입니다.
목록/검색은 `payments.py`, 건물·현세입자 조회 API는 `payments_api.py`에 있습니다.
"""
from datetime import date, timedelta

from flask import flash, make_response, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    building_label as _building_label,
    buildings_and_rooms as _buildings_and_rooms,
    clamp_date_str,
    first_date_for_tenant as _first_date_for_tenant,
    login_required,
    make_pager as _make_pager,
    next_sukum_seq as _next_sukum_seq,
    pad_bunji as _pad_bunji,
    pad_ipju_seq as _pad_ipju_seq,
    parse_bunji_src as _parse_bunji_src,
    PAGE_SIZE as _PAGE_SIZE,
    require_write_access,
    tenant_key as _tenant_key,
)


def _hist_page_for_payment(bunji1, bunji2, hosu, ipju_seq, sukum_dt, sukum_seq, date_from=""):
    """기간별 수금 목록(오래된 순)에서 이 수금이 있는 페이지 번호."""
    b1 = _pad_bunji(bunji1)
    b2 = _pad_bunji(bunji2)
    h = (hosu or "").strip().upper()
    seq = _pad_ipju_seq(ipju_seq)
    dt = str(sukum_dt or "")[:10]
    sq = str(sukum_seq or "").strip()
    if not (b1 and b2 and h and dt):
        return 1
    extra = []
    args = [b1, b2, h]
    if seq:
        extra.append("LPAD(TRIM(s.ipju_seq),2,'0')=LPAD(TRIM(%s),2,'0')")
        args.append(seq)
    if date_from:
        extra.append("s.sukum_dt >= %s")
        args.append(str(date_from)[:10] + " 00:00:00")
    extra_sql = (" AND " + " AND ".join(extra)) if extra else ""
    target_start = dt + " 00:00:00"
    try:
        target_end = (date.fromisoformat(dt) + timedelta(days=1)).isoformat()
    except ValueError:
        target_end = dt + " 23:59:59"
    args.extend([target_start, target_start, target_end, sq])
    row = db.query_one(
        f"""
        SELECT COUNT(*) AS c
        FROM sukum01 s
        WHERE s.bunji1=%s AND s.bunji2=%s AND s.hosu_norm=%s
          {extra_sql}
          AND (s.del_yn IS NULL OR s.del_yn='' OR s.del_yn='N')
          AND (
            s.sukum_dt < %s
            OR (s.sukum_dt >= %s AND s.sukum_dt < %s
                AND CAST(s.sukum_seq AS UNSIGNED) < CAST(%s AS UNSIGNED))
          )
        """,
        tuple(args),
    )
    before = int((row or {}).get("c") or 0)
    return before // int(_PAGE_SIZE) + 1


def _recent_payments():
    """수금 등록 화면 하단: 오늘 입력한 수금. 20건씩 페이지.
    각 행에 hist_from/hist_to·hist_page 를 붙여 클릭 시 그 수금이 있는 페이지로 이동.
    """
    today = date.today().isoformat()
    day_from = today + " 00:00:00"
    total = int(
        (
            db.query_one(
                """
                SELECT COUNT(*) AS c
                FROM sukum01 s
                WHERE s.sys_dt >= %s AND s.sys_dt < %s + INTERVAL 1 DAY
                  AND (s.del_yn IS NULL OR s.del_yn='' OR s.del_yn='N')
                """,
                (day_from, today),
            )
            or {}
        ).get("c")
        or 0
    )
    pager = _make_pager(total)
    rows = db.query(
        """
        SELECT s.sukum_dt, s.sukum_seq, s.bunji1, s.bunji2, s.hosu, s.ipju_seq,
               s.sukum_char, s.sukum_gb, s.manage_desc,
               s.su_sil_amt, s.su_dache_amt, s.sys_dt,
               c1.g_cd_nm AS char_nm, c2.g_cd_nm AS gb_nm,
               d.ipju_nm
        FROM sukum01 s
        LEFT JOIN gicho_code c1
          ON c1.g_cd='01' AND c1.g_sub_cd=s.sukum_char
        LEFT JOIN gicho_code c2
          ON c2.g_cd='02' AND c2.g_sub_cd=s.sukum_gb
        LEFT JOIN bd03_det d
          ON d.bunji1=s.bunji1 AND d.bunji2=s.bunji2
             AND d.hosu_norm=s.hosu_norm AND d.ipju_seq=s.ipju_seq
        WHERE s.sys_dt >= %s AND s.sys_dt < %s + INTERVAL 1 DAY
          AND (s.del_yn IS NULL OR s.del_yn='' OR s.del_yn='N')
        ORDER BY s.sys_dt DESC, s.sukum_dt DESC, CAST(s.sukum_seq AS UNSIGNED) DESC
        LIMIT %s OFFSET %s
        """,
        (day_from, today, pager["per_page"], pager["offset"]),
    )
    cache = {}
    for r in rows or []:
        key = _tenant_key(r.get("bunji1"), r.get("bunji2"), r.get("hosu"), r.get("ipju_seq"))
        if key not in cache:
            first = _first_date_for_tenant(key[0], key[1], key[2], key[3])
            cache[key] = first or "2000-01-01"
        r["hist_from"] = cache[key]
        r["hist_to"] = today
        r["hist_page"] = _hist_page_for_payment(
            r.get("bunji1"),
            r.get("bunji2"),
            r.get("hosu"),
            r.get("ipju_seq"),
            r.get("sukum_dt"),
            r.get("sukum_seq"),
            cache[key],
        )
    return rows, pager


def _payment_form_codes():
    chars = db.query(
        """
        SELECT g_sub_cd, g_cd_nm FROM gicho_code
        WHERE g_cd='01' AND g_sub_cd <> '00'
        ORDER BY g_sub_cd
        """
    )
    gbs = db.query(
        """
        SELECT g_sub_cd, g_cd_nm FROM gicho_code
        WHERE g_cd='02' AND g_sub_cd <> '00'
        ORDER BY g_sub_cd
        """
    )
    return chars, gbs


def _tenants_in_building(bunji1, bunji2):
    if not (bunji1 and bunji2):
        return []
    return db.query(
        """
        SELECT hosu, ipju_seq, ipju_nm, out_dt
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s
        ORDER BY (out_dt IS NULL) DESC, hosu, ipju_seq DESC
        """,
        (bunji1, bunji2),
    )


def _render_payment_new(buildings, rooms, chars, gbs, form, tenants, recent, pager=None):
    resp = make_response(
        render_template(
            "payment_new.html",
            buildings=buildings,
            rooms=rooms,
            chars=chars,
            gbs=gbs,
            form=form,
            tenants=tenants,
            recent_payments=recent,
            pager=pager,
            building_label=_building_label(form.get("bunji1"), form.get("bunji2")),
        )
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/payments/new", methods=["GET", "POST"])
@login_required
@require_write_access
def payment_new():
    buildings, rooms = _buildings_and_rooms()
    chars, gbs = _payment_form_codes()

    arg_b1, arg_b2 = _parse_bunji_src(request.args)
    pre = {
        "bunji1": arg_b1,
        "bunji2": arg_b2,
        "hosu": (request.args.get("hosu") or "").strip().upper(),
        "ipju_seq": (request.args.get("ipju_seq") or "").strip(),
        "sukum_dt": date.today().isoformat(),
        "sukum_char": "01",
        "sukum_gb": "03",  # 기본: 통장입금
        "su_sil_amt": "",
        "su_dache_amt": "",
        "manage_desc": "",
    }
    # form 금액은 템플릿에서 |money 로 표시

    tenants = _tenants_in_building(pre["bunji1"], pre["bunji2"])
    if pre["bunji1"] and pre["bunji2"]:
        # 호실만 있고 순번 없으면 현재 입주 순번 자동
        if pre["hosu"] and not pre["ipju_seq"]:
            for t in tenants:
                if (t.get("hosu") or "").strip().upper() != pre["hosu"]:
                    continue
                if t.get("out_dt"):
                    continue
                seq = (t.get("ipju_seq") or "").strip()
                if seq:
                    pre["ipju_seq"] = seq.zfill(2)
                    break
        elif pre["ipju_seq"]:
            pre["ipju_seq"] = pre["ipju_seq"].zfill(2)

    if request.method == "POST":
        bunji1, bunji2 = _parse_bunji_src(request.form)
        hosu = (request.form.get("hosu") or "").strip().upper()
        ipju_seq = (request.form.get("ipju_seq") or "").strip().zfill(2)
        sukum_dt = clamp_date_str((request.form.get("sukum_dt") or "").strip())
        sukum_char = (request.form.get("sukum_char") or "01").strip().zfill(2)
        sukum_gb = (request.form.get("sukum_gb") or "03").strip().zfill(2)
        manage_desc = (request.form.get("manage_desc") or "").strip()
        amount_raw = (request.form.get("su_sil_amt") or "0").replace(",", "").strip()
        dache_raw = (request.form.get("su_dache_amt") or "0").replace(",", "").strip()

        pre.update(
            {
                "bunji1": bunji1,
                "bunji2": bunji2,
                "hosu": hosu,
                "ipju_seq": ipju_seq,
                "sukum_dt": sukum_dt,
                "sukum_char": sukum_char,
                "sukum_gb": sukum_gb,
                "su_sil_amt": amount_raw,
                "su_dache_amt": dache_raw,
                "manage_desc": manage_desc,
            }
        )
        tenants = _tenants_in_building(bunji1, bunji2)
        recent, pager = _recent_payments()

        try:
            amount = int(amount_raw or 0)
            dache_amt = int(dache_raw or 0)
        except ValueError:
            flash("금액은 숫자로 입력하세요.", "err")
            return _render_payment_new(
                buildings, rooms, chars, gbs, pre, tenants, recent, pager
            )

        if not (bunji1 and bunji2 and hosu and ipju_seq and sukum_dt):
            flash("건물(주소·주소2), 호실, 입주순번, 수금일은 필수입니다.", "err")
            return _render_payment_new(
                buildings, rooms, chars, gbs, pre, tenants, recent, pager
            )

        # 순번: 같은 날 + 같은 건물·호실만 카운트
        sukum_seq = _next_sukum_seq(sukum_dt, bunji1, bunji2, hosu)

        try:
            db.execute(
                """
                INSERT INTO sukum01 (
                    sukum_dt, sukum_seq, bunji1, bunji2, hosu, ipju_seq,
                    sukum_char, sukum_gb, manage_desc, su_sil_amt, su_dache_amt,
                    suri_dt, suri_seq, s_method, del_yn, sys_dt, uid
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    NULL, '', '', 'N', NOW(), %s
                )
                """,
                (
                    sukum_dt + " 00:00:00",
                    sukum_seq,
                    bunji1,
                    bunji2,
                    hosu,
                    ipju_seq,
                    sukum_char,
                    sukum_gb,
                    manage_desc,
                    amount,
                    dache_amt,
                    session.get("sabun") or "",
                ),
            )
        except Exception as e:
            flash(f"저장 실패: {e}", "err")
            return _render_payment_new(
                buildings, rooms, chars, gbs, pre, tenants, recent, pager
            )

        flash("저장했습니다.", "ok")
        # 같은 수금 등록 화면에 머무름 + 하단 목록에 방금 입력 표시
        return redirect(
            url_for(
                "payment_new",
                bunji1=bunji1,
                bunji2=bunji2,
                hosu=hosu,
                ipju_seq=ipju_seq,
            )
        )

    recent, pager = _recent_payments()
    return _render_payment_new(buildings, rooms, chars, gbs, pre, tenants, recent, pager)
