"""CAS + YCard authentication (proven working 2026-06-26)."""
import re
import requests
import urllib3
urllib3.disable_warnings()

from .des_ahu import DES

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36"
CAS_BASE = "https://one.ahu.edu.cn/cas"
YCARD_BASE = "https://ycard.ahu.edu.cn"
# CRITICAL: targetUrl MUST be URL-encoded (matches Ahu_Plus YCARD_CAS_ENTRY)
Y_ENTRY = YCARD_BASE + "/berserker-auth/cas/login/neusoftCas" \
    + "?targetUrl=https%3A%2F%2Fycard.ahu.edu.cn%2Fberserker-base%2Fredirect%3FappId%3D16%26type%3Dapp"


def raw_login(username, password):
    s = requests.Session()
    s.verify = False
    s.headers["User-Agent"] = UA

    r = s.get(Y_ENTRY, allow_redirects=False, timeout=15)
    cas_url = r.headers.get("Location", "")
    if not cas_url:
        return {"error": "no redirect from ycard entry"}

    r = s.get(cas_url, timeout=15)
    lt_m = re.search(r'name="lt"\s+value="([^"]+)"', r.text)
    ex_m = re.search(r'name="execution"\s+value="([^"]+)"', r.text)
    if not lt_m or not ex_m:
        return {"error": "no lt/exec"}
    lt = lt_m.group(1)
    execution = ex_m.group(1)

    enc = DES.str_enc(username + password + lt, "1", "2", "3")

    s.post(CAS_BASE + "/device",
        data={"ul": str(len(username)), "pl": str(len(password)), "rsa": enc, "method": "login"},
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})

    r = s.post(cas_url,
        data={"rsa": enc, "ul": str(len(username)), "pl": str(len(password)),
              "lt": lt, "execution": execution, "_eventId": "submit"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=False, timeout=30)

    next_url = r.headers.get("Location", "")
    for _ in range(10):
        if not next_url:
            break
        r = s.get(next_url, allow_redirects=False, timeout=15)
        jwt_m = re.search(r"synjones-auth=([^&\"\s]+)", r.url)
        if not jwt_m:
            jwt_m = re.search(r"synjones-auth=([^&\"\s]+)", r.text[:5000])
        if jwt_m:
            return {"success": True, "jwt": jwt_m.group(1)}
        next_url = r.headers.get("Location", "")

    return {"error": "no synjones-auth JWT"}


def ycall(jwt, form):
    h = {
        "User-Agent": UA,
        "synjones-auth": f"bearer {jwt}",
        "Accept": "application/json, text/plain, */*",
        "Origin": YCARD_BASE,
        "Referer": f"{YCARD_BASE}/charge-app/"
    }
    try:
        r = requests.post(f"{YCARD_BASE}/charge/feeitem/getThirdData", data=form, headers=h, verify=False, timeout=15)
        return r.json() if r.status_code == 200 else {"_err": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"_err": str(e)}


def parse_kwh(info_str):
    if not info_str:
        return None
    m = re.search(r"(\d+\.?\d*)\s*度", info_str)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+\.?\d*)", info_str)
    return float(m.group(1)) if m else None


full_login = raw_login