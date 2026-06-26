# -*- coding: utf-8 -*-
""""Flask backend for AHU dorm electricity auto-sync.
Proxies CAS auth + ycard API calls, detects recharge changes.
"""
import os, re, json, hashlib, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import urllib3
urllib3.disable_warnings()
from des_ahu import DES

app = Flask(__name__)
CORS(app)

# Store sessions in memory (per-process, for free tier)
SESSIONS = {}  # token -> {cookies, jwt, username, last_balance}
CREDENTIALS = {}  # token -> {username, password_encrypted}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36"
CAS_BASE = "https://one.ahu.edu.cn/cas"
YCARD_BASE = "https://ycard.ahu.edu.cn"
CAS_HOST = "one.ahu.edu.cn"
YCARD_HOST = "ycard.ahu.edu.cn"

# ==================== CAS Auth ====================

def cas_login(username, password):
    """Full CAS login flow. Returns (session_cookies, jsessionid)."""
    session = requests.Session()
    session.verify = False
    session.headers["User-Agent"] = UA

    service = f"https://one.ahu.edu.cn/tp_up/view?m=up"

    # Step 1: GET login page, extract lt and execution
    r = session.get(f"{CAS_BASE}/login", params={"service": service}, allow_redirects=False)
    html = r.text
    lt_match = re.search(r'name="lt"\s+value="([^"]+)"', html)
    exec_match = re.search(r'name="execution"\s+value="([^"]+)"', html)
    if not lt_match or not exec_match:
        if r.status_code in (301, 302):
            return {"error": "已有有效会话，请先清除"}
        return {"error": f"无法获取 lt/execution, HTTP {r.status_code}"}
    lt = lt_match.group(1)
    execution = exec_match.group(1)

    # Step 2: POST /cas/device
    encrypted = DES.str_enc(username + password + lt, "1", "2", "3")
    r = session.post(f"{CAS_BASE}/device", data={
        "ul": str(len(username)), "pl": str(len(password)),
        "rsa": encrypted, "method": "login"
    }, headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"{CAS_BASE}/login?service={service}"
    })
    try:
        info = r.json().get("info", "")
    except:
        return {"error": f"设备验证失败: {r.text[:200]}"}
    if info == "nf":
        return {"error": "学号或密码错误"}
    elif info != "ok":
        return {"error": f"设备验证失败: {info}"}

    # Step 3: POST /cas/login → CASTGC
    r = session.post(f"{CAS_BASE}/login", params={"service": service}, data={
        "rsa": encrypted, "ul": str(len(username)), "pl": str(len(password)),
        "lt": lt, "execution": execution, "_eventId": "submit"
    }, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"{CAS_BASE}/login?service={service}"
    }, allow_redirects=False)
    if r.status_code not in (301, 302):
        return {"error": f"CAS 登录提交失败: HTTP {r.status_code}"}

    # Step 4: GET /cas/login (with CASTGC) → ST ticket
    r = session.get(f"{CAS_BASE}/login", params={"service": service}, allow_redirects=False)
    location = r.headers.get("Location", "")
    ticket_match = re.search(r'ticket=([^&]+)', location)
    if not ticket_match:
        return {"error": f"未获取 ticket: {location[:100]}"}
    ticket = ticket_match.group(1)

    # Step 5: GET /tp_up/view → JSESSIONID
    r = session.get(f"https://one.ahu.edu.cn/tp_up/view", params={"m": "up", "ticket": ticket}, allow_redirects=False)

    # Extract cookies
    cookies = {c.name: c.value for c in session.cookies}
    jsessionid = cookies.get("JSESSIONID", "")
    if not jsessionid:
        return {"error": "未获取 JSESSIONID"}

    return {"success": True, "cookies": cookies, "jsessionid": jsessionid}


def ycard_auth(cookies):
    """Use CAS cookies to get ycard JWT."""
    session = requests.Session()
    session.verify = False
    session.headers["User-Agent"] = UA
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=CAS_HOST)

    ycard_entry = (
        f"{YCARD_BASE}/berserker-auth/cas/login/neusoftCas"
        f"?targetUrl=https://ycard.ahu.edu.cn/berserker-base/redirect?appId=16&type=app"
    )

    try:
        r = session.get(ycard_entry, allow_redirects=True, timeout=30)
        final_url = r.url
        jwt_match = re.search(r'synjones-auth=([^&"\s]+)', final_url)
        if jwt_match:
            jwt = jwt_match.group(1)
        else:
            jwt_match = re.search(r'synjones-auth=([^&"\s]+)', r.text)
            jwt = jwt_match.group(1) if jwt_match else ""
    except Exception as e:
        return {"error": f"ycard 认证失败: {str(e)}"}

    ycard_cookies = {c.name: c.value for c in session.cookies}
    return {"success": True, "jwt": jwt, "cookies": ycard_cookies}


# ==================== Electricity Query ====================

def query_electricity(jwt, feeitemid=408):
    """Query electricity balance from ycard."""
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "synjones-auth": jwt,
    }
    try:
        r = requests.post(
            f"{YCARD_BASE}/charge/feeitem/getThirdData",
            data={"feeitemid": str(feeitemid)},
            headers=headers, verify=False, timeout=15
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def parse_kwh(info_text):
    """Parse kWh from electricity info text."""
    if not info_text:
        return None
    for pattern in [
        r'(\d+\.?\d*)\s*度',
        r'剩余电量\s*[:：]?\s*(\d+\.?\d*)',
    ]:
        m = re.search(pattern, info_text)
        if m:
            return float(m.group(1))
    return None


# ==================== API Endpoints ====================

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "timestamp": time.time()})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "请输入学号和密码"}), 400

    result = cas_login(username, password)
    if "error" in result:
        return jsonify(result), 401

    # Ycard auth
    ycard_result = ycard_auth(result["cookies"])
    if "error" in ycard_result:
        return jsonify(ycard_result), 401

    token = hashlib.sha256(f"{username}:{time.time()}".encode()).hexdigest()[:32]
    SESSIONS[token] = {
        "username": username,
        "jsessionid": result["jsessionid"],
        "jwt": ycard_result["jwt"],
        "cookies": result["cookies"],
        "ycard_cookies": ycard_result.get("cookies", {}),
        "last_balance_ac": None,
        "last_balance_light": None,
        "created_at": time.time()
    }

    return jsonify({
        "success": True,
        "token": token,
        "username": username
    })


@app.route("/api/electricity", methods=["POST"])
def get_electricity():
    data = request.get_json() or {}
    token = data.get("token", "")
    feeitemid = data.get("feeitemid", 408)  # 408=AC, 428=lighting

    if token not in SESSIONS:
        return jsonify({"error": "会话过期，请重新登录"}), 401

    sess = SESSIONS[token]
    result = query_electricity(sess["jwt"], feeitemid)

    if "error" in result:
        return jsonify(result), 500

    # Parse and store
    map_data = result.get("map", {})
    show_data = map_data.get("showData", {})
    info_text = show_data.get("\u4fe1\u606f", "")  # 信息
    kwh = parse_kwh(info_text)

    # Check for balance increase (new recharge)
    balance_key = "last_balance_ac" if feeitemid == 408 else "last_balance_light"
    previous_kwh = sess.get(balance_key)
    new_recharge = None

    if kwh is not None and previous_kwh is not None:
        diff = kwh - previous_kwh
        if diff > 0.5:  # More than 0.5 kWh increase = new recharge
            new_recharge = {
                "previous_kwh": round(previous_kwh, 2),
                "current_kwh": round(kwh, 2),
                "added_kwh": round(diff, 2),
            }

    if kwh is not None:
        sess[balance_key] = kwh

    room_data = map_data.get("data", {})
    return jsonify({
        "success": True,
        "feeitemid": feeitemid,
        "building": room_data.get("buildingName", ""),
        "floor": room_data.get("floorName", ""),
        "room": room_data.get("roomName", ""),
        "remaining_kwh": kwh,
        "info_text": info_text,
        "new_recharge": new_recharge,
        "raw": result
    })


@app.route("/api/sync", methods=["POST"])
def sync_electricity():
    """Query both AC and lighting, detect new recharges."""
    data = request.get_json() or {}
    token = data.get("token", "")

    if token not in SESSIONS:
        return jsonify({"error": "会话过期"}), 401

    sess = SESSIONS[token]
    results = []

    for feeitemid, label in [(408, "空调"), (428, "照明")]:
        result = query_electricity(sess["jwt"], feeitemid)
        if "error" in result:
            results.append({"label": label, "error": result["error"]})
            continue

        map_data = result.get("map", {})
        show_data = map_data.get("showData", {})
        info_text = show_data.get("\u4fe1\u606f", "")
        kwh = parse_kwh(info_text)

        balance_key = "last_balance_ac" if feeitemid == 408 else "last_balance_light"
        previous = sess.get(balance_key)
        recharge = None

        if kwh is not None and previous is not None:
            diff = kwh - previous
            if diff > 0.3:
                recharge = {
                    "previous_kwh": round(previous, 2),
                    "current_kwh": round(kwh, 2),
                    "added_kwh": round(diff, 2),
                }

        if kwh is not None:
            sess[balance_key] = kwh

        room_data = map_data.get("data", {})
        results.append({
            "label": label,
            "feeitemid": feeitemid,
            "building": room_data.get("buildingName", ""),
            "room": room_data.get("roomName", ""),
            "remaining_kwh": kwh,
            "new_recharge": recharge,
        })

    return jsonify({"success": True, "results": results})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
