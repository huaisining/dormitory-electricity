import os, re, json, hashlib, time, sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, urllib3
urllib3.disable_warnings()
from des_ahu import DES

app = Flask(__name__)
CORS(app)
SESSIONS = {}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36"
CAS_BASE = "https://one.ahu.edu.cn/cas"
YCARD_BASE = "https://ycard.ahu.edu.cn"
Y_ENTRY = YCARD_BASE + "/berserker-auth/cas/login/neusoftCas?targetUrl=https://ycard.ahu.edu.cn/berserker-base/redirect?appId=16&type=app"
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

# ====== Auth ======
def full_login(username, password):
    s = requests.Session()
    s.verify = False
    s.headers["User-Agent"] = UA
    chain = []

    r = s.get(Y_ENTRY, allow_redirects=False, timeout=15)
    cas_url = r.headers.get("Location", "")
    if not cas_url:
        return {"error": "no redirect from ycard entry", "chain": chain}
    chain.append("1:302->cas")

    r = s.get(cas_url, timeout=15)
    lt_m = re.search(r'name="lt"\s+value="([^"]+)"', r.text)
    ex_m = re.search(r'name="execution"\s+value="([^"]+)"', r.text)
    if not lt_m or not ex_m:
        return {"error": "no lt/exec", "chain": chain}
    lt, execution = lt_m.group(1), ex_m.group(1)
    chain.append("2:got_lt_exec")

    enc = DES.str_enc(username + password + lt, "1", "2", "3")
    s.post(CAS_BASE + "/device",
        data={"ul": str(len(username)), "pl": str(len(password)), "rsa": enc, "method": "login"},
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
    chain.append("3:device_ok")

    r = s.post(cas_url,
        data={"rsa": enc, "ul": str(len(username)), "pl": str(len(password)), "lt": lt, "execution": execution, "_eventId": "submit"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=False, timeout=30)
    chain.append(f"5:POST={r.status_code}")

    next_url = r.headers.get("Location", "")
    for i in range(10):
        if not next_url:
            break
        r = s.get(next_url, allow_redirects=False, timeout=15)
        jwt_m = re.search(r"synjones-auth=([^&\"\s]+)", r.url)
        if not jwt_m:
            jwt_m = re.search(r"synjones-auth=([^&\"\s]+)", r.text[:5000])
        if jwt_m:
            return {"success": True, "jwt": jwt_m.group(1), "chain": chain, "student_id": username}
        next_url = r.headers.get("Location", "")

    jwt_m = re.search(r"synjones-auth=([^&\"\s]+)", r.url) if r else None
    if jwt_m:
        return {"success": True, "jwt": jwt_m.group(1), "student_id": username}
    return {"error": "no JWT", "chain": chain}

def api_call(jwt, form_data):
    h = {"User-Agent": UA, "synjones-auth": f"bearer {jwt}",
         "Accept": "application/json, text/plain, */*", "Origin": YCARD_BASE,
         "Referer": f"{YCARD_BASE}/charge-app/"}
    try:
        r = requests.post(f"{YCARD_BASE}/charge/feeitem/getThirdData", data=form_data, headers=h, verify=False, timeout=15)
        return r.json() if r.status_code == 200 else {"_err": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"_err": str(e)}

def parse_kwh(s):
    if not s: return None
    m = re.search(r"(\d+\.?\d*)\s*度", s)
    if m: return float(m.group(1))
    m = re.search(r"(\d+\.?\d*)", s)
    return float(m.group(1)) if m else None

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
            "building_name": row["building_name"], "members": members})
    db.close()
    return jsonify({"success": True, "has_room": False})

def room_join():
    """Attach current session to a room (after selecting room in sync page)."""
    d = request.get_json() or {}
    token = d.get("token", "")
    if token not in SESSIONS: return jsonify({"error": "session expired"}), 401

    campus = d.get("campus", "")
    building_code, building_name = "", ""
    if "&" in d.get("building", ""):
        parts = d["building"].split("&", 1)
        building_code, building_name = parts[0], parts[1]
    floor = d.get("floor", "")
    room_code = d.get("room", "")
    room_name = d.get("roomName", "")
    my_name = d.get("name", SESSIONS[token]["student_id"])

    if not campus or not building_code or not floor or not room_code:
        return jsonify({"error": "missing room info"}), 400

    db = get_db()
    # Find or create room
    row = db.execute(
        "SELECT id FROM rooms WHERE campus=? AND building_code=? AND floor=? AND room_code=?",
        (campus, building_code, floor, room_code)
    ).fetchone()

    if row:
        room_id = row["id"]
    else:
        cur = db.execute(
            "INSERT INTO rooms (campus, building_code, building_name, floor, room_code, room_name) VALUES (?,?,?,?,?,?)",
            (campus, building_code, building_name, floor, room_code, room_name)
        )
        room_id = cur.lastrowid

    # Add member
    sid = SESSIONS[token]["student_id"]
    db.execute(
        "INSERT OR REPLACE INTO members (room_id, student_id, name) VALUES (?,?,?)",
        (room_id, sid, my_name)
    )
    db.commit()

    SESSIONS[token]["room_id"] = room_id
    SESSIONS[token]["name"] = my_name

    # Get all members
    members = [dict(r) for r in db.execute("SELECT student_id, name FROM members WHERE room_id=?", (room_id,)).fetchall()]
    db.close()
    return jsonify({"success": True, "room_id": room_id, "room_name": room_name, "members": members})

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
    if "_err" in result: return jsonify({"error": str(result)}), 500

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
    # Reconstruct code&name format from stored values + names
    building_full = room["building_code"]
    if room["building_name"] and "&" not in building_full:
        building_full = building_full + "&" + room["building_name"]
    floor_full = room["floor"]
    if "&" not in floor_full:
        floor_full = floor_full + "&" + floor_full + "层"
    room_full = room["room_code"]
    if room["room_name"] and "&" not in room_full:
        room_full = room_full + "&" + room["room_name"]
    form = {"feeitemid": feeitemid, "type": "IEC", "level": level,
            "building": building_full, "floor": floor_full, "room": room_full}
    if room["campus"]: form["campus"] = room["campus"]

    result = api_call(jwt, form)
    if "_err" in result: return jsonify({"error": str(result)}), 500

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
        "info_text": info
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
    if "_err" in result: return jsonify({"error": str(result)}), 500

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
        return jsonify({"http_code": r.status_code if hasattr(r, 'status_code') else "N/A",
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
            if any(kw in search_text for kw in ["缴费", "水电", "能耗"]):
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
    if os.path.exists(idx_path):
        return flask.send_file(idx_path)
    return jsonify({"error": "index.html not found"}), 404

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
