"""건물/호실 관리 화면.

건물 목록·공실 현황 조회, 건물 신규 등록·수정, 호수 신규 등록 라우트와
그 전용 도우미 함수들을 모아둔 모듈입니다. (기초 내역 관리 메뉴)
"""
from datetime import date, datetime

from flask import flash, redirect, render_template, request, session, url_for

import db
from app_instance import app
from utils import (
    CURRENT_TENANT_SQL as _CURRENT_TENANT_SQL,
    fmt_bunji_pair,
    login_required,
    money,
    pad_bunji as _pad_bunji,
    parse_bunji_input as _parse_bunji_input,
    parse_money as _parse_money,
)

# 전기료납부(elec_gb) — 기존 프로그램: 각세대별 / 관리비에 포함
# DB에 B 가 많고, 실무상 각세대별 전기료 → B=각세대별
ELEC_OPTIONS = [
    ("B", "각세대별"),
    ("A", "관리비에 포함"),
    ("", "미지정"),
]


def _elec_label(v):
    if v is None or str(v).strip() == "":
        return "미지정"
    key = str(v).strip().upper()
    for code, name in ELEC_OPTIONS:
        if code == key:
            return name
    return key


def _coerce_building_floor_no(value):
    floor_raw = (value or "").strip()
    return int(floor_raw) if floor_raw.isdigit() else None


def _normalize_elec_gb(value):
    elec_gb = (value or "").strip().upper()
    if elec_gb not in ("A", "B", ""):
        return ""
    return elec_gb


def _extract_building_form_values(form):
    return {
        "bunji1": _pad_bunji(form.get("bunji1")),
        "bunji2": _pad_bunji(form.get("bunji2")),
        "juso": (form.get("juso") or "").strip(),
        "owner_nm": (form.get("owner_nm") or "").strip(),
        "owner_tel": (form.get("owner_tel") or "").strip(),
        "building_dt": (form.get("building_dt") or "").strip(),
        "bank_cd": (form.get("bank_cd") or "").strip(),
        "elec_gb": _normalize_elec_gb(form.get("elec_gb")),
        "floor_no": _coerce_building_floor_no(form.get("floor_no")),
        "man_cost": _parse_money(form.get("man_cost")),
        "first_amt": _parse_money(form.get("first_amt")),
    }


def _building_from_form(form, *, for_insert=False):
    data = _extract_building_form_values(form)
    data.update(
        {
            "del_yn": "N",
            "uid": session.get("sabun") or "",
        }
    )
    return data


def _validate_required_building_fields(data):
    if not data.get("bunji1") or not data.get("bunji2"):
        return "주소, 주소2는 필수입니다."
    if not data.get("juso"):
        return "건물명은 필수입니다."
    return None


def _check_duplicate_building(data):
    exists = db.query_one(
        "SELECT 1 AS x FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (data["bunji1"], data["bunji2"]),
    )
    if exists:
        return "이미 등록된 주소입니다."
    return None


def _validate_building(data, *, for_insert=False):
    err = _validate_required_building_fields(data)
    if err:
        return err
    if for_insert:
        err = _check_duplicate_building(data)
        if err:
            return err
    return None


@app.route("/buildings")
@login_required
def buildings():
    """기초 내역 관리 · 건물 내역 조회 (목록)
    ?next=rooms 이면 행/호수 클릭 시 호수 내역 화면으로 이동
    """
    next_mode = (request.args.get("next") or "").strip()
    rows = db.query(
        f"""
        SELECT b.*,
               COALESCE(m.room_cnt, 0) AS room_cnt,
               COALESCE(d.tenant_cnt, 0) AS tenant_cnt
        FROM bd01 b
        LEFT JOIN (
            SELECT bunji1, bunji2, COUNT(*) AS room_cnt
            FROM bd03_m
            GROUP BY bunji1, bunji2
        ) m ON m.bunji1=b.bunji1 AND m.bunji2=b.bunji2
        LEFT JOIN (
            SELECT bunji1, bunji2, COUNT(*) AS tenant_cnt
            FROM bd03_det d
            WHERE {_CURRENT_TENANT_SQL}
            GROUP BY bunji1, bunji2
        ) d ON d.bunji1=b.bunji1 AND d.bunji2=b.bunji2
        ORDER BY b.bunji1, b.bunji2
        """
    )
    for r in rows:
        r["elec_label"] = _elec_label(r.get("elec_gb"))
    return render_template(
        "buildings.html",
        buildings=rows,
        next_mode=next_mode,
    )


@app.route("/vacancies")
@login_required
def vacancies():
    """기초 내역 관리 · 공실 현황 조회

    공실: bd03_m 호수 중 현재 입주자(bd03_det, out_dt 없음)가 없는 호.
    """
    # 주소·주소2 분리 (구 링크용 bunji=508-88 도 허용)
    bunji1 = _pad_bunji(request.args.get("bunji1"))
    bunji2 = _pad_bunji(request.args.get("bunji2"))
    bunji_legacy = (request.args.get("bunji") or "").strip()
    if bunji_legacy and not (bunji1 or bunji2):
        try:
            bunji1, bunji2 = _parse_bunji_input(bunji_legacy)
        except Exception:
            bunji1, bunji2 = "", ""
    q = (request.args.get("q") or "").strip()
    # checkbox+hidden: 마지막 값 사용 (체크 시 0,1 → 1 / 미체크 시 0)
    only_vals = request.args.getlist("only_empty")
    if only_vals:
        only_empty = only_vals[-1].strip() == "1"
    else:
        only_empty = True

    where = [
        f"""NOT EXISTS (
              SELECT 1 FROM bd03_det d
              WHERE d.bunji1=m.bunji1 AND d.bunji2=m.bunji2
                AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu))
                AND {_CURRENT_TENANT_SQL}
            )"""
    ]
    args = []
    if bunji1 and bunji2:
        where.append("m.bunji1=%s AND m.bunji2=%s")
        args.extend([bunji1, bunji2])
    if q:
        like = f"%{q}%"
        where.append(
            "(b.juso LIKE %s OR b.owner_nm LIKE %s OR m.hosu LIKE %s "
            "OR CONCAT(TRIM(LEADING '0' FROM m.bunji1), '-', "
            "TRIM(LEADING '0' FROM m.bunji2)) LIKE %s)"
        )
        args.extend([like, like, like, like])

    where_sql = " AND ".join(where)

    vacant_rows = db.query(
        f"""
        SELECT m.bunji1, m.bunji2, m.hosu, m.rent_gb, m.r_type, m.b_type, m.o_type,
               b.juso, b.owner_nm, b.owner_tel,
               last_d.out_dt AS last_out_dt,
               last_d.ipju_nm AS last_ipju_nm,
               last_d.ipju_seq AS last_ipju_seq,
               last_d.ipju_dt AS last_ipju_dt,
               last_d.rent_amt AS last_rent_amt,
               last_d.manage_amt AS last_manage_amt,
               last_d.bojung_amt AS last_bojung_amt
        FROM bd03_m m
        LEFT JOIN bd01 b ON b.bunji1=m.bunji1 AND b.bunji2=m.bunji2
        LEFT JOIN bd03_det last_d
          ON last_d.bunji1=m.bunji1 AND last_d.bunji2=m.bunji2
         AND UPPER(TRIM(last_d.hosu))=UPPER(TRIM(m.hosu))
         AND last_d.ipju_seq = (
               SELECT d2.ipju_seq FROM bd03_det d2
               WHERE d2.bunji1=m.bunji1 AND d2.bunji2=m.bunji2
                 AND UPPER(TRIM(d2.hosu))=UPPER(TRIM(m.hosu))
               ORDER BY CAST(d2.ipju_seq AS UNSIGNED) DESC
               LIMIT 1
             )
        WHERE {where_sql}
        ORDER BY m.bunji1, m.bunji2, m.hosu
        """,
        tuple(args),
    )

    today = date.today()
    for r in vacant_rows:
        out = r.get("last_out_dt")
        if out is None:
            r["vacant_days"] = None
            r["never_tenant"] = not bool(r.get("last_ipju_nm"))
        else:
            if isinstance(out, datetime):
                out_d = out.date()
            elif isinstance(out, date):
                out_d = out
            else:
                try:
                    out_d = datetime.strptime(str(out)[:10], "%Y-%m-%d").date()
                except ValueError:
                    out_d = None
            if out_d and out_d.year >= 1000:
                r["vacant_days"] = max(0, (today - out_d).days)
                r["never_tenant"] = False
            else:
                r["vacant_days"] = None
                r["never_tenant"] = True

    # 건물별 요약 (전체 호수 / 입주 / 공실)
    building_rows = db.query(
        f"""
        SELECT b.bunji1, b.bunji2, b.juso, b.owner_nm,
               COALESCE(m.room_cnt, 0) AS room_cnt,
               COALESCE(d.occupied_cnt, 0) AS occupied_cnt
        FROM bd01 b
        LEFT JOIN (
            SELECT bunji1, bunji2, COUNT(*) AS room_cnt
            FROM bd03_m
            GROUP BY bunji1, bunji2
        ) m ON m.bunji1=b.bunji1 AND m.bunji2=b.bunji2
        LEFT JOIN (
            SELECT bunji1, bunji2, COUNT(*) AS occupied_cnt
            FROM bd03_det d
            WHERE {_CURRENT_TENANT_SQL}
            GROUP BY bunji1, bunji2
        ) d ON d.bunji1=b.bunji1 AND d.bunji2=b.bunji2
        ORDER BY b.bunji1, b.bunji2
        """
    )
    building_summary = []
    for b in building_rows:
        room_cnt = int(b.get("room_cnt") or 0)
        occ = int(b.get("occupied_cnt") or 0)
        vac = max(0, room_cnt - occ)
        b["occupied_cnt"] = occ
        b["vacant_cnt"] = vac
        # 공실 있는 건물만: 공실 0 (만실·호수 미등록) 제외
        if only_empty and vac == 0:
            continue
        if bunji1 and bunji2 and (b["bunji1"] != bunji1 or b["bunji2"] != bunji2):
            continue
        if q:
            display = fmt_bunji_pair(b["bunji1"], b["bunji2"])
            blob = f"{b.get('juso') or ''} {b.get('owner_nm') or ''} {display}"
            if q not in blob and q not in display:
                # 공실 목록에 이미 잡힌 건물만 통과시킬 수도 있음 — 검색어 포함 여부
                if q.lower() not in blob.lower():
                    continue
        building_summary.append(b)

    # 전체 집계
    totals = db.query_one(
        f"""
        SELECT
          (SELECT COUNT(*) FROM bd03_m) AS room_total,
          (SELECT COUNT(*) FROM bd03_det d WHERE {_CURRENT_TENANT_SQL}) AS occupied_total,
          (SELECT COUNT(*) FROM bd01) AS building_total
        """
    )
    room_total = int((totals or {}).get("room_total") or 0)
    occupied_total = int((totals or {}).get("occupied_total") or 0)
    vacant_total = max(0, room_total - occupied_total)
    stats = {
        "building_total": int((totals or {}).get("building_total") or 0),
        "room_total": room_total,
        "occupied_total": occupied_total,
        "vacant_total": vacant_total,
        "vacant_listed": len(vacant_rows),
        "buildings_with_vacancy": sum(
            1 for b in building_rows if int(b.get("vacant_cnt") or 0) > 0
        ),
    }

    return render_template(
        "vacancies.html",
        vacancies=vacant_rows,
        building_summary=building_summary,
        stats=stats,
        filters={
            "bunji1": bunji1,
            "bunji2": bunji2,
            "q": q,
            "only_empty": only_empty,
        },
    )


@app.route("/building/new", methods=["GET", "POST"])
@login_required
def building_new():
    """건물 신규 등록"""
    form = {
        "bunji1": "",
        "bunji2": "",
        "juso": "",
        "owner_nm": "",
        "owner_tel": "",
        "building_dt": "",
        "floor_no": "",
        "bank_cd": "",
        "man_cost": "",
        "first_amt": "",
        "elec_gb": "B",
    }
    if request.method == "POST":
        data = _building_from_form(request.form, for_insert=True)
        form = {
            **data,
            "floor_no": "" if data["floor_no"] is None else str(data["floor_no"]),
            "man_cost": data["man_cost"],
            "first_amt": data["first_amt"],
        }
        err = _validate_building(data, for_insert=True)
        if err:
            flash(err, "err")
            return render_template(
                "building_form.html",
                mode="new",
                form=form,
                elec_options=ELEC_OPTIONS,
            )
        try:
            db.execute(
                """
                INSERT INTO bd01 (
                    bunji1, bunji2, juso, building_dt, floor_no, bank_cd,
                    owner_nm, owner_tel, man_cost, first_amt, elec_gb,
                    del_yn, uid, sys_dt
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    'N', %s, NOW()
                )
                """,
                (
                    data["bunji1"],
                    data["bunji2"],
                    data["juso"],
                    data["building_dt"] or None,
                    data["floor_no"],
                    data["bank_cd"],
                    data["owner_nm"],
                    data["owner_tel"],
                    data["man_cost"],
                    data["first_amt"],
                    data["elec_gb"] or None,
                    data["uid"],
                ),
            )
        except Exception as e:
            flash(f"등록 실패: {e}", "err")
            return render_template(
                "building_form.html",
                mode="new",
                form=form,
                elec_options=ELEC_OPTIONS,
            )
        flash("건물이 등록되었습니다.", "ok")
        return redirect(
            url_for("building_detail", bunji1=data["bunji1"], bunji2=data["bunji2"])
        )

    return render_template(
        "building_form.html",
        mode="new",
        form=form,
        elec_options=ELEC_OPTIONS,
    )


def _get_building_or_redirect(bunji1, bunji2):
    b = db.query_one(
        "SELECT * FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (bunji1, bunji2),
    )
    if not b:
        return None
    b["elec_label"] = _elec_label(b.get("elec_gb"))
    return b


def _get_rooms(bunji1, bunji2):
    return db.query(
        f"""
        SELECT m.hosu, m.rent_gb, m.r_type, m.b_type, m.o_type, m.r_no, m.gas_no,
               d.ipju_seq, d.ipju_nm, d.ipju_tel1, d.ipju_dt, d.out_dt,
               d.bojung_amt, d.rent_amt, d.manage_amt, d.yechi_amt
        FROM bd03_m m
        LEFT JOIN bd03_det d
          ON d.bunji1=m.bunji1 AND d.bunji2=m.bunji2
         AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu))
         AND {_CURRENT_TENANT_SQL}
        WHERE m.bunji1=%s AND m.bunji2=%s
        ORDER BY m.hosu
        """,
        (bunji1, bunji2),
    )


@app.route("/building/<bunji1>/<bunji2>")
@login_required
def building_detail(bunji1, bunji2):
    """기초 내역 관리 · 건물 내역 (건물 정보만)"""
    b = _get_building_or_redirect(bunji1, bunji2)
    if not b:
        flash("건물을 찾을 수 없습니다.", "err")
        return redirect(url_for("buildings"))
    return render_template("building_detail.html", building=b)


@app.route("/building/<bunji1>/<bunji2>/rooms")
@login_required
def building_rooms(bunji1, bunji2):
    """호수별 상세 내역 (호수 내역 조회) — 건물 카드 숨김"""
    b = _get_building_or_redirect(bunji1, bunji2)
    if not b:
        flash("건물을 찾을 수 없습니다.", "err")
        return redirect(url_for("buildings"))
    rooms = _get_rooms(bunji1, bunji2)
    return render_template("building_rooms.html", building=b, rooms=rooms)


@app.route("/building/<bunji1>/<bunji2>/room/new", methods=["GET", "POST"])
@login_required
def room_new(bunji1, bunji2):
    """호수 신규 등록 (bd03_m)"""
    b = db.query_one(
        "SELECT * FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (bunji1, bunji2),
    )
    if not b:
        flash("건물을 찾을 수 없습니다.", "err")
        return redirect(url_for("buildings"))

    form = {
        "hosu": "",
        "rent_gb": "A",
        "r_type": "",
        "b_type": "",
        "o_type": "",
        "r_no": "",
        "gas_no": "",
    }

    if request.method == "POST":
        hosu = (request.form.get("hosu") or "").strip().upper()
        rent_gb = (request.form.get("rent_gb") or "A").strip().upper()[:1] or "A"
        r_type = (request.form.get("r_type") or "").strip().upper()[:1]
        b_type = (request.form.get("b_type") or "").strip().upper()[:1]
        o_type = (request.form.get("o_type") or "").strip().upper()[:1]
        gas_no = (request.form.get("gas_no") or "").strip()
        r_no_raw = (request.form.get("r_no") or "").strip()
        r_no = int(r_no_raw) if r_no_raw.isdigit() else None

        form = {
            "hosu": hosu,
            "rent_gb": rent_gb,
            "r_type": r_type,
            "b_type": b_type,
            "o_type": o_type,
            "r_no": r_no_raw,
            "gas_no": gas_no,
        }

        if not hosu:
            flash("호수는 필수입니다.", "err")
            return render_template(
                "room_form.html", building=b, form=form, mode="new"
            )

        exists = db.query_one(
            """
            SELECT 1 AS x FROM bd03_m
            WHERE bunji1=%s AND bunji2=%s AND hosu=%s
            """,
            (bunji1, bunji2, hosu),
        )
        if exists:
            flash("이미 등록된 호수입니다.", "err")
            return render_template(
                "room_form.html", building=b, form=form, mode="new"
            )

        try:
            db.execute(
                """
                INSERT INTO bd03_m (
                    bunji1, bunji2, hosu, rent_gb, r_type, b_type, o_type,
                    r_no, gas_no, plan_out_dt, del_yn, sys_dt, uid
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, '', 'N', NOW(), %s
                )
                """,
                (
                    bunji1,
                    bunji2,
                    hosu,
                    rent_gb,
                    r_type or None,
                    b_type or None,
                    o_type or None,
                    r_no,
                    gas_no,
                    session.get("sabun") or "",
                ),
            )
        except Exception as e:
            flash(f"호수 등록 실패: {e}", "err")
            return render_template(
                "room_form.html", building=b, form=form, mode="new"
            )

        flash(f"호수 {hosu} 가 등록되었습니다.", "ok")
        return redirect(url_for("building_rooms", bunji1=bunji1, bunji2=bunji2))

    return render_template("room_form.html", building=b, form=form, mode="new")


def _building_form_from_row(b):
    """bd01 행 → 수정 폼 초기값"""
    dt = b.get("building_dt") or ""
    if dt and not isinstance(dt, str):
        dt = str(dt)[:10]
    elif isinstance(dt, str):
        dt = dt[:10]
    return {
        "bunji1": b.get("bunji1") or "",
        "bunji2": b.get("bunji2") or "",
        "juso": b.get("juso") or "",
        "owner_nm": b.get("owner_nm") or "",
        "owner_tel": b.get("owner_tel") or "",
        "building_dt": dt,
        "floor_no": "" if b.get("floor_no") is None else str(b.get("floor_no")),
        "bank_cd": b.get("bank_cd") or "",
        "man_cost": b.get("man_cost"),
        "first_amt": b.get("first_amt"),
        "elec_gb": (b.get("elec_gb") or "").strip().upper(),
    }


def _building_orig_for_js(form):
    """수정 확인 팝업용 원본 스냅샷 (표시 문자열)"""
    return {
        "juso": form.get("juso") or "",
        "owner_nm": form.get("owner_nm") or "",
        "owner_tel": form.get("owner_tel") or "",
        "building_dt": (str(form.get("building_dt") or ""))[:10],
        "floor_no": form.get("floor_no") or "",
        "bank_cd": form.get("bank_cd") or "",
        "elec_gb": form.get("elec_gb") or "",
        "first_amt": money(form.get("first_amt")),
        "man_cost": money(form.get("man_cost")),
    }


@app.route("/building/<bunji1>/<bunji2>/edit", methods=["GET", "POST"])
@login_required
def building_edit(bunji1, bunji2):
    """건물 내역 수정"""
    b = db.query_one(
        "SELECT * FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (bunji1, bunji2),
    )
    if not b:
        flash("건물을 찾을 수 없습니다.", "err")
        return redirect(url_for("buildings"))

    orig = _building_form_from_row(b)

    if request.method == "POST":
        data = _building_from_form(request.form)
        # 주소는 키이므로 변경하지 않음
        data["bunji1"] = bunji1
        data["bunji2"] = bunji2
        form = {
            **data,
            "floor_no": "" if data["floor_no"] is None else str(data["floor_no"]),
            "man_cost": data["man_cost"],
            "first_amt": data["first_amt"],
        }
        err = _validate_building(data, for_insert=False)
        if err:
            flash(err, "err")
            return render_template(
                "building_form.html",
                mode="edit",
                form=form,
                orig_js=_building_orig_for_js(orig),
                elec_options=ELEC_OPTIONS,
            )
        try:
            db.execute(
                """
                UPDATE bd01 SET
                    juso=%s,
                    building_dt=%s,
                    floor_no=%s,
                    bank_cd=%s,
                    owner_nm=%s,
                    owner_tel=%s,
                    man_cost=%s,
                    first_amt=%s,
                    elec_gb=%s,
                    uid=%s,
                    sys_dt=NOW()
                WHERE bunji1=%s AND bunji2=%s
                """,
                (
                    data["juso"],
                    data["building_dt"] or None,
                    data["floor_no"],
                    data["bank_cd"],
                    data["owner_nm"],
                    data["owner_tel"],
                    data["man_cost"],
                    data["first_amt"],
                    data["elec_gb"] or None,
                    data["uid"],
                    bunji1,
                    bunji2,
                ),
            )
        except Exception as e:
            flash(f"수정 실패: {e}", "err")
            return render_template(
                "building_form.html",
                mode="edit",
                form=form,
                orig_js=_building_orig_for_js(orig),
                elec_options=ELEC_OPTIONS,
            )
        flash("건물 내역이 수정되었습니다.", "ok")
        return redirect(url_for("building_detail", bunji1=bunji1, bunji2=bunji2))

    return render_template(
        "building_form.html",
        mode="edit",
        form=orig,
        orig_js=_building_orig_for_js(orig),
        elec_options=ELEC_OPTIONS,
    )
