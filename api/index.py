import os, re, json, hashlib, time
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests, urllib3
urllib3.disable_warnings()

app = Flask(__name__, static_folder="..")
CORS(app)

from shared.auth import UA, CAS_BASE, YCARD_BASE, Y_ENTRY, ycall, parse_kwh

KV_URL = os.environ.get("KV_REST_API_URL", "")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN", "")

def kv_get(key):
    if not KV_URL: return None
    try:
        r = requests.get(f"{KV_URL}/get/{key}", headers={"Authorization": f"Bearer {KV_TOKEN}"}, timeout=5)
        return json.loads(r.json()["result"]) if r.status_code == 200 else None
    except: return None

def kv_set(key, value):
    if not KV_URL: return
    try:
        requests.post(f"{KV_URL}/set/{key}", headers={"Authorization": f"Bearer {KV_TOKEN}"}, json={"value": json.dumps(value, ensure_ascii=False)}, timeout=5)
    except: pass

MEM = {}

def load(k, default=None):
    v = kv_get(k)
    if v is not None: return v
    return MEM.get(k, default)

def save(k, v):
    MEM[k] = v
    kv_set(k, v)

import sys, os as _os
_shared = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..")
if _shared not in sys.path: sys.path.insert(0, _shared)
from shared.des_ahu import DES

def full_login(user, pwd):
    # ponytail: delegates to shared.auth
    from shared.auth import raw_login
    return raw_login(user, pwd)

# ponytail: pk renamed to parse_kwh, imported from shared.auth
pk = parse_kwh
# --- Routes ---
@app.route("/")
def idx():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "index.html")
    if os.path.exists(p): return send_file(p)
    return jsonify({"error": "not found"}), 404

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "kv": bool(KV_URL)})

@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json() or {}
    u = d.get("username", "").strip(); p = d.get("password", "").strip()
    if not u or not p: return jsonify({"error": "need credentials"}), 400
    r = full_login(u, p)
    if "error" in r: return jsonify(r), 401
    tok = hashlib.sha256(f"{u}:{time.time()}".encode()).hexdigest()[:32]
    save(f"sess:{tok}", {"sid": u, "jwt": r["jwt"], "rid": None, "name": u})
    return jsonify({"success": True, "token": tok, "student_id": u})

def gs(tok):
    return load(f"sess:{tok}") or {}

def ss(tok, data):
    save(f"sess:{tok}", data)

@app.route("/api/room/my", methods=["POST"])
def my_room():
    d = request.get_json() or {}; tok = d.get("token", ""); sd = gs(tok)
    if not sd: return jsonify({"has_room": False})
    rooms = load("rooms", {}); members = load("members", {})
    for rid, mems in members.items():
        for m in mems:
            if m["student_id"] == sd["sid"]:
                sd["rid"] = rid; sd["name"] = m["name"]; ss(tok, sd)
                r = rooms.get(str(rid), {})
                return jsonify({"success": True, "has_room": True, "room_id": rid, "room_name": r.get("room_name",""), "building_name": r.get("building_name",""), "members": mems})
    return jsonify({"success": True, "has_room": False})

@app.route("/api/room/join", methods=["POST"])
def room_join():
    d = request.get_json() or {}; tok = d.get("token", ""); sd = gs(tok)
    if not sd: return jsonify({"error": "session expired"}), 401
    c = d.get("campus",""); b = d.get("building",""); f = d.get("floor","")
    rc = d.get("room",""); rn = d.get("roomName",""); nm = d.get("name", sd["sid"])
    bc, bn = b.split("&", 1) if "&" in b else (b, b)
    rooms = load("rooms", {}); rid = None
    for k, v in rooms.items():
        if v.get("room_code") == rc and v.get("floor") == f: rid = k; break
    if rid is None:
        rid = str(len(rooms) + 1)
        rooms[rid] = {"campus": c, "building_code": bc, "building_name": bn, "floor": f, "room_code": rc, "room_name": rn}
        save("rooms", rooms)
    members = load("members", {})
    if rid not in members: members[rid] = []
    members[rid] = [m for m in members[rid] if m["student_id"] != sd["sid"]]
    members[rid].append({"student_id": sd["sid"], "name": nm})
    save("members", members)
    sd["rid"] = rid; sd["name"] = nm; ss(tok, sd)
    return jsonify({"success": True, "room_id": rid, "room_name": rn, "members": members[rid]})

@app.route("/api/room/info", methods=["POST"])
def room_info():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd.get("rid"): return jsonify({"error": "not joined"}), 400
    rooms = load("rooms", {}); members = load("members", {}).get(sd["rid"], [])
    return jsonify({"success": True, "room": rooms.get(sd["rid"], {}), "members": members})

@app.route("/api/records", methods=["POST"])
def get_records():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd.get("rid"): return jsonify({"error": "not joined"}), 400
    return jsonify({"success": True, "records": load(f"recs:{sd['rid']}", [])})

@app.route("/api/records/add", methods=["POST"])
def add_record():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd.get("rid"): return jsonify({"error": "not joined"}), 400
    recs = load(f"recs:{sd['rid']}", [])
    rid = len(recs) + 1
    recs.append({"id": rid, "date": d.get("date",""), "amount": float(d.get("amount",0)), "payer_name": d.get("payer",""), "kwh": float(d.get("kwh",0)), "settled": 0, "source": d.get("source","manual"), "elec_type": d.get("elecType","")})
    save(f"recs:{sd['rid']}", recs)
    return jsonify({"success": True, "id": rid})

@app.route("/api/records/delete", methods=["POST"])
def delete_record():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd.get("rid"): return jsonify({"error": "not joined"}), 400
    recs = [r for r in load(f"recs:{sd['rid']}", []) if r["id"] != d.get("id")]
    save(f"recs:{sd['rid']}", recs)
    return jsonify({"success": True})

@app.route("/api/records/settle", methods=["POST"])
def settle_records():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd.get("rid"): return jsonify({"error": "not joined"}), 400
    ids = d.get("ids", []); recs = load(f"recs:{sd['rid']}", [])
    for r in recs:
        if r["id"] in ids: r["settled"] = 1
    save(f"recs:{sd['rid']}", recs)
    return jsonify({"success": True})

@app.route("/api/records/undo_settle", methods=["POST"])
def undo_settle():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd.get("rid"): return jsonify({"error": "not joined"}), 400
    recs = load(f"recs:{sd['rid']}", [])
    for r in recs: r["settled"] = 0
    save(f"recs:{sd['rid']}", recs)
    return jsonify({"success": True})

@app.route("/api/feeitem/select", methods=["POST"])
def api_select():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd: return jsonify({"error": "session expired"}), 401
    form = {"feeitemid": str(d.get("feeitemid", "488")), "type": "select", "level": str(d.get("level", "0"))}
    for k in ["campus", "building", "floor"]:
        if d.get(k): form[k] = d[k]
    r = ycall(sd["jwt"], form)
    if "_err" in r: return jsonify({"error": str(r)}), 500
    items = (r.get("map") or {}).get("data", [])
    opts = [{"id": it.get("value", it.get("id", "")), "name": it.get("name", "")} for it in items if isinstance(it, dict)]
    return jsonify({"success": True, "options": opts})

@app.route("/api/electricity/auto", methods=["POST"])
def api_elec_auto():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd.get("rid"): return jsonify({"error": "not joined"}), 400
    rooms = load("rooms", {}); room = rooms.get(sd["rid"], {})
    if not room: return jsonify({"error": "room not found"}), 404
    bf = room.get("building_code","")
    if room.get("building_name") and "&" not in bf: bf += "&" + room["building_name"]
    ff = room.get("floor","")
    if "&" not in ff: ff = ff + "&" + ff + "层"
    rf = room.get("room_code","")
    if room.get("room_name") and "&" not in rf: rf += "&" + room["room_name"]
    form = {"feeitemid": "488", "type": "IEC", "level": "4", "building": bf, "floor": ff, "room": rf}
    if room.get("campus"): form["campus"] = room["campus"]
    r = ycall(sd["jwt"], form)
    if "_err" in r: return jsonify({"error": str(r)}), 500
    mp = r.get("map") or {}; sd2 = mp.get("showData") or {}
    info = sd2.get("\u4fe1\u606f", ""); rd = mp.get("data") or {}
    kwh = pk(info)
    return jsonify({"success": True, "building": rd.get("buildingName",""), "room": rd.get("roomName",""), "remaining_kwh": kwh})

@app.route("/api/bills/electricity", methods=["POST"])
def api_bills_electricity():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd: return jsonify({"error": "session expired"}), 401
    jwt = sd["jwt"]; ps = d.get("pageSize", 50); mp = d.get("maxPages", 20)
    hd = {"User-Agent": UA, "synjones-auth": f"bearer {jwt}", "synAccessSource": "h5", "Accept": "*/*", "Referer": f"{YCARD_BASE}/campus-card/billing/list?appId=16&type=app"}
    elec = []; total = 0
    try:
        r = requests.get(f"{YCARD_BASE}/berserker-search/search/personal/turnover?size={ps}&current=1&synAccessSource=h5", headers=hd, verify=False, timeout=15)
        if r.status_code != 200: return jsonify({"error": f"HTTP {r.status_code}"}), 502
        resp = r.json(); db2 = resp.get("data", {}); recs = db2.get("records", []); total += len(recs)
        tp = min(db2.get("pages", 1), mp)
        for rec in recs:
            e = extr(rec)
            if e: elec.append(e)
        for pg in range(2, tp + 1):
            r = requests.get(f"{YCARD_BASE}/berserker-search/search/personal/turnover?size={ps}&current={pg}&synAccessSource=h5", headers=hd, verify=False, timeout=15)
            if r.status_code != 200: break
            for rec in (r.json().get("data", {}) or {}).get("records", []):
                e = extr(rec)
                if e: elec.append(e)
                total += 1
        return jsonify({"success": True, "total_scanned": total, "electricity_count": len(elec), "records": elec})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def extr(rec):
    resume = str(rec.get("resume", ""))
    merchant = str(rec.get("toMerchant", ""))
    turnover = str(rec.get("turnoverType", ""))
    ta = rec.get("tranamt", rec.get("tranAmt", 0))
    if not isinstance(ta, (int, float)): return None
    ay = abs(ta) / 100.0
    if ay < 0.01: return None
    st = " ".join([resume, turnover, str(rec.get("consumeTypeName","") or ""), str(rec.get("locationName","")), merchant, str(rec.get("payName",""))])
    if any(kw in st for kw in ["\u5145\u503c", "\u9000\u6b3e", "\u9000\u8d39", "\u8f6c\u5165"]): return None
    if not any(kw in st for kw in ["\u7535\u8d39", "\u8d2d\u7535", "\u7a7a\u8c03", "\u7167\u660e", "\u7535\u529b"]): return None
    et = "\u7167\u660e\u7535\u8d39" if "\u7167\u660e" in st else "\u7a7a\u8c03\u7535\u8d39" if "\u7a7a\u8c03" in st else "\u7535\u8d39"
    ds = str(rec.get("effectdateStr", rec.get("effectDateStr", "")) or rec.get("jndatetimeStr", ""))
    return {"date": ds[:19], "amount": round(ay, 2), "type": et, "merchant": merchant or resume, "description": resume or merchant}

@app.route("/api/bills/debug", methods=["POST"])
def api_bills_debug():
    d = request.get_json() or {}; sd = gs(d.get("token",""))
    if not sd: return jsonify({"error": "session expired"}), 401
    hd = {"User-Agent": UA, "synjones-auth": f"bearer {sd['jwt']}", "synAccessSource": "h5", "Accept": "*/*", "Referer": f"{YCARD_BASE}/campus-card/billing/list?appId=16&type=app"}
    try:
        r = requests.get(f"{YCARD_BASE}/berserker-search/search/personal/turnover?size=5&current=1&synAccessSource=h5", headers=hd, verify=False, timeout=15)
        data = r.json() if r.status_code == 200 else {"_http": r.status_code}
        recs = (data.get("data", {}) or {}).get("records", [])
        return jsonify({"total": len(recs), "samples": [{"keys": list(rec.keys()), "resume": rec.get("resume",""), "tranamt": rec.get("tranamt","")} for rec in recs[:5]]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
