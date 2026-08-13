"""입주자관리 화면.

입주 이력 등록/수정/삭제(레거시 「입주 이력 등록」 창)와, 화면에서 자바스크립트가
호출하는 입주 순번·이력 조회 API(`/api/next_ipju_seq`, `/api/tenant_load`) 라우트를
모아둔 모듈입니다.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import flash, jsonify, render_template, request, session

import db
from app_instance import app
from utils import (
    CURRENT_TENANT_SQL as _CURRENT_TENANT_SQL,
    building_label as _building_label,
    buildings_and_rooms as _buildings_and_rooms,
    clamp_date_str,
    fmt_date,
    login_required,
    pad_bunji as _pad_bunji,
    parse_bunji_input as _parse_bunji_input,
    tenant_is_past_out as _tenant_is_past_out,
)


def _empty_tenant_form():
    today = date.today().isoformat()
    return {
        "bunji1": "",
        "bunji2": "",
        "hosu": "",
        "ipju_seq": "",
        "ipju_nm": "",
        "ipju_tel3": "",  # 연락처 일반
        "ipju_tel1": "",  # 휴대1
        "ipju_tel2": "",  # 휴대2
        "ipju_dt": today,
        "jumin1": "",
        "jumin2": "",
        "bojung_amt": "0",
        "rent_amt": "0",
        "manage_amt": "0",
        "yechi_amt": "0",
        "napbu_gb": "B",  # B=후납 (레거시 기본)
        "ipju_gb": "A",
        "mode": "new",  # new | edit
        "out_dt": "",
        "tenant_status": "new",  # current | past | new
    }


def _coerce_amount_to_str(v, default="0"):
    if v is None:
        return default
    if isinstance(v, (int, float, Decimal)):
        try:
            return str(int(v))
        except (ValueError, TypeError, InvalidOperation):
            return default
    s = str(v).strip()
    if not s:
        return default
    try:
        return str(int(Decimal(s.replace(",", "").replace(" ", ""))))
    except (ValueError, TypeError, InvalidOperation):
        return default


def _tenant_search_query(form):
    for key in ("ipju_nm", "ipju_tel1", "ipju_tel2", "ipju_tel3"):
        value = (form.get(key) or "").strip()
        if value:
            return value
    bunji1 = (form.get("bunji1") or "").strip()
    bunji2 = (form.get("bunji2") or "").strip()
    hosu = (form.get("hosu") or "").strip()
    if bunji1 and bunji2:
        return f"{bunji1}-{bunji2}"
    if hosu:
        return hosu
    return ""


def _tenant_status_from_row(row) -> str:
    """current=현세입자, past=퇴실자, new=신규."""
    if not row:
        return "new"
    if _tenant_is_past_out(row.get("out_dt")):
        return "past"
    return "current"


def _tenant_form_from_row(row):
    form = _empty_tenant_form()
    if not row:
        return form
    jumin = (row.get("ipju_jumin_no") or "").strip()
    jumin = re.sub(r"\D", "", jumin)
    out_s = fmt_date(row.get("out_dt")) or ""
    if out_s and out_s.startswith("0"):
        # 0000-00-00 류는 공백
        try:
            if int(out_s[:4]) < 1000:
                out_s = ""
        except ValueError:
            pass
    form.update(
        {
            "bunji1": row.get("bunji1") or "",
            "bunji2": row.get("bunji2") or "",
            "hosu": (row.get("hosu") or "").strip(),
            "ipju_seq": str(row.get("ipju_seq") or "").strip().zfill(2)
            if str(row.get("ipju_seq") or "").strip()
            else "",
            "ipju_nm": (row.get("ipju_nm") or "").strip(),
            "ipju_tel3": (row.get("ipju_tel3") or "").strip(),
            "ipju_tel1": (row.get("ipju_tel1") or "").strip(),
            "ipju_tel2": (row.get("ipju_tel2") or "").strip(),
            "ipju_dt": fmt_date(row.get("ipju_dt")) or date.today().isoformat(),
            "out_dt": out_s,
            "jumin1": jumin[:6] if jumin else "",
            "jumin2": jumin[6:13] if len(jumin) > 6 else (jumin[6:] if jumin else ""),
            "bojung_amt": _coerce_amount_to_str(row.get("bojung_amt")),
            "rent_amt": _coerce_amount_to_str(row.get("rent_amt")),
            "manage_amt": _coerce_amount_to_str(row.get("manage_amt")),
            "yechi_amt": _coerce_amount_to_str(row.get("yechi_amt")),
            "napbu_gb": (row.get("napbu_gb") or "B").strip() or "B",
            "ipju_gb": (row.get("ipju_gb") or "A").strip() or "A",
            "mode": "edit",
            "tenant_status": _tenant_status_from_row(row),
        }
    )
    return form


def _next_ipju_seq(bunji1, bunji2, hosu):
    """해당 호실의 다음 입주 순번 (max+1, 없으면 01)."""
    hosu = (hosu or "").strip().upper()
    row = db.query_one(
        """
        SELECT MAX(CAST(ipju_seq AS UNSIGNED)) AS mx
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
        """,
        (bunji1, bunji2, hosu),
    )
    mx = int((row or {}).get("mx") or 0)
    return str(mx + 1).zfill(2)


def _current_ipju_seq(bunji1, bunji2, hosu):
    """현세입자 순번. 없으면 빈 문자열."""
    b1, b2 = _pad_bunji(bunji1), _pad_bunji(bunji2)
    h = (hosu or "").strip().upper()
    if not (b1 and b2 and h):
        return ""
    row = db.query_one(
        f"""
        SELECT ipju_seq FROM bd03_det d
        WHERE d.bunji1=%s AND d.bunji2=%s AND UPPER(TRIM(d.hosu))=%s
          AND {_CURRENT_TENANT_SQL}
        ORDER BY CAST(d.ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (b1, b2, h),
    )
    if not row:
        return ""
    s = str(row.get("ipju_seq") or "").strip()
    return s.zfill(2) if s.isdigit() else s


def _clamp_new_ipju_seq(bunji1, bunji2, hosu, seq):
    """신규 입력용 순번 보정.
    요청 순번이 (현세입자 다음 = max+1) 보다 크면 다음 순번으로 맞춤.
    반환: (보정된순번, 보정여부)
    """
    next_s = _next_ipju_seq(bunji1, bunji2, hosu)
    s = str(seq or "").strip()
    if not s:
        return next_s, True
    digits = re.sub(r"\D", "", s)
    if not digits:
        return next_s, True
    sn = int(digits)
    nn = int(next_s)
    if sn > nn:
        return next_s, True
    # 2자리 표기 (99 초과 드묾)
    return str(sn).zfill(2) if sn < 100 else str(sn), False


def _form_from_tenant_request(src):
    """request.form / request.args → form dict"""
    form = _empty_tenant_form()
    bunji_raw = (src.get("bunji") or "").strip()
    if bunji_raw:
        b1, b2 = _parse_bunji_input(bunji_raw)
    else:
        b1 = _pad_bunji((src.get("bunji1") or "").strip())
        b2 = _pad_bunji((src.get("bunji2") or "").strip())
    form["bunji1"] = b1
    form["bunji2"] = b2
    form["hosu"] = (src.get("hosu") or "").strip().upper()
    seq = (src.get("ipju_seq") or "").strip()
    form["ipju_seq"] = seq.zfill(2) if seq.isdigit() else seq
    form["ipju_nm"] = (src.get("ipju_nm") or "").strip()
    form["ipju_tel3"] = (src.get("ipju_tel3") or "").strip()
    form["ipju_tel1"] = (src.get("ipju_tel1") or "").strip()
    form["ipju_tel2"] = (src.get("ipju_tel2") or "").strip()
    form["ipju_dt"] = clamp_date_str((src.get("ipju_dt") or "").strip()) or date.today().isoformat()
    form["jumin1"] = re.sub(r"\D", "", (src.get("jumin1") or ""))[:6]
    form["jumin2"] = re.sub(r"\D", "", (src.get("jumin2") or ""))[:7]
    form["bojung_amt"] = (src.get("bojung_amt") or "0").replace(",", "").strip() or "0"
    form["rent_amt"] = (src.get("rent_amt") or "0").replace(",", "").strip() or "0"
    form["manage_amt"] = (src.get("manage_amt") or "0").replace(",", "").strip() or "0"
    form["yechi_amt"] = (src.get("yechi_amt") or "0").replace(",", "").strip() or "0"
    nap = (src.get("napbu_gb") or "B").strip().upper()
    form["napbu_gb"] = nap if nap in ("A", "B") else "B"
    form["mode"] = (src.get("mode") or "new").strip() or "new"
    return form


@app.route("/tenants/manage", methods=["GET", "POST"])
@login_required
def tenant_manage():
    """입주자관리 · 입주 이력 등록/수정 (레거시 「입주 이력 등록」 창)"""
    buildings, rooms = _buildings_and_rooms()

    if request.method == "GET":
        form = _empty_tenant_form()
        # URL 로 기존 건 로드
        b1 = _pad_bunji((request.args.get("bunji1") or "").strip())
        b2 = _pad_bunji((request.args.get("bunji2") or "").strip())
        if request.args.get("bunji"):
            b1, b2 = _parse_bunji_input(request.args.get("bunji"))
        hosu = (request.args.get("hosu") or "").strip().upper()
        seq = (request.args.get("ipju_seq") or "").strip()
        if seq.isdigit():
            seq = seq.zfill(2)
        if b1 and b2 and hosu and seq:
            row = _lookup_tenant_row(b1, b2, hosu, seq)
            if row:
                form = _tenant_form_from_row(row)
            else:
                form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"] = (
                    b1,
                    b2,
                    hosu,
                    seq,
                )
                form["mode"] = "new"
        elif b1 and b2:
            form["bunji1"], form["bunji2"] = b1, b2
            if hosu:
                form["hosu"] = hosu
                # 호수만 지정: 다음 순번 신규가 아니라 현재(또는 최신) 이력 표시
                row = _lookup_tenant_row(b1, b2, hosu)
                if row:
                    form = _tenant_form_from_row(row)
                else:
                    form["ipju_seq"] = _next_ipju_seq(b1, b2, hosu)
                    form["mode"] = "new"
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=_building_label(form["bunji1"], form["bunji2"]),
        )

    action = (request.form.get("action") or "save").strip()
    form = _form_from_tenant_request(request.form)
    building_label = _building_label(form["bunji1"], form["bunji2"])

    if action == "new":
        # 키(주소·호수)만 남기고 신규
        keep_b1, keep_b2, keep_h = form["bunji1"], form["bunji2"], form["hosu"]
        form = _empty_tenant_form()
        form["bunji1"], form["bunji2"], form["hosu"] = keep_b1, keep_b2, keep_h
        if keep_b1 and keep_b2 and keep_h:
            form["ipju_seq"] = _next_ipju_seq(keep_b1, keep_b2, keep_h)
        flash("신규 입력 모드입니다. 저장하면 새 순번으로 등록됩니다.", "ok")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=_building_label(form["bunji1"], form["bunji2"]),
        )

    if action == "delete":
        if not (
            form["bunji1"]
            and form["bunji2"]
            and form["hosu"]
            and form["ipju_seq"]
        ):
            flash("삭제할 주소·호수·순번을 확인하세요.", "err")
            return render_template(
                "tenant_form.html",
                form=form,
                buildings=buildings,
                rooms=rooms,
                building_label=building_label,
            )
        # 퇴실자 이력 삭제 금지
        del_row = _lookup_tenant_row(
            form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]
        )
        if not del_row:
            flash("삭제할 이력을 찾지 못했습니다.", "err")
            return render_template(
                "tenant_form.html",
                form=form,
                buildings=buildings,
                rooms=rooms,
                building_label=building_label,
            )
        if _tenant_is_past_out(del_row.get("out_dt")):
            flash(
                "퇴실 완료된 이력은 삭제할 수 없습니다. (퇴실자)",
                "err",
            )
            form = _tenant_form_from_row(del_row)
            return render_template(
                "tenant_form.html",
                form=form,
                buildings=buildings,
                rooms=rooms,
                building_label=_building_label(form["bunji1"], form["bunji2"]),
                popup_msg="퇴실 완료된 이력은 삭제할 수 없습니다.",
                popup_type="err",
            )
        n = db.execute(
            """
            DELETE FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
              AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            """,
            (form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]),
        )
        if n:
            flash(f"입주 이력(순번 {form['ipju_seq']})이 삭제되었습니다.", "ok")
            keep_b1, keep_b2, keep_h = form["bunji1"], form["bunji2"], form["hosu"]
            form = _empty_tenant_form()
            form["bunji1"], form["bunji2"], form["hosu"] = keep_b1, keep_b2, keep_h
            if keep_h:
                form["ipju_seq"] = _next_ipju_seq(keep_b1, keep_b2, keep_h)
            return render_template(
                "tenant_form.html",
                form=form,
                buildings=buildings,
                rooms=rooms,
                building_label=_building_label(form["bunji1"], form["bunji2"]),
                popup_msg="삭제되었습니다.",
                popup_type="ok",
            )
        flash("삭제할 이력을 찾지 못했습니다.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=_building_label(form["bunji1"], form["bunji2"]),
        )

    # ── 저장 ──
    if not (form["bunji1"] and form["bunji2"] and form["hosu"]):
        flash("주소와 호수는 필수입니다.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )
    if not form["ipju_nm"]:
        flash("성명을 입력하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )
    # 건물·호수 존재
    b = db.query_one(
        "SELECT bunji1 FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (form["bunji1"], form["bunji2"]),
    )
    if not b:
        flash("등록되지 않은 주소입니다. 건물 내역을 먼저 등록하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )
    room = db.query_one(
        """
        SELECT hosu FROM bd03_m
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
        """,
        (form["bunji1"], form["bunji2"], form["hosu"]),
    )
    if not room:
        flash("등록되지 않은 호수입니다. 호수 내역을 먼저 등록하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )

    if not form["ipju_seq"]:
        form["ipju_seq"] = _next_ipju_seq(
            form["bunji1"], form["bunji2"], form["hosu"]
        )

    # 신규 INSERT 대상인지 미리 확인 후, 순번이 max+1 초과면 보정
    pre_exists = db.query_one(
        """
        SELECT ipju_seq FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s
          AND CAST(ipju_seq AS UNSIGNED)=CAST(%s AS UNSIGNED)
        """,
        (form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]),
    )
    if not pre_exists:
        clamped, did_clamp = _clamp_new_ipju_seq(
            form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]
        )
        if did_clamp:
            form["ipju_seq"] = clamped
            flash(
                f"신규 순번이 너무 커서 「다음 순번 {clamped}」으로 맞췄습니다.",
                "ok",
            )

    try:
        bojung = int(form["bojung_amt"] or 0)
        rent = int(form["rent_amt"] or 0)
        manage = int(form["manage_amt"] or 0)
        yechi = int(form["yechi_amt"] or 0)
    except ValueError:
        flash("금액은 숫자로 입력하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
        )

    # 보증금 · 예치금 동시 입력 불가
    if bojung > 0 and yechi > 0:
        flash("보증금과 예치금은 함께 입력할 수 없습니다. 하나만 입력하세요.", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=building_label,
            popup_msg="보증금과 예치금은 함께 입력할 수 없습니다.",
            popup_type="err",
        )

    jumin = (form["jumin1"] + form["jumin2"])[:13]
    ipju_dt = form["ipju_dt"] + " 00:00:00"
    uid = (session.get("sabun") or "")[:5]

    exists = db.query_one(
        """
        SELECT ipju_seq, ipju_nm, ipju_jumin_no, ipju_dt, out_dt
        FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s
          AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
        """,
        (form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]),
    )
    was_insert = not exists

    def _saved_snapshot():
        """저장 직후 하단 결과 박스용."""
        b1d = form["bunji1"]
        b2d = form["bunji2"]
        try:
            b1s = str(int(re.sub(r"\D", "", str(b1d)) or "0"))
            b2s = str(int(re.sub(r"\D", "", str(b2d)) or "0"))
        except ValueError:
            b1s, b2s = str(b1d), str(b2d)
        return {
            "action": "insert" if was_insert else "update",
            "bunji": f"{b1s}-{b2s}",
            "hosu": form["hosu"],
            "ipju_seq": form["ipju_seq"],
            "ipju_nm": form["ipju_nm"],
            "ipju_dt": form.get("ipju_dt") or "",
            "ipju_tel1": form.get("ipju_tel1") or "",
            "ipju_tel2": form.get("ipju_tel2") or "",
            "ipju_tel3": form.get("ipju_tel3") or "",
            "bojung_amt": bojung,
            "rent_amt": rent,
            "manage_amt": manage,
            "yechi_amt": yechi,
            "napbu_gb": form.get("napbu_gb") or "B",
            "napbu_label": "선납" if (form.get("napbu_gb") or "") == "A" else "후납",
        }

    try:
        if exists:
            # 퇴실자 이력은 조회만 — 수정 저장 불가
            if _tenant_is_past_out(exists.get("out_dt")):
                flash("퇴실 완료된 이력은 수정할 수 없습니다. (조회 전용)", "err")
                full = _lookup_tenant_row(
                    form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]
                )
                if full:
                    form = _tenant_form_from_row(full)
                return render_template(
                    "tenant_form.html",
                    form=form,
                    buildings=buildings,
                    rooms=rooms,
                    building_label=building_label,
                    popup_msg="퇴실 완료된 이력은 수정할 수 없습니다.",
                    popup_type="err",
                )
            db.execute(
                """
                UPDATE bd03_det SET
                  ipju_nm=%s, ipju_jumin_no=%s,
                  ipju_tel1=%s, ipju_tel2=%s, ipju_tel3=%s,
                  ipju_dt=%s, bojung_amt=%s, rent_amt=%s,
                  manage_amt=%s, yechi_amt=%s, napbu_gb=%s,
                  sys_dt=NOW(), uid=%s
                WHERE bunji1=%s AND bunji2=%s
                  AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
                """,
                (
                    form["ipju_nm"],
                    jumin,
                    form["ipju_tel1"],
                    form["ipju_tel2"],
                    form["ipju_tel3"],
                    ipju_dt,
                    bojung,
                    rent,
                    manage,
                    yechi,
                    form["napbu_gb"],
                    uid,
                    form["bunji1"],
                    form["bunji2"],
                    form["hosu"],
                    form["ipju_seq"],
                ),
            )
            # 성공 안내는 popup_msg 한 번만 (flash 중복 방지)
        else:
            db.execute(
                """
                INSERT INTO bd03_det (
                  bunji1, bunji2, hosu, ipju_seq, ipju_gb, ipju_dt, ipju_nm,
                  ipju_jumin_no, ipju_tel1, ipju_tel2, ipju_tel3,
                  plan_out_dt, out_dt, out_seq,
                  bojung_amt, rent_amt, manage_amt, yechi_amt, napbu_gb,
                  misu_tot, suri_tot, out_jungsan_end, del_yn, sys_dt, uid
                ) VALUES (
                  %s, %s, %s, %s, 'A', %s, %s,
                  %s, %s, %s, %s,
                  NULL, NULL, '',
                  %s, %s, %s, %s, %s,
                  NULL, 0, '', 'N', NOW(), %s
                )
                """,
                (
                    form["bunji1"],
                    form["bunji2"],
                    form["hosu"],
                    form["ipju_seq"],
                    ipju_dt,
                    form["ipju_nm"],
                    jumin,
                    form["ipju_tel1"],
                    form["ipju_tel2"],
                    form["ipju_tel3"],
                    bojung,
                    rent,
                    manage,
                    yechi,
                    form["napbu_gb"],
                    uid,
                ),
            )
    except Exception as e:
        flash(f"저장 실패: {e}", "err")
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label=_building_label(form["bunji1"], form["bunji2"]),
        )

    # 예전 flash("…수정…") + flash("…조회 화면…") 잔여 메시지 비우기
    try:
        from flask import get_flashed_messages

        get_flashed_messages()
    except Exception:
        pass

    snap = _saved_snapshot()
    nm = (snap.get("ipju_nm") or "").strip()
    seq_s = snap.get("ipju_seq") or ""

    # 신규 저장: 팝업 1회 + 전체 필드 초기화 + 하단 결과 박스
    if was_insert:
        form = _empty_tenant_form()
        return render_template(
            "tenant_form.html",
            form=form,
            buildings=buildings,
            rooms=rooms,
            building_label="",
            popup_msg=f"저장되었습니다.\n{nm} · 순번 {seq_s}",
            popup_type="ok",
            last_saved=snap,
        )

    # 수정 저장: 팝업 1회 + 이력 유지 + 하단 결과 박스
    saved = _lookup_tenant_row(
        form["bunji1"], form["bunji2"], form["hosu"], form["ipju_seq"]
    )
    if saved:
        form = _tenant_form_from_row(saved)
    else:
        form["mode"] = "edit"
        form["tenant_status"] = "current"
    return render_template(
        "tenant_form.html",
        form=form,
        buildings=buildings,
        rooms=rooms,
        building_label=_building_label(form["bunji1"], form["bunji2"]),
        popup_msg=f"수정 저장되었습니다.\n{nm} · 순번 {seq_s}",
        popup_type="ok",
        last_saved=snap,
    )


def _lookup_tenant_row(bunji1, bunji2, hosu, ipju_seq=""):
    """입주 이력 1건. 순번 있으면 그 건, 없으면 현재거주 → 없으면 최신 순번."""
    b1 = _pad_bunji(bunji1)
    b2 = _pad_bunji(bunji2)
    h = (hosu or "").strip().upper()
    seq = (ipju_seq or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    if not (b1 and b2 and h):
        return None
    if seq:
        # 패딩 일치 우선 (인덱스 유리), 실패 시 CAST 폴백
        row = db.query_one(
            """
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
              AND UPPER(TRIM(hosu))=%s AND ipju_seq=%s
            LIMIT 1
            """,
            (b1, b2, h, seq),
        )
        if row:
            return row
        return db.query_one(
            """
            SELECT * FROM bd03_det
            WHERE bunji1=%s AND bunji2=%s
              AND UPPER(TRIM(hosu))=%s
              AND CAST(ipju_seq AS UNSIGNED)=CAST(%s AS UNSIGNED)
            LIMIT 1
            """,
            (b1, b2, h, seq),
        )
    # 현재 거주 우선
    row = db.query_one(
        f"""
        SELECT * FROM bd03_det d
        WHERE d.bunji1=%s AND d.bunji2=%s
          AND UPPER(TRIM(d.hosu))=%s
          AND {_CURRENT_TENANT_SQL}
        ORDER BY CAST(d.ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (b1, b2, h),
    )
    if row:
        return row
    # 공실이면 마지막 이력
    return db.query_one(
        """
        SELECT * FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s AND UPPER(TRIM(hosu))=%s
        ORDER BY CAST(ipju_seq AS UNSIGNED) DESC
        LIMIT 1
        """,
        (b1, b2, h),
    )


@app.route("/api/next_ipju_seq")
@login_required
def api_next_ipju_seq():
    bunji1 = _pad_bunji((request.args.get("bunji1") or "").strip())
    bunji2 = _pad_bunji((request.args.get("bunji2") or "").strip())
    if request.args.get("bunji"):
        bunji1, bunji2 = _parse_bunji_input(request.args.get("bunji"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    if not (bunji1 and bunji2 and hosu):
        return jsonify({"ok": False, "ipju_seq": ""})
    next_s = _next_ipju_seq(bunji1, bunji2, hosu)
    cur_s = _current_ipju_seq(bunji1, bunji2, hosu)
    req = (request.args.get("seq") or request.args.get("ipju_seq") or "").strip()
    clamped = next_s
    did_clamp = False
    if req:
        clamped, did_clamp = _clamp_new_ipju_seq(bunji1, bunji2, hosu, req)
    return jsonify(
        {
            "ok": True,
            "ipju_seq": next_s,
            "next_seq": next_s,
            "current_seq": cur_s,
            "clamped_seq": clamped,
            "did_clamp": did_clamp,
            "bunji1": bunji1,
            "bunji2": bunji2,
            "hosu": hosu,
        }
    )


@app.route("/api/tenant_load")
@login_required
def api_tenant_load():
    """주소·호수(·순번)로 입주 이력 1건 로드.
    순번 생략 시: 현재 거주자 → 없으면 최신 순번 이력.
    """
    bunji1 = _pad_bunji((request.args.get("bunji1") or "").strip())
    bunji2 = _pad_bunji((request.args.get("bunji2") or "").strip())
    if request.args.get("bunji"):
        bunji1, bunji2 = _parse_bunji_input(request.args.get("bunji"))
    hosu = (request.args.get("hosu") or "").strip().upper()
    seq = (request.args.get("ipju_seq") or "").strip()
    if seq.isdigit():
        seq = seq.zfill(2)
    if not (bunji1 and bunji2 and hosu):
        return jsonify({"ok": False, "reason": "key"})
    next_s = _next_ipju_seq(bunji1, bunji2, hosu)
    cur_s = _current_ipju_seq(bunji1, bunji2, hosu)
    row = _lookup_tenant_row(bunji1, bunji2, hosu, seq) if seq else _lookup_tenant_row(
        bunji1, bunji2, hosu, ""
    )
    if not row:
        # 이력 없음 → 신규. 요청 순번이 다음(max+1)보다 크면 다음 순번으로 보정
        use_seq, did_clamp = _clamp_new_ipju_seq(bunji1, bunji2, hosu, seq or next_s)
        return jsonify(
            {
                "ok": False,
                "reason": "empty",
                "next_seq": next_s,
                "current_seq": cur_s,
                "ipju_seq": use_seq,
                "did_clamp": did_clamp,
                "bunji1": bunji1,
                "bunji2": bunji2,
                "hosu": hosu,
            }
        )
    form = _tenant_form_from_row(row)
    return jsonify(
        {
            "ok": True,
            "form": form,
            "next_seq": next_s,
            "current_seq": cur_s,
        }
    )
