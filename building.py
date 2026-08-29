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
    account_digits as _account_digits,
    fmt_bunji_pair,
    login_required,
    money,
    pad_bunji as _pad_bunji,
    paginate as _paginate,
    parse_bunji_input as _parse_bunji_input,
    parse_money as _parse_money,
    require_write_access,
)

# 전기료납부(elec_gb) — 기존 프로그램: 각세대별 / 관리비에 포함
# DB에 B 가 많고, 실무상 각세대별 전기료 → B=각세대별
ELEC_OPTIONS = [
    ("B", "각세대별"),
    ("A", "관리비에 포함"),
    ("", "미지정"),
]

# 관리형태(mgmt_gb) — 책임관리: 세입자 입금이 우리 통장으로 들어와 임대료만 임대인 정산.
# 일반관리: 세입자 입금이 임대인 통장으로 직접 들어가고 우리는 관리비를 별도 청구.
MGMT_OPTIONS = [
    ("R", "책임관리"),
    ("G", "일반관리"),
]

SUKUM_ACCT_OPTIONS = [
    ("M", "관리주체 통장"),
    ("O", "건물주 통장"),
]

BANK_OPTIONS = [
    ("", "은행 선택"),
    ("국민", "국민"),
    ("신한", "신한"),
    ("우리", "우리"),
    ("하나", "하나"),
    ("농협", "농협"),
    ("기업", "기업"),
    ("산업", "산업"),
    ("수협", "수협"),
    ("SC제일", "SC제일"),
    ("씨티", "씨티"),
    ("케이뱅크", "케이뱅크"),
    ("카카오뱅크", "카카오뱅크"),
    ("토스뱅크", "토스뱅크"),
    ("부산", "부산"),
    ("대구", "대구"),
    ("광주", "광주"),
    ("전북", "전북"),
    ("경남", "경남"),
    ("제주", "제주"),
    ("새마을", "새마을"),
    ("신협", "신협"),
    ("우체국", "우체국"),
    ("저축", "저축"),
]


def _bank_name_set():
    return {code for code, _ in BANK_OPTIONS if code}


def _split_bank_cd(raw):
    """bd01.bank_cd → (은행명, 계좌). 예전 값은 계좌만."""
    s = (raw or "").strip()
    names = _bank_name_set()
    if "|" in s:
        bank, acc = s.split("|", 1)
        return bank.strip(), acc.strip()
    parts = s.split(None, 1)
    if parts and parts[0] in names:
        return parts[0], parts[1] if len(parts) > 1 else ""
    return "", s


def _join_bank_cd(bank, acc):
    bank = (bank or "").strip()
    acc = (acc or "").strip()
    if bank and acc:
        return f"{bank}|{acc}"
    return acc or bank


def _apply_bank_fields(form, raw=None):
    name, acc = _split_bank_cd(raw if raw is not None else form.get("bank_cd"))
    form["bank_name"] = name
    form["bank_acc"] = acc
    return form


def _building_selects():
    return {
        "elec_options": ELEC_OPTIONS, "bank_options": BANK_OPTIONS,
        "mgmt_options": MGMT_OPTIONS, "sukum_acct_options": SUKUM_ACCT_OPTIONS,
    }


def _mgmt_label(v):
    key = (v or "").strip().upper()
    for code, name in MGMT_OPTIONS:
        if code == key:
            return name
    return "미지정"


def _sukum_acct_label(v):
    key = (v or "").strip().upper()
    for code, name in SUKUM_ACCT_OPTIONS:
        if code == key:
            return name
    return "미지정"


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


def _normalize_mgmt_gb(value):
    mgmt_gb = (value or "").strip().upper()
    if mgmt_gb not in ("R", "G"):
        return "R"
    return mgmt_gb


def _normalize_sukum_acct_gb(value, mgmt_gb="R"):
    key = (value or "").strip().upper()
    if key not in ("M", "O"):
        return "O" if _normalize_mgmt_gb(mgmt_gb) == "G" else "M"
    return key


def _ensure_g_cost_cols():
    for col in ("stair_cost", "inet_cost", "option_cost"):
        try:
            db.execute(
                f"ALTER TABLE bd01 ADD COLUMN {col} decimal(18,0) NULL DEFAULT 0"
            )
        except Exception:
            pass
    try:
        db.execute("ALTER TABLE bd01 ADD COLUMN sukum_acct_gb char(1) NULL DEFAULT NULL")
    except Exception:
        pass
    db.execute(
        """UPDATE bd01 SET sukum_acct_gb=CASE WHEN COALESCE(mgmt_gb,'R')='G' THEN 'O' ELSE 'M' END
           WHERE sukum_acct_gb IS NULL OR TRIM(sukum_acct_gb)=''"""
    )


def _extract_building_form_values(form):
    return {
        "bunji1": _pad_bunji(form.get("bunji1")),
        "bunji2": _pad_bunji(form.get("bunji2")),
        "juso": (form.get("juso") or "").strip(),
        "owner_nm": (form.get("owner_nm") or "").strip(),
        "owner_tel": (form.get("owner_tel") or "").strip(),
        "building_dt": (form.get("building_dt") or "").strip(),
        "bank_cd": _join_bank_cd(form.get("bank_name"), form.get("bank_acc") or form.get("bank_cd")),
        "elec_gb": _normalize_elec_gb(form.get("elec_gb")),
        "mgmt_gb": _normalize_mgmt_gb(form.get("mgmt_gb")),
        "sukum_acct_gb": _normalize_sukum_acct_gb(form.get("sukum_acct_gb"), form.get("mgmt_gb")),
        "floor_no": _coerce_building_floor_no(form.get("floor_no")),
        "man_cost": _parse_money(form.get("man_cost")),
        "first_amt": _parse_money(form.get("first_amt")),
        "stair_cost": _parse_money(form.get("stair_cost")),
        "inet_cost": _parse_money(form.get("inet_cost")),
        "option_cost": _parse_money(form.get("option_cost")),
    }


def _building_from_form(form, *, for_insert=False):
    data = _extract_building_form_values(form)
    data.update(
        {
            "del_yn": "N",
            "uid": session.get("sabun") or "",
        }
    )
    if data.get("mgmt_gb") != "G":
        data["stair_cost"] = 0
        data["inet_cost"] = 0
        data["option_cost"] = 0
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


def _validate_account_number(data):
    """계좌번호 검증. 은행·건물마다 자릿수·대시 규칙이 달라(구계좌, 휴대폰번호 계좌 등)
    특정 자릿수를 강제하지 않고 자릿수만 너무 짧은지(오타 의심) 확인.
    책임관리 건물은 우리 관리사무소 통장을 여러 건물이 같이 쓰는 게 정상이라 중복은 허용."""
    acc_digits = _account_digits(data.get("bank_cd"))
    if not acc_digits:
        return None
    if not (8 <= len(acc_digits) <= 20):
        return "계좌번호 자릿수를 확인하세요 (숫자 8~20자리, 휴대폰번호 계좌 포함)."
    return None


def _validate_building(data, *, for_insert=False):
    err = _validate_required_building_fields(data)
    if err:
        return err
    if for_insert:
        err = _check_duplicate_building(data)
        if err:
            return err
    return _validate_account_number(data)


def _decorate_building_card(r):
    r["mgmt_gb"] = _normalize_mgmt_gb(r.get("mgmt_gb"))
    r["sukum_acct_gb"] = _normalize_sukum_acct_gb(r.get("sukum_acct_gb"), r.get("mgmt_gb"))
    r["elec_label"] = _elec_label(r.get("elec_gb"))
    r["mgmt_label"] = _mgmt_label(r.get("mgmt_gb"))
    r["sukum_acct_label"] = _sukum_acct_label(r.get("sukum_acct_gb"))
    for col in ("stair_cost", "inet_cost", "option_cost"):
        r.setdefault(col, 0)
    _apply_bank_fields(r)
    dt = r.get("building_dt")
    r["build_year"] = str(dt)[:4] if dt else ""
    bank = " ".join(x for x in (r.get("bank_name"), r.get("bank_acc")) if x).strip()
    r["bank_disp"] = bank or "건물 전용 계좌 없음"
    return r


@app.route("/buildings")
@login_required
def buildings():
    """기초 내역 관리 · 건물 내역 조회 (목록)
    ?next=rooms 이면 카드에서 호수 내역으로 이동
    """
    _ensure_g_cost_cols()
    next_mode = (request.args.get("next") or "").strip()
    q = (request.args.get("q") or "").strip()
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
    if q:
        ql = q.replace(" ", "").lower()
        filtered = []
        for r in rows:
            key = "".join(
                [
                    str(r.get("juso") or ""),
                    str(r.get("owner_nm") or ""),
                    fmt_bunji_pair(r.get("bunji1"), r.get("bunji2")),
                    str(r.get("bunji1") or ""),
                    str(r.get("bunji2") or ""),
                ]
            ).replace(" ", "").lower()
            if ql in key:
                filtered.append(r)
        rows = filtered
    for r in rows:
        _decorate_building_card(r)
        room_rows = _get_rooms(r.get("bunji1"), r.get("bunji2"))
        floor_map = {}
        for room in room_rows:
            h = str(room.get("hosu") or "").strip()
            d = "".join(c for c in h if c.isdigit())
            f = "지하" if h.upper().startswith("B") else (int(d[:-2] or 0) if len(d) >= 3 else 0)
            floor_map.setdefault(f, []).append((h, bool(room.get("ipju_seq") or room.get("ipju_nm"))))
        r["floor_map"] = [(f, ([rooms] if len(rooms) <= 5 else [rooms[i:i + ((len(rooms) + 1) // 2)] for i in range(0, len(rooms), (len(rooms) + 1) // 2)])) for f, rooms in sorted(floor_map.items(), key=lambda x: (isinstance(x[0], str), -(x[0] if isinstance(x[0], int) else 0)))]
    return render_template(
        "buildings.html",
        buildings=rows,
        next_mode=next_mode,
        q=q,
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

    show_occupied = request.args.get("show_occupied") == "1"

    where = []
    if only_empty:
        where.append(
            f"""NOT EXISTS (
                  SELECT 1 FROM bd03_det d
                  WHERE d.bunji1=m.bunji1 AND d.bunji2=m.bunji2
                    AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu))
                    AND {_CURRENT_TENANT_SQL}
                )"""
        )
    elif show_occupied:
        where.append(
            f"""EXISTS (
                  SELECT 1 FROM bd03_det d
                  WHERE d.bunji1=m.bunji1 AND d.bunji2=m.bunji2
                    AND UPPER(TRIM(d.hosu))=UPPER(TRIM(m.hosu))
                    AND {_CURRENT_TENANT_SQL}
                )"""
        )
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
    if where_sql:
        where_clause = f"WHERE {where_sql}"
    else:
        where_clause = ""

    # 전체 개수 조회
    count_query = f"""
        SELECT COUNT(*) as total
        FROM bd03_m m
        LEFT JOIN bd01 b ON b.bunji1=m.bunji1 AND b.bunji2=m.bunji2
        {where_clause}
    """
    total_row = db.query_one(count_query, tuple(args))
    total_count = int((total_row or {}).get("total") or 0)

    # 페이징 처리
    from utils import parse_page, make_pager, PAGE_SIZE
    page = parse_page()
    per_page = PAGE_SIZE
    pager = make_pager(total_count, page, per_page=per_page)

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
        {where_clause}
        ORDER BY m.bunji1, m.bunji2,
          CASE WHEN m.hosu LIKE 'B%%' THEN 0 WHEN m.hosu REGEXP '^[0-9]' THEN 1 ELSE 2 END,
          m.hosu
        LIMIT %s OFFSET %s
        """,
        tuple(args + [per_page, pager["offset"]]),
    )

    today = date.today()
    for r in vacant_rows:
        out = r.get("last_out_dt")
        out_d = None
        if out is not None:
            if isinstance(out, datetime):
                out_d = out.date()
            elif isinstance(out, date):
                out_d = out
            else:
                try:
                    out_d = datetime.strptime(str(out)[:10], "%Y-%m-%d").date()
                except ValueError:
                    out_d = None
        has_tenant_hist = bool(r.get("last_ipju_nm"))
        if not has_tenant_hist:
            r["room_status"] = "never"
            r["vacant_days"] = None
        elif out_d is None or out_d.year < 1000:
            r["room_status"] = "occupied"
            r["vacant_days"] = None
        else:
            r["room_status"] = "vacant"
            r["vacant_days"] = max(0, (today - out_d).days)
        r["never_tenant"] = r["room_status"] == "never"

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
        "show_occupied": show_occupied,
    }

    return render_template(
        "vacancies.html",
        vacancies=vacant_rows,
        building_summary=building_summary,
        stats=stats,
        pager=pager,
        filters={
            "bunji1": bunji1,
            "bunji2": bunji2,
            "q": q,
            "only_empty": only_empty,
        },
    )


@app.route("/building/new", methods=["GET", "POST"])
@login_required
@require_write_access
def building_new():
    """건물 신규 등록"""
    _ensure_g_cost_cols()
    form = {
        "bunji1": "",
        "bunji2": "",
        "juso": "",
        "owner_nm": "",
        "owner_tel": "",
        "building_dt": "",
        "floor_no": "",
        "bank_cd": "",
        "bank_name": "",
        "bank_acc": "",
        "man_cost": "",
        "first_amt": "",
        "stair_cost": "",
        "inet_cost": "",
        "option_cost": "",
        "elec_gb": "B",
        "mgmt_gb": "R",
        "sukum_acct_gb": "M",
    }
    if request.method == "POST":
        data = _building_from_form(request.form, for_insert=True)
        form = _apply_bank_fields(
            {
                **data,
                "floor_no": "" if data["floor_no"] is None else str(data["floor_no"]),
                "man_cost": data["man_cost"],
                "first_amt": data["first_amt"],
                "stair_cost": data["stair_cost"],
                "inet_cost": data["inet_cost"],
                "option_cost": data["option_cost"],
            }
        )
        err = _validate_building(data, for_insert=True)
        if err:
            flash(err, "err")
            return render_template(
                "building_form.html",
                mode="new",
                form=form,
                **_building_selects(),
            )
        try:
            db.execute(
                """
                INSERT INTO bd01 (
                    bunji1, bunji2, juso, building_dt, floor_no, bank_cd,
                    owner_nm, owner_tel, man_cost, first_amt, elec_gb, mgmt_gb, sukum_acct_gb,
                    stair_cost, inet_cost, option_cost,
                    del_yn, uid, sys_dt
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
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
                    data["mgmt_gb"],
                    data["sukum_acct_gb"],
                    data.get("stair_cost") or 0,
                    data.get("inet_cost") or 0,
                    data.get("option_cost") or 0,
                    data["uid"],
                ),
            )
        except Exception as e:
            flash(f"등록 실패: {e}", "err")
            return render_template(
                "building_form.html",
                mode="new",
                form=form,
                **_building_selects(),
            )
        flash("건물이 등록되었습니다.", "ok")
        return redirect(
            url_for("building_detail", bunji1=data["bunji1"], bunji2=data["bunji2"])
        )

    return render_template(
        "building_form.html",
        mode="new",
        form=form,
        **_building_selects(),
    )


def _get_building_or_redirect(bunji1, bunji2):
    _ensure_g_cost_cols()
    b = db.query_one(
        "SELECT * FROM bd01 WHERE bunji1=%s AND bunji2=%s",
        (bunji1, bunji2),
    )
    if not b:
        return None
    _decorate_building_card(b)
    cnt = db.query_one(
        "SELECT COUNT(*) AS c FROM bd03_m WHERE bunji1=%s AND bunji2=%s",
        (b.get("bunji1"), b.get("bunji2")),
    )
    b["room_cnt"] = int((cnt or {}).get("c") or 0)
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
    # 층별 호실 현황(현재 입주자는 파란색, 공실은 빨간색)
    rooms = _get_rooms(bunji1, bunji2)
    floor_map = {}
    for room in rooms:
        hosu = str(room.get("hosu") or "").strip()
        digits = "".join(ch for ch in hosu if ch.isdigit())
        floor = int(digits[:-2] or 0) if len(digits) >= 3 else 0
        floor_map.setdefault(floor, []).append({
            "hosu": hosu,
            "occupied": bool(room.get("ipju_seq") or room.get("ipju_nm")),
        })
    floors = sorted(floor_map.items(), key=lambda item: item[0], reverse=True)
    return render_template("building_detail.html", building=b, floors=floors, **_building_selects())


@app.route("/building/<bunji1>/<bunji2>/rooms")
@login_required
def building_rooms(bunji1, bunji2):
    """호수별 상세 내역 (호수 내역 조회) — 건물 카드 숨김"""
    b = _get_building_or_redirect(bunji1, bunji2)
    if not b:
        flash("건물을 찾을 수 없습니다.", "err")
        return redirect(url_for("buildings"))
    rooms = _get_rooms(bunji1, bunji2)
    floor_map = {}
    for room in rooms:
        h = str(room.get("hosu") or "").strip(); d = "".join(c for c in h if c.isdigit())
        f = "지하" if h.upper().startswith("B") else (int(d[:-2] or 0) if len(d) >= 3 else 0)
        floor_map.setdefault(f, []).append((h, bool(room.get("ipju_seq") or room.get("ipju_nm"))))
    floor_map = [(f, [rs] if len(rs) <= 5 else [rs[i:i + ((len(rs)+1)//2)] for i in range(0, len(rs), (len(rs)+1)//2)]) for f, rs in sorted(floor_map.items(), key=lambda x: (isinstance(x[0], str), -(x[0] if isinstance(x[0], int) else 0)))]
    rooms, pager = _paginate(rooms)
    return render_template("building_rooms.html", building=b, rooms=rooms, pager=pager, floor_map=floor_map)


@app.route("/building/<bunji1>/<bunji2>/room/new", methods=["GET", "POST"])
@login_required
@require_write_access
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


@app.route("/building/<bunji1>/<bunji2>/room/<hosu>/delete", methods=["POST"])
@login_required
@require_write_access
def room_delete(bunji1, bunji2, hosu):
    """호수 삭제 (bd03_m)"""
    hosu = hosu.strip().upper()

    # 입주자가 있는지 확인
    tenant = db.query_one(
        """
        SELECT ipju_nm FROM bd03_det
        WHERE bunji1=%s AND bunji2=%s AND hosu=%s
          AND (out_dt IS NULL OR out_dt < '1000-01-01')
        LIMIT 1
        """,
        (bunji1, bunji2, hosu),
    )

    if tenant:
        flash("현재 입주자가 있는 호수는 삭제할 수 없습니다.", "err")
        return redirect(url_for("building_rooms", bunji1=bunji1, bunji2=bunji2))

    try:
        n = db.execute(
            "DELETE FROM bd03_m WHERE bunji1=%s AND bunji2=%s AND hosu=%s",
            (bunji1, bunji2, hosu),
        )
        if n:
            flash(f"호수 {hosu} 가 삭제되었습니다.", "ok")
        else:
            flash("삭제할 호수를 찾을 수 없습니다.", "err")
    except Exception as e:
        flash(f"삭제 실패: {e}", "err")

    return redirect(url_for("building_rooms", bunji1=bunji1, bunji2=bunji2))


def _building_form_from_row(b):
    """bd01 행 → 수정 폼 초기값"""
    dt = b.get("building_dt") or ""
    if dt and not isinstance(dt, str):
        dt = str(dt)[:10]
    elif isinstance(dt, str):
        dt = dt[:10]
    form = {
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
        "stair_cost": b.get("stair_cost"),
        "inet_cost": b.get("inet_cost"),
        "option_cost": b.get("option_cost"),
        "elec_gb": (b.get("elec_gb") or "").strip().upper(),
        "mgmt_gb": _normalize_mgmt_gb(b.get("mgmt_gb")),
        "sukum_acct_gb": _normalize_sukum_acct_gb(b.get("sukum_acct_gb"), b.get("mgmt_gb")),
    }
    return _apply_bank_fields(form)


def _building_orig_for_js(form):
    """수정 확인 팝업용 원본 스냅샷 (표시 문자열)"""
    return {
        "juso": form.get("juso") or "",
        "owner_nm": form.get("owner_nm") or "",
        "owner_tel": form.get("owner_tel") or "",
        "building_dt": (str(form.get("building_dt") or ""))[:10],
        "floor_no": form.get("floor_no") or "",
        "bank_cd": form.get("bank_cd") or "",
        "bank_name": form.get("bank_name") or "",
        "bank_acc": form.get("bank_acc") or "",
        "elec_gb": form.get("elec_gb") or "",
        "mgmt_gb": form.get("mgmt_gb") or "",
        "sukum_acct_gb": form.get("sukum_acct_gb") or "",
        "first_amt": money(form.get("first_amt")),
        "man_cost": money(form.get("man_cost")),
        "stair_cost": money(form.get("stair_cost")),
        "inet_cost": money(form.get("inet_cost")),
        "option_cost": money(form.get("option_cost")),
    }


_BUILDING_CHANGE_FIELDS = (
    ("juso", "건물명"),
    ("owner_nm", "건물주명"),
    ("owner_tel", "전화"),
    ("building_dt", "건축일"),
    ("floor_no", "층수"),
    ("bank_cd", "은행계좌"),
    ("elec_gb", "전기료납부"),
    ("mgmt_gb", "관리형태"),
    ("sukum_acct_gb", "입금통장 주체"),
    ("first_amt", "최초보증금"),
    ("man_cost", "관리수수료"),
    ("stair_cost", "계단청소"),
    ("inet_cost", "인터넷+유선"),
    ("option_cost", "옵션비"),
)


def _norm_building_val(key, v):
    if key in ("first_amt", "man_cost", "stair_cost", "inet_cost", "option_cost"):
        if v is None or v == "":
            return None
        return int(v)
    if key == "floor_no":
        if v is None or v == "":
            return None
        return int(v)
    if key == "building_dt":
        return (str(v)[:10] if v else "")
    if key in ("elec_gb", "mgmt_gb", "sukum_acct_gb"):
        return (str(v).strip().upper() if v else "")
    return ("" if v is None else str(v)).strip()


def _disp_building_val(key, v):
    if key in ("first_amt", "man_cost", "stair_cost", "inet_cost", "option_cost"):
        return money(v) if v is not None else "빈값"
    if key == "elec_gb":
        return _elec_label(v)
    if key == "mgmt_gb":
        return _mgmt_label(v)
    if key == "sukum_acct_gb":
        return _sukum_acct_label(v)
    if v is None or v == "":
        return "빈값"
    return str(v)


def _building_changes(orig, data):
    rows = []
    orig_g = _normalize_mgmt_gb(orig.get("mgmt_gb")) == "G"
    data_g = data.get("mgmt_gb") == "G"
    skip_g_cost = not orig_g and not data_g
    for key, label in _BUILDING_CHANGE_FIELDS:
        if skip_g_cost and key in ("stair_cost", "inet_cost", "option_cost"):
            continue
        before = _norm_building_val(key, orig.get(key))
        after = _norm_building_val(key, data.get(key))
        if before != after:
            rows.append((label, _disp_building_val(key, before), _disp_building_val(key, after)))
    return rows


def _building_change_flash(changes):
    bits = [f"{lab} {a} → {b}" for lab, a, b in changes]
    return "건물 내역이 수정되었습니다. " + " · ".join(bits)


@app.route("/building/<bunji1>/<bunji2>/edit", methods=["GET", "POST"])
@login_required
@require_write_access
def building_edit(bunji1, bunji2):
    """건물 내역 수정"""
    _ensure_g_cost_cols()
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
        form = _apply_bank_fields(
            {
                **data,
                "floor_no": "" if data["floor_no"] is None else str(data["floor_no"]),
                "man_cost": data["man_cost"],
                "first_amt": data["first_amt"],
                "stair_cost": data["stair_cost"],
                "inet_cost": data["inet_cost"],
                "option_cost": data["option_cost"],
            }
        )
        err = _validate_building(data, for_insert=False)
        if err:
            flash(err, "err")
            return render_template(
                "building_form.html",
                mode="edit",
                form=form,
                orig_js=_building_orig_for_js(orig),
                **_building_selects(),
            )
        changes = _building_changes(orig, data)
        if not changes:
            flash("변경된 내용이 없습니다.", "ok")
            return redirect(url_for("building_detail", bunji1=bunji1, bunji2=bunji2))
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
                    mgmt_gb=%s,
                    sukum_acct_gb=%s,
                    stair_cost=%s,
                    inet_cost=%s,
                    option_cost=%s,
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
                    data["mgmt_gb"],
                    data["sukum_acct_gb"],
                    data.get("stair_cost") or 0,
                    data.get("inet_cost") or 0,
                    data.get("option_cost") or 0,
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
                **_building_selects(),
            )
        flash(_building_change_flash(changes), "ok")
        return redirect(url_for("building_detail", bunji1=bunji1, bunji2=bunji2))

    return render_template(
        "building_form.html",
        mode="edit",
        form=orig,
        orig_js=_building_orig_for_js(orig),
        **_building_selects(),
    )
