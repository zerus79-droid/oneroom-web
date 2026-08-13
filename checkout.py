"""퇴실 정산 관리 화면.

퇴실 정산 작성/저장, 퇴실(예정)자 조회, 계약 해지 인쇄
라우트와 그 전용 도우미 함수들을 모아둔 모듈입니다.
"""
from datetime import date, datetime, timedelta

from flask import flash, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    CURRENT_TENANT_SQL as _CURRENT_TENANT_SQL,
    building_label as _building_label,
    build_pager as _build_pager,
    fmt_bunji,
    fmt_date,
    fmt_ipju_short as _fmt_ipju_short,
    mask_jumin,
    login_required,
    money,
    pad_bunji as _pad_bunji,
    tenant_is_past_out as _tenant_is_past_out,
    to_int_amt as _to_int_amt,
)


def _to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _period_mm_dd(ipju_dt, out_dt):
    """입주~퇴실 개월·일 (XP 입주기간)."""
    start = _to_date(ipju_dt)
    end = _to_date(out_dt)
    if not start or not end or end < start:
        return 0, 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
        # 이전달 말일
        first = end.replace(day=1)
        prev_last = first - timedelta(days=1)
        days = prev_last.day - start.day + end.day
    else:
        days = end.day - start.day
    return max(0, months), max(0, days)


def _checkout_build(bunji1, bunji2, hosu, ipju_seq, out_dt, extra=None):
    """
    퇴실 정산 미리보기 데이터.
    extra: 화면에서 입력한 공과·수리 등 {elec, water, restore, gas, etc, suri}
    """
    extra = extra or {}
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    hosu = (hosu or "").strip().upper()
    seq = (ipju_seq or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    out_d = _to_date(out_dt) or date.today()

    if not (b1 and b2 and hosu):
        return {"error": "주소·호수를 입력하세요."}

    def _missing_addr_or_room():
        bld = db.query_one(
            "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
            (b1, b2),
        )
        if not bld:
            return "등록되지 않은 주소입니다. 주소를 확인하세요."
        room = db.query_one(
            """
            SELECT hosu FROM bd03_m
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
            """,
            (b1, b2, hosu),
        )
        if not room:
            return "등록되지 않은 호수입니다. 호수를 확인하세요."
        return None

    tenant = None
    # 순번 지정(퇴실자 목록→정산 링크): 해당 이력 1건
    if seq:
        tenant = db.query_one(
            """
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (b1, b2, hosu, seq),
        )
        if not tenant:
            miss = _missing_addr_or_room()
            if miss:
                return {"error": miss}
            return {"error": f"순번 {seq} 입주 이력이 없습니다."}
    else:
        # 일반 조회: 반드시 현재 입주자 (과거 퇴실자로 자동 폴백 금지)
        tenant = db.query_one(
            f"""
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
              AND {_CURRENT_TENANT_SQL.replace('d.', '')}
            ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
            LIMIT 1
            """,
            (b1, b2, hosu),
        )
        if not tenant:
            miss = _missing_addr_or_room()
            if miss:
                return {"error": miss}
            past = db.query_one(
                """
                SELECT ipju_nm, ipju_seq, out_dt FROM bd03_det
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
                LIMIT 1
                """,
                (b1, b2, hosu),
            )
            if past:
                pnm = (past.get("ipju_nm") or "").strip() or "(이름없음)"
                pseq = str(past.get("ipju_seq") or "").zfill(2)
                pout = fmt_date(past.get("out_dt")) or ""
                extra_msg = f" 최근 퇴실: {pnm} (순번 {pseq}"
                if pout:
                    extra_msg += f", 퇴실 {pout}"
                extra_msg += ")."
                return {
                    "error": "현재 입주자가 없습니다." + extra_msg
                    + " 과거 정산은 퇴실(예정)자 조회에서 열어 주세요."
                }
            return {"error": "해당 호수의 입주 이력이 없습니다."}

    seq = str(tenant.get("ipju_seq") or "").zfill(2)
    ipju_d = _to_date(tenant.get("ipju_dt"))
    bojung = _to_int_amt(tenant.get("bojung_amt"))
    yechi = _to_int_amt(tenant.get("yechi_amt"))
    rent = _to_int_amt(tenant.get("rent_amt"))
    manage = _to_int_amt(tenant.get("manage_amt"))
    napbu = (tenant.get("napbu_gb") or "B").strip().upper()
    nm = (tenant.get("ipju_nm") or "").strip()
    tel = (
        (tenant.get("ipju_tel1") or tenant.get("ipju_tel3") or tenant.get("ipju_tel2") or "")
        .strip()
    )
    jumin = (tenant.get("ipju_jumin_no") or "").strip()

    mm, dd = _period_mm_dd(ipju_d, out_d)
    monthly = rent + manage
    # 후납 기준 입금총액 추정: 보증+예치+(임+관)×개월 + 일할 임대 + 수리
    suri = _to_int_amt(extra.get("suri"))
    if suri <= 0:
        suri_row = db.query_one(
            """
            SELECT COALESCE(SUM(COALESCE(ipjuja_budam,0)),0) AS a
            FROM bd05_suri
            WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (b1, b2, hosu, seq),
        )
        suri = _to_int_amt((suri_row or {}).get("a"))

    day_rent = int(round(rent * dd / 30.0)) if dd and rent else 0
    if napbu == "A":
        # 선납: 대략 보+예+(임+관)×개월 + 수리 − 일할 환급 추정
        base = bojung + yechi + monthly * mm + suri - day_rent
    else:
        base = bojung + yechi + monthly * mm + day_rent + suri
    base = max(0, base)

    pays = db.query(
        """
        SELECT sukum_dt, sukum_seq, sukum_char, sukum_gb,
               su_sil_amt, su_dache_amt
        FROM sukum01
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
          AND (del_yn IS NULL OR del_yn='N' OR del_yn='')
        ORDER BY sukum_dt, sukum_seq
        """,
        (b1, b2, hosu, seq),
    )
    pay_list = []
    sukum_tot = 0
    for p in pays or []:
        sil = _to_int_amt(p.get("su_sil_amt"))
        dac = _to_int_amt(p.get("su_dache_amt"))
        tot = sil + dac
        sukum_tot += tot
        pay_list.append(
            {
                "dt": _fmt_ipju_short(p.get("sukum_dt")),
                "dt_full": fmt_date(p.get("sukum_dt")),
                "amt": tot,
                "amt_disp": money(tot),
                "sil": sil,
                "dache": dac,
                "char": p.get("sukum_char") or "",
            }
        )

    h_amt = bojung + yechi  # 환불성수금
    misu = max(0, base - sukum_tot)
    elec = _to_int_amt(extra.get("elec"))
    water = _to_int_amt(extra.get("water"))
    restore = _to_int_amt(extra.get("restore"))
    gas = _to_int_amt(extra.get("gas"))
    gita = _to_int_amt(extra.get("etc"))
    util_tot = elec + water + restore + gas + gita
    jungsan = h_amt - misu - util_tot

    bldg = db.query_one(
        "SELECT juso FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (b1, b2),
    )
    juso = ((bldg or {}).get("juso") or "").strip()
    addr = f"{fmt_bunji(b1)}-{fmt_bunji(b2)}"
    nap_label = "선" if napbu == "A" else "후"
    rent_man_label = f"{int(round(rent/10000)) if rent else 0}/{int(round(manage/10000)) if manage else 0} {nap_label}"
    is_current = not _tenant_is_past_out(tenant.get("out_dt"))

    return {
        "error": None,
        "bunji1": b1,
        "bunji2": b2,
        "hosu": hosu,
        "ipju_seq": seq,
        "out_dt": out_d.isoformat(),
        "gijun_dt": out_d.isoformat(),
        "ipju_dt": fmt_date(ipju_d),
        "tenant_out_dt": fmt_date(tenant.get("out_dt")) if not is_current else "",
        "is_current": is_current,
        "ipju_nm": nm,
        "tel": tel,
        "jumin": jumin,
        "bojung_amt": bojung,
        "yechi_amt": yechi,
        "rent_amt": rent,
        "manage_amt": manage,
        "napbu_gb": napbu,
        "nap_label": nap_label,
        "rent_manage_label": rent_man_label,
        "addr": addr,
        "juso": juso,
        "mm": mm,
        "dd": dd,
        "period_label": f"{mm}개월 {dd}일",
        "suri_amt": suri,
        "ipkum_gijun": base,
        "sukum_tot": sukum_tot,
        "h_amt": h_amt,
        "misu_amt": misu,
        "elec_amt": elec,
        "sudo_amt": water,
        "sisul_amt": restore,
        "gas_amt": gas,
        "gita_amt": gita,
        "util_tot": util_tot,
        "jungsan_amt": jungsan,
        "payments": pay_list,
        "building_label": juso or addr,
    }


def _checkout_to_print(data):
    """계약 해지 인쇄 템플릿용 dict."""
    util_tot = _to_int_amt(data.get("util_tot"))
    h = _to_int_amt(data.get("h_amt"))
    misu = _to_int_amt(data.get("misu_amt"))
    jungsan = _to_int_amt(data.get("jungsan_amt"))
    doc = {
        "name": data.get("ipju_nm") or "",
        "addr": data.get("addr") or "",
        "hosu": data.get("hosu") or "",
        "key_note": "",
        "broker_note": "",
        "move_after": "",
        "tel": data.get("tel") or "",
        "bojung_disp": money(data.get("bojung_amt")),
        "rent_manage_label": data.get("rent_manage_label") or "",
        "ipju_dt": data.get("ipju_dt") or "",
        "out_dt": data.get("out_dt") or "",
        "etc_note": "",
    }
    fac = {k: "" for k in (
        "door", "kitchen", "wallpaper", "floor", "light", "window",
        "bath", "boiler", "key2", "contract", "clean", "convert_amt",
    )}
    rent = {
        "period": data.get("period_label") or "",
        "repair_amt": money(data.get("suri_amt")) if _to_int_amt(data.get("suri_amt")) else "",
        "base_total": money(data.get("ipkum_gijun")),
        "paid_total": money(data.get("sukum_tot")),
        "refundable": money(data.get("h_amt")),
        "unpaid": money(data.get("misu_amt")),
    }
    payments = [
        {"dt": p.get("dt") or "", "amt": p.get("amt_disp") or money(p.get("amt"))}
        for p in (data.get("payments") or [])
    ]
    util = {
        "elec": money(data.get("elec_amt")) if _to_int_amt(data.get("elec_amt")) else "",
        "water": money(data.get("sudo_amt")) if _to_int_amt(data.get("sudo_amt")) else "",
        "restore": money(data.get("sisul_amt")) if _to_int_amt(data.get("sisul_amt")) else "",
        "gas": money(data.get("gas_amt")) if _to_int_amt(data.get("gas_amt")) else "",
        "etc": money(data.get("gita_amt")) if _to_int_amt(data.get("gita_amt")) else "",
        "total": money(util_tot) if util_tot else "",
    }
    settle = {
        "amount": money(jungsan),
        "formula_nums": f"({money(h)} - {money(misu)} - {money(util_tot) or '0'})",
        "confirm_year": str(date.today().year),
        "confirm_month": "",
        "confirm_day": "",
        "confirmer": session.get("s_name") or "",
    }
    return doc, fac, rent, payments, util, settle


@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    """퇴실 정산 관리 (XP). 조회·저장·인쇄."""
    today = date.today().isoformat()

    def _form_from_req(src):
        return {
            "out_dt": (src.get("out_dt") or today).strip()[:10],
            "bunji1": _pad_bunji(src.get("bunji1")),
            "bunji2": _pad_bunji(src.get("bunji2")),
            "hosu": (src.get("hosu") or "").strip().upper(),
            "ipju_seq": (src.get("ipju_seq") or "").strip(),
            "suri": (src.get("suri") or "0").strip(),
            "elec": (src.get("elec") or "0").strip(),
            "water": (src.get("water") or "0").strip(),
            "restore": (src.get("restore") or "0").strip(),
            "gas": (src.get("gas") or "0").strip(),
            "etc": (src.get("etc") or "0").strip(),
        }

    if request.method == "POST":
        action = (request.form.get("action") or "calc").strip()
        f = _form_from_req(request.form)
        extra = {
            "suri": f["suri"],
            "elec": f["elec"],
            "water": f["water"],
            "restore": f["restore"],
            "gas": f["gas"],
            "etc": f["etc"],
        }

        if action == "new":
            return redirect(url_for("checkout"))

        data = _checkout_build(
            f["bunji1"], f["bunji2"], f["hosu"], f["ipju_seq"], f["out_dt"], extra
        )
        if data.get("error"):
            flash(data["error"], "err")
            return redirect(url_for("checkout", **{k: v for k, v in f.items() if v}))

        if action == "save":
            uid = session.get("sabun") or ""
            out_d = data["out_dt"]
            # out_seq
            mx = db.query_one(
                """
                SELECT MAX(CAST(out_seq AS UNSIGNED)) AS m FROM bd07_out
                WHERE out_dt=%s AND bunji1=%s AND bunji2=%s AND hosu=%s AND ipju_seq=%s
                """,
                (out_d, data["bunji1"], data["bunji2"], data["hosu"], data["ipju_seq"]),
            )
            out_seq = str(int((mx or {}).get("m") or 0) + 1).zfill(2)
            try:
                # 기존 건 있으면 갱신
                exists = db.query_one(
                    """
                    SELECT out_seq FROM bd07_out
                    WHERE bunji1=%s AND bunji2=%s AND hosu=%s AND ipju_seq=%s
                      AND out_dt=%s
                    ORDER BY out_seq DESC LIMIT 1
                    """,
                    (
                        data["bunji1"],
                        data["bunji2"],
                        data["hosu"],
                        data["ipju_seq"],
                        out_d,
                    ),
                )
                if exists:
                    out_seq = exists["out_seq"]
                    db.execute(
                        """
                        UPDATE bd07_out SET
                          gijun_dt=%s, ipju_mm_cnt=%s, ipju_dd_cnt=%s,
                          ipkum_gijun=%s, sukum_tot=%s, h_amt=%s,
                          elec_amt=%s, sudo_amt=%s, sisul_amt=%s, gas_amt=%s, gita_amt=%s,
                          jungsan_amt=%s, g_suri_tot=%s,
                          g_napbu_gb=%s, g_ipju_dt=%s, g_ipju_nm=%s,
                          g_ipju_jumin_no=%s, g_ipju_tel=%s,
                          g_bojung_amt=%s, g_yechi_amt=%s, g_rent_amt=%s, g_manage_amt=%s,
                          uid=%s, sys_dt=NOW()
                        WHERE out_dt=%s AND out_seq=%s AND bunji1=%s AND bunji2=%s
                          AND hosu=%s AND ipju_seq=%s
                        """,
                        (
                            out_d,
                            data["mm"],
                            data["dd"],
                            data["ipkum_gijun"],
                            data["sukum_tot"],
                            data["h_amt"],
                            data["elec_amt"],
                            data["sudo_amt"],
                            data["sisul_amt"],
                            data["gas_amt"],
                            data["gita_amt"],
                            data["jungsan_amt"],
                            data["suri_amt"],
                            data["napbu_gb"],
                            data["ipju_dt"] or None,
                            data["ipju_nm"],
                            data["jumin"][:15],
                            data["tel"][:50],
                            data["bojung_amt"],
                            data["yechi_amt"],
                            data["rent_amt"],
                            data["manage_amt"],
                            uid,
                            out_d,
                            out_seq,
                            data["bunji1"],
                            data["bunji2"],
                            data["hosu"],
                            data["ipju_seq"],
                        ),
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO bd07_out (
                          out_dt, out_seq, gijun_dt, bunji1, bunji2, hosu, ipju_seq,
                          ipju_mm_cnt, ipju_dd_cnt, ipkum_gijun, sukum_tot, h_amt,
                          elec_amt, sudo_amt, sisul_amt, gas_amt, gita_amt, jungsan_amt,
                          g_suri_tot, g_napbu_gb, g_ipju_dt, g_ipju_nm, g_ipju_jumin_no,
                          g_ipju_tel, g_bojung_amt, g_yechi_amt, g_rent_amt, g_manage_amt,
                          uid, sys_dt
                        ) VALUES (
                          %s,%s,%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,
                          %s,NOW()
                        )
                        """,
                        (
                            out_d,
                            out_seq,
                            out_d,
                            data["bunji1"],
                            data["bunji2"],
                            data["hosu"],
                            data["ipju_seq"],
                            data["mm"],
                            data["dd"],
                            data["ipkum_gijun"],
                            data["sukum_tot"],
                            data["h_amt"],
                            data["elec_amt"],
                            data["sudo_amt"],
                            data["sisul_amt"],
                            data["gas_amt"],
                            data["gita_amt"],
                            data["jungsan_amt"],
                            data["suri_amt"],
                            data["napbu_gb"],
                            data["ipju_dt"] or None,
                            data["ipju_nm"],
                            data["jumin"][:15],
                            data["tel"][:50],
                            data["bojung_amt"],
                            data["yechi_amt"],
                            data["rent_amt"],
                            data["manage_amt"],
                            uid,
                        ),
                    )
                # 입주 이력 퇴실일 반영
                db.execute(
                    """
                    UPDATE bd03_det
                    SET out_dt=%s, out_seq=%s, out_jungsan_end=%s, sys_dt=NOW(), uid=%s
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                    """,
                    (
                        out_d,
                        out_seq,
                        data["jungsan_amt"],
                        uid,
                        data["bunji1"],
                        data["bunji2"],
                        data["hosu"],
                        data["ipju_seq"],
                    ),
                )
                flash(
                    f"퇴실 정산을 저장했습니다. ({data['ipju_nm']} · 정산 {money(data['jungsan_amt'])}원)",
                    "ok",
                )
            except Exception as e:
                flash(f"저장 실패: {e}", "err")
            return redirect(
                url_for(
                    "checkout",
                    bunji1=fmt_bunji(data["bunji1"]),
                    bunji2=fmt_bunji(data["bunji2"]),
                    hosu=data["hosu"],
                    ipju_seq=data["ipju_seq"],
                    out_dt=data["out_dt"],
                )
            )

        # action=calc → 같은 화면 재표시 (GET으로)
        return redirect(
            url_for(
                "checkout",
                bunji1=fmt_bunji(f["bunji1"]) if f["bunji1"] else "",
                bunji2=fmt_bunji(f["bunji2"]) if f["bunji2"] else "",
                hosu=f["hosu"],
                ipju_seq=f["ipju_seq"],
                out_dt=f["out_dt"],
                suri=f["suri"],
                elec=f["elec"],
                water=f["water"],
                restore=f["restore"],
                gas=f["gas"],
                etc=f["etc"],
            )
        )

    # GET
    f = _form_from_req(request.args)
    if not f["out_dt"]:
        f["out_dt"] = today
    data = None
    if f["bunji1"] and f["bunji2"] and f["hosu"]:
        extra = {
            "suri": f["suri"],
            "elec": f["elec"],
            "water": f["water"],
            "restore": f["restore"],
            "gas": f["gas"],
            "etc": f["etc"],
        }
        data = _checkout_build(
            f["bunji1"], f["bunji2"], f["hosu"], f["ipju_seq"], f["out_dt"], extra
        )
        if data.get("error"):
            flash(data["error"], "err")
            data = None
        elif data:
            f["ipju_seq"] = data.get("ipju_seq") or f["ipju_seq"]

    return render_template(
        "checkout.html",
        form=f,
        data=data,
        building_label=_building_label(f["bunji1"], f["bunji2"])
        if f["bunji1"] and f["bunji2"]
        else "",
    )


@app.route("/checkout/list", methods=["GET", "POST"])
@login_required
def checkout_list():
    """
    퇴실(예정)자 조회 및 변경처리 (XP「호수 퇴실자 내역 조회」).
    목록 조회 + 현입주자로 변환(퇴실일 취소).
    """
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        b1 = _pad_bunji(request.form.get("bunji1"))
        b2 = _pad_bunji(request.form.get("bunji2"))
        hosu = (request.form.get("hosu") or "").strip().upper()
        seq = (request.form.get("ipju_seq") or "").strip()
        if seq.isdigit():
            seq = seq.zfill(2)
        keep = {
            "bunji1": fmt_bunji(b1) if b1 else request.form.get("q_bunji1") or "",
            "bunji2": fmt_bunji(b2) if b2 else request.form.get("q_bunji2") or "",
            "hosu": (request.form.get("q_hosu") or request.form.get("hosu") or "").strip().upper(),
            "name": (request.form.get("q_name") or request.form.get("name") or "").strip(),
            "mode": request.form.get("mode") or "all",
        }
        if action == "restore":
            if not (b1 and b2 and hosu and seq):
                flash("변환할 행을 선택하세요.", "err")
                return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))
            # 같은 호에 이미 현거주가 있으면 차단
            other = db.query_one(
                f"""
                SELECT ipju_seq, ipju_nm FROM bd03_det
                WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                  AND ipju_seq<>%s
                  AND {_CURRENT_TENANT_SQL.replace('d.', '')}
                LIMIT 1
                """,
                (b1, b2, hosu, seq),
            )
            if other:
                flash(
                    f"같은 호에 현거주자({other.get('ipju_nm') or other.get('ipju_seq')})가 있어 "
                    "현입주자로 변환할 수 없습니다.",
                    "err",
                )
                return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))
            try:
                n = db.execute(
                    """
                    UPDATE bd03_det
                    SET out_dt=NULL, out_seq=NULL, plan_out_dt=NULL,
                        sys_dt=NOW(), uid=%s
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                    """,
                    (session.get("sabun") or "", b1, b2, hosu, seq),
                )
                if n:
                    flash("현입주자로 변환했습니다. (퇴실·예정일 취소)", "ok")
                else:
                    flash("대상 이력을 찾지 못했습니다.", "err")
            except Exception as e:
                flash(f"변환 실패: {e}", "err")
            return redirect(url_for("checkout_list", q=1, **{k: v for k, v in keep.items() if v}))

        return redirect(url_for("checkout_list"))

    # GET
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    name = (request.args.get("name") or "").strip()
    mode = (request.args.get("mode") or "all").strip().lower()
    if mode not in ("all", "out", "plan"):
        mode = "all"
    per_page = 30
    try:
        page = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    # 최소 조건: 주소 · 호수 · 이름 중 하나 이상
    has_min_filter = bool(bunji1 or hosu or name)
    want_query = "q" in request.args
    ran = want_query and has_min_filter
    empty_msg = ""
    if want_query and not has_min_filter:
        empty_msg = "주소 · 호수 · 이름 중 하나 이상 입력한 뒤 조회하세요."

    results = []
    total = 0
    total_pages = 1
    if ran:
        where = ["1=1"]
        args = []
        if mode == "out":
            where.append("(out_dt IS NOT NULL AND out_dt >= '1000-01-01')")
        elif mode == "plan":
            where.append(
                "(plan_out_dt IS NOT NULL AND plan_out_dt >= '1000-01-01') "
                "AND (out_dt IS NULL OR out_dt < '1000-01-01')"
            )
        else:
            where.append(
                "((out_dt IS NOT NULL AND out_dt >= '1000-01-01') "
                "OR (plan_out_dt IS NOT NULL AND plan_out_dt >= '1000-01-01'))"
            )
        if bunji1:
            where.append("bunji1=%s")
            args.append(bunji1)
        if bunji2:
            where.append("bunji2=%s")
            args.append(bunji2)
        if hosu:
            where.append("UPPER(TRIM(hosu))=%s")
            args.append(hosu)
        if name:
            where.append("ipju_nm LIKE %s")
            args.append(f"%{name}%")
        where_sql = " AND ".join(where)

        cnt = db.query_one(
            f"SELECT COUNT(*) AS c FROM bd03_det WHERE {where_sql}",
            args,
        )
        total = int((cnt or {}).get("c") or 0)
        total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * per_page

        rows = db.query(
            f"""
            SELECT bunji1, bunji2, hosu, ipju_seq, ipju_nm, ipju_jumin_no,
                   ipju_dt, out_dt, plan_out_dt, bojung_amt, rent_amt, manage_amt, napbu_gb
            FROM bd03_det
            WHERE {where_sql}
            ORDER BY COALESCE(out_dt, plan_out_dt) DESC, bunji1, bunji2, hosu
            LIMIT %s OFFSET %s
            """,
            args + [per_page, offset],
        )
        for r in rows:
            has_out = r.get("out_dt") and (
                not isinstance(r["out_dt"], datetime) or r["out_dt"].year >= 1000
            )
            has_plan = r.get("plan_out_dt") and (
                not isinstance(r["plan_out_dt"], datetime) or r["plan_out_dt"].year >= 1000
            )
            if has_out:
                kind = "퇴실"
                kind_dt = r.get("out_dt")
            elif has_plan:
                kind = "퇴실예정"
                kind_dt = r.get("plan_out_dt")
            else:
                kind = "—"
                kind_dt = None
            results.append(
                {
                    **r,
                    "kind": kind,
                    "kind_dt": kind_dt,
                    # 퇴실일 / 퇴실예정일 분리 표시 (한 칸에 합치지 않음)
                    "out_dt_disp": fmt_date(r.get("out_dt")) if has_out else "",
                    "plan_out_dt_disp": fmt_date(r.get("plan_out_dt"))
                    if has_plan
                    else "",
                    "jumin_disp": mask_jumin(r.get("ipju_jumin_no")),
                }
            )

    building_label = (
        _building_label(bunji1, bunji2) if bunji1 and bunji2 else ""
    )

    # 결과 0건일 때 원인 구분 (주소/호수/이력)
    if ran and total == 0:
        mode_lab = {"all": "퇴실·예정", "out": "퇴실", "plan": "퇴실예정"}.get(
            mode, "퇴실·예정"
        )
        if bunji1 and bunji2:
            bld = db.query_one(
                "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
                (bunji1, bunji2),
            )
            if not bld:
                empty_msg = "등록되지 않은 주소입니다. 주소를 확인하세요."
            elif hosu:
                room = db.query_one(
                    """
                    SELECT hosu FROM bd03_m
                    WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
                    """,
                    (bunji1, bunji2, hosu),
                )
                if not room:
                    empty_msg = "등록되지 않은 호수입니다. 호수를 확인하세요."
                else:
                    empty_msg = f"해당 호수의 {mode_lab} 이력이 없습니다."
            else:
                empty_msg = f"해당 주소의 {mode_lab} 이력이 없습니다."
        elif bunji1 and not bunji2:
            empty_msg = f"주소 조건에 맞는 {mode_lab} 이력이 없습니다."
        elif hosu:
            empty_msg = f"호수 조건에 맞는 {mode_lab} 이력이 없습니다."
        elif name:
            empty_msg = f"「{name}」 이름에 맞는 {mode_lab} 이력이 없습니다."
        else:
            empty_msg = f"조건에 맞는 {mode_lab} 이력이 없습니다."

    pager = _build_pager(page, total_pages, page_block_size=6)
    pager["total"] = total
    pager["per_page"] = per_page

    return render_template(
        "checkout_list.html",
        filters={
            "bunji1": bunji1,
            "bunji2": bunji2,
            "hosu": hosu,
            "name": name,
            "mode": mode,
            "page": page,
        },
        results=results,
        ran=ran,
        want_query=want_query,
        building_label=building_label,
        empty_msg=empty_msg,
        pager=pager,
    )


@app.route("/checkout/print")
@login_required
def checkout_print():
    """계약 해지 인쇄 — 퇴실 정산 데이터 바인딩."""
    f = {
        "out_dt": (request.args.get("out_dt") or date.today().isoformat())[:10],
        "bunji1": _pad_bunji(request.args.get("bunji1")),
        "bunji2": _pad_bunji(request.args.get("bunji2")),
        "hosu": (request.args.get("hosu") or "").strip().upper(),
        "ipju_seq": (request.args.get("ipju_seq") or "").strip(),
        "suri": request.args.get("suri") or "0",
        "elec": request.args.get("elec") or "0",
        "water": request.args.get("water") or "0",
        "restore": request.args.get("restore") or "0",
        "gas": request.args.get("gas") or "0",
        "etc": request.args.get("etc") or "0",
    }
    data = _checkout_build(
        f["bunji1"],
        f["bunji2"],
        f["hosu"],
        f["ipju_seq"],
        f["out_dt"],
        {
            "suri": f["suri"],
            "elec": f["elec"],
            "water": f["water"],
            "restore": f["restore"],
            "gas": f["gas"],
            "etc": f["etc"],
        },
    )
    if not data or data.get("error"):
        flash(data.get("error") if data else "인쇄할 데이터가 없습니다.", "err")
        return redirect(url_for("checkout"))
    doc, fac, rent, payments, util, settle = _checkout_to_print(data)
    return render_template(
        "contract_cancel_print.html",
        doc=doc,
        fac=fac,
        rent=rent,
        payments=payments,
        util=util,
        settle=settle,
    )


@app.route("/print/contract-cancel")
@app.route("/print/contract-cancel/sample")
@login_required
def contract_cancel_print_sample():
    """샘플 계약 해지 인쇄 (강현숙 PDF 수치)."""
    return redirect(
        url_for(
            "checkout_print",
            bunji1="1139",
            bunji2="4",
            hosu="707",
            ipju_seq="12",
            out_dt="2026-08-11",
        )
    )
