import os, json, hashlib, time, sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, urllib3
urllib3.disable_warnings()
import sys, os as _os
_shared = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
if _shared not in sys.path: sys.path.insert(0, _shared)
from shared.des_ahu import DES

app = Flask(__name__)
CORS(app)
SESSIONS = {}
from shared.auth import UA, CAS_BASE, YCARD_BASE, Y_ENTRY, ycall, parse_kwh
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dorm.db")

# ====== Database ======
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campus TEXT NOT NULL,
            building_code TEXT NOT NULL,
            building_name TEXT NOT NULL,
            floor TEXT NOT NULL,
            room_code TEXT NOT NULL,
            room_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(campus, building_code, floor, room_code)
        );
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            student_id TEXT NOT NULL,
            name TEXT NOT NULL,
            joined_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(room_id, student_id)
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            payer_student_id TEXT NOT NULL DEFAULT '',
            payer_name TEXT NOT NULL,
            kwh REAL DEFAULT 0,
            settled INTEGER DEFAULT 0,
            source TEXT DEFAULT 'manual',
            elec_type TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            settled_at TEXT DEFAULT (datetime('now','localtime')),
            record_ids TEXT NOT NULL
        );
    """)
    db.commit()
    db.close()

init_db()

try:
    _db = get_db()
    _db.execute("ALTER TABLE rooms ADD COLUMN lighting_building TEXT DEFAULT ''")
    _db.commit()
except:
    pass
try:
    _db = get_db()
    _db.execute("ALTER TABLE rooms ADD COLUMN lighting_floor TEXT DEFAULT ''")
    _db.commit()
except:
    pass
try:
    _db = get_db()
    _db.execute("ALTER TABLE rooms ADD COLUMN lighting_room TEXT DEFAULT ''")
    _db.commit()
except:
    pass
_db = get_db()
_db.execute("UPDATE rooms SET lighting_building = building_code || '&' || replace(building_name, '空调', '照明') WHERE lighting_building = '' AND building_name LIKE '%空调%'")
_db.execute("UPDATE rooms SET lighting_building = building_code || '&' || replace(building_name, '照明', '空调') WHERE lighting_building = '' AND building_name LIKE '%照明%'")
_db.commit()
_db.close()

# ====== Auth ======
def full_login(username, password):
    # ponytail: delegates raw CAS auth to shared.auth, appends session fields here
    from shared.auth import raw_login
    result = raw_login(username, password)
    if "jwt" in result:
        result["student_id"] = username
    return result
def api_call(jwt, form_data):
    # ponytail: thin wrapper, shared.auth.ycall does the real work
    return ycall(jwt, form_data)

def get_room_id(token):
    """Get room_id from session, or None if not joined yet."""
    if token not in SESSIONS: return None
    return SESSIONS[token].get("room_id")

def require_room(token):
    """Get room_id or abort."""
    rid = get_room_id(token)
    if rid is None:
        return None
    return rid

# ====== Routes ======

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json() or {}
    u = d.get("username", "").strip()
    p = d.get("password", "").strip()
    if not u or not p: return jsonify({"error": "need credentials"}), 400
    result = full_login(u, p)
    if "error" in result: return jsonify(result), 401
    token = hashlib.sha256(f"{u}:{time.time()}".encode()).hexdigest()[:32]
    SESSIONS[token] = {"student_id": u, "jwt": result["jwt"], "room_id": None, "name": u}
    return jsonify({"success": True, "token": token, "student_id": u})

@app.route("/api/room/join", methods=["POST"])
def join_room():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS:
        return jsonify({"error": "session expired"}), 401
    sid = SESSIONS[token]["student_id"]
    name = d.get("name", sid)
    campus = d.get("campus", "")
    building = d.get("building", "")
    floor = d.get("floor", "")
    room = d.get("room", "")
    room_name = d.get("roomName", "")
    if not campus or not building or not floor or not room:
        return jsonify({"error": "need campus/building/floor/room"}), 400
    db = get_db()
    building_parts = building.split("&", 1)
    building_code = building_parts[0]
    building_name = building_parts[1] if len(building_parts) > 1 else building_code
    room_parts = room.split("&", 1)
    room_code = room_parts[0]
    room_name_final = room_parts[1] if len(room_parts) > 1 else room_name
    row = db.execute(
        "SELECT id FROM rooms WHERE campus=? AND building_code=? AND floor=? AND room_code=?",
        (campus, building_code, floor, room_code)
    ).fetchone()
    if row:
        room_id = row["id"]
    else:
        cur = db.execute(
            "INSERT INTO rooms (campus, building_code, building_name, floor, room_code, room_name) VALUES (?,?,?,?,?,?)",
            (campus, building_code, building_name, floor, room_code, room_name_final)
        )
        room_id = cur.lastrowid
    try:
        db.execute("INSERT INTO members (room_id, student_id, name) VALUES (?,?,?)", (room_id, sid, name))
    except:
        pass
    db.commit()
    SESSIONS[token]["room_id"] = room_id
    SESSIONS[token]["name"] = name
    members = [dict(r) for r in db.execute("SELECT student_id, name FROM members WHERE room_id=?", (room_id,)).fetchall()]
    db.close()
    return jsonify({"success": True, "room_id": room_id, "room_name": room_name_final, "members": members})

@app.route("/api/room/my", methods=["POST"])
def my_room():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS:
        return jsonify({"error": "session expired"}), 401
    sid = SESSIONS[token]["student_id"]
    db = get_db()
    print(f"[room/my] student_id={sid}", flush=True)
    row = db.execute(
        "SELECT m.room_id, m.name, r.building_name, r.room_name, r.campus, r.building_code, r.floor, r.room_code FROM members m JOIN rooms r ON m.room_id=r.id WHERE m.student_id=?",
        (sid,)
    ).fetchone()
    print(f"[room/my] row found: {row is not None}", flush=True)
    if row:
        room_id = row["room_id"]
        SESSIONS[token]["room_id"] = room_id
        SESSIONS[token]["name"] = row["name"] or sid
        members = [dict(r) for r in db.execute("SELECT student_id, name FROM members WHERE room_id=?", (room_id,)).fetchall()]
        db.close()
        return jsonify({"success": True, "has_room": True, "room_id": room_id, "room_name": row["room_name"],
            "building_name": row["building_name"], "campus": row["campus"], "members": members})
    db.close()
    return jsonify({"success": True, "has_room": False})



@app.route("/api/room/info", methods=["POST"])
def room_info():
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400

    db = get_db()
    room = dict(db.execute("SELECT * FROM rooms WHERE id=?", (rid,)).fetchone())
    members = [dict(r) for r in db.execute("SELECT student_id, name FROM members WHERE room_id=?", (rid,)).fetchall()]
    db.close()
    return jsonify({"success": True, "room": room, "members": members})

@app.route("/api/records", methods=["POST"])
def get_records():
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400

    db = get_db()
    year = d.get("year")
    month = d.get("month")
    if year and month:
        prefix = f"{int(year):04d}-{int(month):02d}"
        rows = db.execute(
            "SELECT id, date, amount, payer_student_id, payer_name, kwh, settled, source, elec_type FROM records WHERE room_id=? AND date LIKE ? ORDER BY id DESC",
            (rid, prefix + "%")
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, date, amount, payer_student_id, payer_name, kwh, settled, source, elec_type FROM records WHERE room_id=? ORDER BY id DESC",
            (rid,)
        ).fetchall()
    records = [dict(r) for r in rows]
    db.close()
    return jsonify({"success": True, "records": records})

@app.route("/api/records/add", methods=["POST"])
def add_record():
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400

    date = d.get("date", "")
    amount = d.get("amount", 0)
    payer_name = d.get("payer", SESSIONS[token].get("name", ""))
    payer_sid = SESSIONS[token]["student_id"]
    kwh = d.get("kwh", 0)
    source = d.get("source", "manual")
    elec_type = d.get("elecType", "")

    if not date or amount <= 0:
        return jsonify({"error": "invalid data"}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO records (room_id, date, amount, payer_student_id, payer_name, kwh, source, elec_type) VALUES (?,?,?,?,?,?,?,?)",
        (rid, date, amount, payer_sid, payer_name, kwh, source, elec_type)
    )
    new_id = cur.lastrowid
    db.commit()
    db.close()
    return jsonify({"success": True, "id": new_id})

@app.route("/api/records/delete", methods=["POST"])
def delete_record():
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400

    rec_id = d.get("id")
    db = get_db()
    db.execute("DELETE FROM records WHERE id=? AND room_id=?", (rec_id, rid))
    db.commit()
    db.close()
    return jsonify({"success": True})

@app.route("/api/records/settle", methods=["POST"])
def settle_records():
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400

    record_ids = d.get("ids", [])
    if not record_ids:
        return jsonify({"error": "no ids"}), 400

    db = get_db()
    placeholders = ",".join("?" * len(record_ids))
    db.execute(f"UPDATE records SET settled=1 WHERE id IN ({placeholders}) AND room_id=?", (*record_ids, rid))
    db.execute("INSERT INTO settlements (room_id, record_ids) VALUES (?,?)", (rid, json.dumps(record_ids)))
    db.commit()
    db.close()
    return jsonify({"success": True})

@app.route("/api/records/undo_settle", methods=["POST"])
def undo_settle():
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400

    db = get_db()
    last = db.execute("SELECT * FROM settlements WHERE room_id=? ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    if not last:
        db.close()
        return jsonify({"error": "no settlement"}), 400

    ids = json.loads(last["record_ids"])
    placeholders = ",".join("?" * len(ids))
    db.execute(f"UPDATE records SET settled=0 WHERE id IN ({placeholders}) AND room_id=?", (*ids, rid))
    db.execute("DELETE FROM settlements WHERE id=?", (last["id"],))
    db.commit()
    db.close()
    return jsonify({"success": True})

@app.route("/api/records/summary", methods=["POST"])
def records_summary():
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400
    import datetime
    now = datetime.datetime.now()
    year = d.get("year")
    month = d.get("month")
    db = get_db()
    if year is not None and month is not None:
        prefix = f"{int(year):04d}-{int(month):02d}"
        rows = db.execute(
            "SELECT elec_type, SUM(amount) as total, COUNT(*) as cnt FROM records WHERE room_id=? AND date LIKE ? GROUP BY elec_type",
            (rid, prefix + "%")
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT elec_type, SUM(amount) as total, COUNT(*) as cnt FROM records WHERE room_id=? GROUP BY elec_type",
            (rid,)
        ).fetchall()
    db.close()
    ac_total = 0.0
    light_total = 0.0
    other_total = 0.0
    count = 0
    for r in rows:
        et = r["elec_type"] or ""
        amt = round(r["total"] or 0, 2)
        cnt = r["cnt"] or 0
        count += cnt
        if et == "空调电费":
            ac_total = amt
        elif et == "照明电费":
            light_total = amt
        else:
            other_total += amt
    grand_total = round(ac_total + light_total + other_total, 2)
    return jsonify({
        "success": True,
        "year": year, "month": month,
        "ac_total": ac_total,
        "lighting_total": light_total,
        "other_total": other_total,
        "grand_total": grand_total,
        "count": count
    })

@app.route("/api/feeitem/select", methods=["POST"])
def api_select():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    jwt = SESSIONS[token]["jwt"]
    form = {"feeitemid": str(d.get("feeitemid", "488")), "type": "select", "level": str(d.get("level", "0"))}
    if d.get("campus"): form["campus"] = d["campus"]
    if d.get("building"): form["building"] = d["building"]
    if d.get("floor"): form["floor"] = d["floor"]
    result = api_call(jwt, form)
    if "_err" in result: return jsonify({"error": "elec API failed", "detail": result.get("_err",""), "raw": str(result)[:500], "sent_form": {"feeitemid":feeitemid,"building":building_full,"floor":floor_full,"room":room_full,"campus":room.get("campus","")}}), 500

    # Check API-level response code (Ahu_Plus does this)
    api_code = result.get("code", 200)
    api_msg = result.get("msg", "")

    mp = result.get("map") or {}
    items = mp.get("data", [])
    total_steps = mp.get("total", [])

    options = [{"id": it.get("value", it.get("id", "")), "name": it.get("name", "")} for it in items if isinstance(it, dict)]

    return jsonify({
        "success": True,
        "options": options,
        "level": form["level"],
        "_debug": {"api_code": api_code, "api_msg": api_msg, "total_steps": total_steps, "raw_data_count": len(items)}
    })

@app.route("/api/electricity", methods=["POST"])
def api_elec():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    jwt = SESSIONS[token]["jwt"]
    feeitemid = str(d.get("feeitemid", "488"))
    level = str(d.get("level", "4"))
    campus = d.get("campus", "")
    building = d.get("building", "")
    floor = d.get("floor", "")
    room = d.get("room", "")
    form = {"feeitemid": feeitemid, "type": "IEC", "level": level,
            "building": building, "floor": floor, "room": room}
    if campus: form["campus"] = campus
    result = api_call(jwt, form)
    if "_err" in result: return jsonify({"error": result.get("_err", "")}), 500
    mp = result.get("map") or {}
    sd = mp.get("showData") or {}
    info = sd.get("信息", "")
    rd = mp.get("data") or {}
    kwh = parse_kwh(info)
    if kwh is None and isinstance(sd, dict):
        for v in sd.values():
            kwh = parse_kwh(str(v))
            if kwh is not None: break
    return jsonify({"success": True, "kwh": kwh, "info": info,
        "building": rd.get("buildingName", ""), "room": rd.get("roomName", "")})

@app.route("/api/electricity/auto", methods=["POST"])
def api_elec_auto():
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400

    jwt = SESSIONS[token]["jwt"]
    db = get_db()
    room = db.execute("SELECT * FROM rooms WHERE id=?", (rid,)).fetchone()
    db.close()
    if not room: return jsonify({"error": "room not found"}), 404

    feeitemid = "488"
    level = "4"
    building_full = room["building_code"]
    if room["building_name"] and "&" not in building_full:
        building_full = building_full + "&" + room["building_name"]
    floor_full = room["floor"]
    if "&" not in floor_full:
        floor_full = floor_full + "&" + floor_full + "层"
    room_full = room["room_code"]
    if room["room_name"] and "&" not in room_full:
        room_full = room_full + "&" + room["room_name"]

    def query_one_with_room(bld, flr, rm, label):
        form = {"feeitemid": feeitemid, "type": "IEC", "level": level,
                "building": bld, "floor": flr, "room": rm}
        if room["campus"]: form["campus"] = room["campus"]
        result = api_call(jwt, form)
        if "_err" in result:
            return {"label": label, "kwh": None, "error": result.get("_err", "")}
        mp = result.get("map") or {}
        sd = mp.get("showData") or {}
        info = sd.get("信息", "")
        rd = mp.get("data") or {}
        kwh = parse_kwh(info)
        if kwh is None and isinstance(sd, dict):
            for v in sd.values():
                kwh = parse_kwh(str(v))
                if kwh is not None: break
        return {"label": label, "kwh": kwh, "building": rd.get("buildingName", ""),
                "room": rd.get("roomName", ""), "info": info}

    def query_one(bld, label):
        form = {"feeitemid": feeitemid, "type": "IEC", "level": level,
                "building": bld, "floor": floor_full, "room": room_full}
        if room["campus"]: form["campus"] = room["campus"]
        result = api_call(jwt, form)
        if "_err" in result:
            return {"label": label, "kwh": None, "error": result.get("_err", "")}
        mp = result.get("map") or {}
        sd = mp.get("showData") or {}
        info = sd.get("信息", "")
        rd = mp.get("data") or {}
        kwh = parse_kwh(info)
        if kwh is None and isinstance(sd, dict):
            for v in sd.values():
                kwh = parse_kwh(str(v))
                if kwh is not None: break
        return {"label": label, "kwh": kwh, "building": rd.get("buildingName", ""),
                "room": rd.get("roomName", ""), "info": info}

    # Query AC meter separately
    ac = query_one(building_full, "空调")
    ac["label"] = "空调"

    # Query lighting meter separately
    l_bld = room["lighting_building"]
    l_flr = room["lighting_floor"]
    l_rm = room["lighting_room"]
    lighting = None
    if l_bld and l_flr and l_rm:
        lighting = query_one_with_room(l_bld, l_flr, l_rm, "照明")
    else:
        lighting = {"label": "照明", "kwh": None, "info": "未设置照明房间"}

    l_kwh = lighting.get("kwh") if lighting else None
    ac_kwh = ac.get("kwh")
    total = ac_kwh if ac_kwh is not None else None
    if ac_kwh is not None and l_kwh is not None:
        total = round(ac_kwh + l_kwh, 2)

    return jsonify({
        "success": True,
        "ac": ac,
        "lighting": lighting,
        "total_kwh": total,
        "note": "" if (l_bld and l_flr and l_rm) else "请设置照明房间"
    })

def api_elec():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    jwt = SESSIONS[token]["jwt"]
    feeitemid = str(d.get("feeitemid", "488"))
    building = d.get("building", "")
    floor = d.get("floor", "")
    room = d.get("room", "")
    campus = d.get("campus", "")

    level = "4" if feeitemid == "488" else "3"
    form = {"feeitemid": feeitemid, "type": "IEC", "level": level,
            "building": building, "floor": floor, "room": room}
    if campus: form["campus"] = campus

    result = api_call(jwt, form)
    if "_err" in result: return jsonify({"error": "elec API failed", "detail": result.get("_err",""), "sent_form": {"feeitemid":feeitemid,"building":building,"floor":floor,"room":room,"campus":campus}}), 500

    mp = result.get("map") or {}
    sd = mp.get("showData") or {}
    info = sd.get("信息", "")
    tip = str(mp.get("tipinfo", "") or "")
    rd = mp.get("data") or {}

    kwh = parse_kwh(info) or parse_kwh(tip)
    if kwh is None and isinstance(sd, dict):
        for v in sd.values():
            kwh = parse_kwh(str(v))
            if kwh is not None: break

    return jsonify({
        "success": True, "feeitemid": feeitemid,
        "building": rd.get("buildingName", ""),
        "room": rd.get("roomName", ""),
        "remaining_kwh": kwh,
        "info_text": info,
        "tipinfo": tip[:300],
        "_sent": f"level={level} campus={campus} bld={building} flr={floor} room={room}"
    })

# ====== Campus Card Bills ======

@app.route("/api/bills", methods=["POST"])
def api_bills():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    jwt = SESSIONS[token]["jwt"]
    current = d.get("current", 1)
    size = d.get("size", 50)

    url = f"{YCARD_BASE}/berserker-search/search/personal/turnover?size={size}&current={current}&synAccessSource=h5"
    headers = {
        "User-Agent": UA, "synjones-auth": f"bearer {jwt}",
        "synAccessSource": "h5", "Accept": "*/*",
        "Referer": f"{YCARD_BASE}/campus-card/billing/list?appId=16&type=app"
    }
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        if r.status_code != 200:
            return jsonify({"error": f"bill API HTTP {r.status_code}"}), 502
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/bills/electricity", methods=["POST"])
def api_bills_electricity():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    jwt = SESSIONS[token]["jwt"]
    page_size = d.get("pageSize", 50)
    max_pages = d.get("maxPages", 20)

    elec_bills = []
    total_records = 0
    headers = {
        "User-Agent": UA, "synjones-auth": f"bearer {jwt}",
        "synAccessSource": "h5", "Accept": "*/*",
        "Referer": f"{YCARD_BASE}/campus-card/billing/list?appId=16&type=app"
    }

    try:
        url = f"{YCARD_BASE}/berserker-search/search/personal/turnover?size={page_size}&current=1&synAccessSource=h5"
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        if r.status_code != 200:
            return jsonify({"error": f"first page HTTP {r.status_code}"}), 502
        resp = r.json()
        data_block = resp.get("data", {})
        records = data_block.get("records", [])
        total_pages = min(data_block.get("pages", 1), max_pages)
        total_records += len(records)
        for rec in records:
            elec = extract_electricity_bill(rec)
            if elec: elec_bills.append(elec)

        for page in range(2, total_pages + 1):
            url = f"{YCARD_BASE}/berserker-search/search/personal/turnover?size={page_size}&current={page}&synAccessSource=h5"
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            if r.status_code != 200: break
            resp = r.json()
            more = (resp.get("data", {}) or {}).get("records", [])
            total_records += len(more)
            for rec in more:
                elec = extract_electricity_bill(rec)
                if elec: elec_bills.append(elec)

        return jsonify({
            "success": True, "total_scanned": total_records,
            "total_pages_scanned": min(total_pages, max_pages),
            "electricity_count": len(elec_bills), "records": elec_bills
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/bills/summary", methods=["POST"])
def api_bills_summary():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    jwt = SESSIONS[token]["jwt"]
    import datetime
    now = datetime.datetime.now()
    year = int(d.get("year", now.year))
    month = int(d.get("month", now.month))
    prefix = f"{year:04d}-{month:02d}"
    page_size = 50
    max_pages = 20

    ac_total = 0.0
    light_total = 0.0
    count = 0
    headers = {
        "User-Agent": UA, "synjones-auth": f"bearer {jwt}",
        "synAccessSource": "h5", "Accept": "*/*",
        "Referer": f"{YCARD_BASE}/campus-card/billing/list?appId=16&type=app"
    }

    try:
        url = f"{YCARD_BASE}/berserker-search/search/personal/turnover?size={page_size}&current=1&synAccessSource=h5"
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        if r.status_code != 200:
            return jsonify({"error": f"HTTP {r.status_code}"}), 502
        resp = r.json()
        data_block = resp.get("data", {})
        records = data_block.get("records", [])
        total_pages = min(data_block.get("pages", 1), max_pages)

        def process(recs):
            nonlocal ac_total, light_total, count
            for rec in recs:
                elec = extract_electricity_bill(rec)
                if not elec: continue
                d = elec.get("date", "")[:7]
                if d != prefix: continue
                et = elec.get("type", "")
                amt = elec.get("amount", 0)
                if et == "空调电费":
                    ac_total += amt
                elif et == "照明电费":
                    light_total += amt
                else:
                    pass
                count += 1

        process(records)
        for page in range(2, total_pages + 1):
            url = f"{YCARD_BASE}/berserker-search/search/personal/turnover?size={page_size}&current={page}&synAccessSource=h5"
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            if r.status_code != 200: break
            resp = r.json()
            more = (resp.get("data", {}) or {}).get("records", [])
            process(more)

        return jsonify({
            "success": True,
            "year": year, "month": month,
            "ac_total": round(ac_total, 2),
            "lighting_total": round(light_total, 2),
            "grand_total": round(ac_total + light_total, 2),
            "count": count
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/bills/import", methods=["POST"])
def api_bills_import():
    """Auto-import electricity bills from campus card to records table."""
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    jwt = SESSIONS[token]["jwt"]
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400
    page_size = 50
    max_pages = 20
    headers = {
        "User-Agent": UA, "synjones-auth": f"bearer {jwt}",
        "synAccessSource": "h5", "Accept": "*/*",
        "Referer": f"{YCARD_BASE}/campus-card/billing/list?appId=16&type=app"
    }
    imported = 0
    skipped = 0
    try:
        db = get_db()
        existing = set()
        for row in db.execute("SELECT date, amount FROM records WHERE room_id=? AND source='campus_card'", (rid,)).fetchall():
            existing.add((row["date"][:10], round(row["amount"], 2)))
        
        total_pages = [1]
        def do_page(page):
            nonlocal imported, skipped
            url = f"{YCARD_BASE}/berserker-search/search/personal/turnover?size={page_size}&current={page}&synAccessSource=h5"
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            if r.status_code != 200: return False
            resp = r.json()
            data_block = resp.get("data", {}) or {}
            if page == 1:
                total_pages[0] = min(data_block.get("pages", 1), max_pages)
            recs = data_block.get("records", [])
            for rec in recs:
                elec = extract_electricity_bill(rec)
                if not elec: continue
                dkey = (elec["date"][:10], round(elec["amount"], 2))
                if dkey in existing: 
                    skipped += 1
                    continue
                pname = SESSIONS[token].get("name", "") or SESSIONS[token]["student_id"]
                db.execute(
                    "INSERT INTO records (room_id, date, amount, payer_student_id, payer_name, kwh, source, elec_type) VALUES (?,?,?,?,?,?,?,?)",
                    (rid, elec["date"][:19], elec["amount"], SESSIONS[token]["student_id"], pname, 0, "campus_card", elec["type"])
                )
                existing.add(dkey)
                imported += 1
            return True

        if not do_page(1):
            db.close()
            return jsonify({"error": "Failed to fetch campus card data"}), 502
        for page in range(2, total_pages[0] + 1):
            if not do_page(page): break
        
        db.commit()
        db.close()
        return jsonify({"success": True, "imported": imported, "skipped": skipped})
    except Exception as e:
        try: db.close()
        except: pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/rooms/list", methods=["POST"])
def rooms_list():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    db = get_db()
    rows = db.execute(
        "SELECT r.id, r.room_name, r.building_name, r.campus, (SELECT COUNT(*) FROM members m WHERE m.room_id=r.id) as member_count FROM rooms r ORDER BY r.id DESC"
    ).fetchall()
    db.close()
    return jsonify({"success": True, "rooms": [dict(r) for r in rows]})

@app.route("/api/room/join2", methods=["POST"])
def join_room_by_id():
    """Join an existing room by room ID."""
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    sid = SESSIONS[token]["student_id"]
    name = d.get("name", sid)
    room_id = d.get("room_id")
    if not room_id: return jsonify({"error": "need room_id"}), 400
    db = get_db()
    room = db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        db.close()
        return jsonify({"error": "room not found"}), 404
    try:
        db.execute("INSERT INTO members (room_id, student_id, name) VALUES (?,?,?)", (room_id, sid, name))
    except:
        pass
    db.commit()
    SESSIONS[token]["room_id"] = room_id
    SESSIONS[token]["name"] = name
    members = [dict(r) for r in db.execute("SELECT student_id, name FROM members WHERE room_id=?", (room_id,)).fetchall()]
    db.close()
    return jsonify({"success": True, "room_id": room_id, "room_name": room["room_name"], "members": members})

@app.route("/api/room/lighting", methods=["POST"])
def room_lighting():
    """Save lighting building/floor/room for current room."""
    d = request.get_json() or {}
    token = d.get("token", "")
    rid = require_room(token)
    if rid is None: return jsonify({"error": "not joined room"}), 400

    lb = d.get("building", "")
    lf = d.get("floor", "")
    lr = d.get("room", "")
    if not lb or not lf or not lr:
        return jsonify({"error": "need building, floor, room"}), 400

    db = get_db()
    db.execute("UPDATE rooms SET lighting_building=?, lighting_floor=?, lighting_room=? WHERE id=?",
               (lb, lf, lr, rid))
    db.commit()
    db.close()
    return jsonify({"success": True})

@app.route("/api/bills/debug", methods=["POST"])
def api_bills_debug():
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401
    jwt = SESSIONS[token]["jwt"]
    url = f"{YCARD_BASE}/berserker-search/search/personal/turnover?size=5&current=1&synAccessSource=h5"
    headers = {
        "User-Agent": UA, "synjones-auth": f"bearer {jwt}",
        "synAccessSource": "h5", "Accept": "*/*",
        "Referer": f"{YCARD_BASE}/campus-card/billing/list?appId=16&type=app"
    }
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=15)
        data = r.json() if r.status_code == 200 else {"_http": r.status_code}
        records = (data.get("data", {}) or {}).get("records", [])
        sample = []
        for rec in records[:5]:
            sample.append({
                "_all_keys": list(rec.keys()),
                "resume": rec.get("resume", ""),
                "tranamt": rec.get("tranamt", "N/A"),
                "effectdateStr": rec.get("effectdateStr", rec.get("effectDateStr", "")),
                "turnoverType": rec.get("turnoverType", ""),
                "toMerchant": rec.get("toMerchant", ""),
                "payName": rec.get("payName", ""),
                "locationName": rec.get("locationName", ""),
                "consumeTypeName": rec.get("consumeTypeName", ""),
            })
        return jsonify({"http_code": r.status_code if True else "N/A",
            "total_scanned": len(records), "sample_size": len(sample), "samples": sample})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def extract_electricity_bill(rec):
    resume = str(rec.get("resume", ""))
    merchant = str(rec.get("toMerchant", ""))
    location = str(rec.get("locationName", ""))
    turnover = str(rec.get("turnoverType", ""))
    pay_name = str(rec.get("payName", ""))
    consume_type = str(rec.get("consumeTypeName", "") or "")

    tran_amt = rec.get("tranamt", rec.get("tranAmt", 0))
    if isinstance(tran_amt, (int, float)):
        amount_yuan = abs(tran_amt) / 100.0
    else:
        amount_yuan = 0.0
    if amount_yuan < 0.01:
        return None

    search_text = " ".join([resume, turnover, consume_type, location, merchant, pay_name])


    exclude_kw = ["充值", "退款", "退费", "转入", "入账"]
    if any(kw in search_text for kw in exclude_kw):
        return None

    is_elec = any(kw in search_text for kw in ["电费", "购电", "买电", "售电", "电力"])
    is_ac = "空调" in search_text
    is_light = "照明" in search_text

    if not (is_elec or is_ac or is_light):
        if isinstance(tran_amt, (int, float)) and tran_amt < 0:
            if any(kw in search_text for kw in ["缴费", "水电", "能源"]):
                is_elec = True
        if not (is_elec or is_ac or is_light):
            return None

    if is_light:
        elec_type = "照明电费"
    elif is_ac:
        elec_type = "空调电费"
    else:
        elec_type = "电费"

    date_str = str(rec.get("effectdateStr", rec.get("effectDateStr", "")) or rec.get("jndatetimeStr", ""))
    return {
        "date": date_str[:19] if date_str else "",
        "amount": round(amount_yuan, 2),
        "type": elec_type,
        "merchant": merchant or resume or "",
        "description": resume or merchant or "",
        "turnoverType": turnover,
        "payName": pay_name,
        "raw": {"resume": resume, "tranAmt": tran_amt, "effectDateStr": date_str,
                "toMerchant": merchant, "turnoverType": turnover, "payName": pay_name}
    }



# ====== PWA / Static ======
import flask

@app.route("/")
def serve_index():
    idx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "index.html")
    resp = flask.make_response(flask.send_file(idx_path))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/manifest.json")
def serve_manifest():
    mp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest.json")
    if os.path.exists(mp):
        return flask.send_file(mp, mimetype="application/manifest+json")
    return jsonify({}), 404

@app.route("/sw.js")
def serve_sw():
    sp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sw.js")
    if os.path.exists(sp):
        return flask.send_file(sp, mimetype="application/javascript")
    return "// not found", 404

@app.route("/icon-<int:size>.png")
def serve_icon(size):
    ip = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"icon-{size}.png")
    if os.path.exists(ip):
        return flask.send_file(ip, mimetype="image/png")
    return "", 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)), debug=False)
