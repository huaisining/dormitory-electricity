with open(r'D:\document\寝室电费\backend\app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace full_login with manual redirect following (like Ahu_Plus redirectClient)
old_func_start = '''def full_login(username, password):
    s = requests.Session(); s.verify = False; s.headers['User-Agent'] = UA
    ycard_entry = YCARD_BASE + '/berserker-auth/cas/login/neusoftCas?targetUrl=https://ycard.ahu.edu.cn/berserker-base/redirect?appId=16&type=app'
    r = s.get(ycard_entry, allow_redirects=False, timeout=15)
    cas_url = r.headers.get('Location', '')
    if not cas_url: return {'error': 'no CAS redirect from ycard entry'}
    r = s.get(cas_url, timeout=15)
    html = r.text
    lt_m = re.search(r'name=\"lt\"\s+value=\"([^\"]+)\"', html)
    ex_m = re.search(r'name=\"execution\"\s+value=\"([^\"]+)\"', html)
    if not lt_m or not ex_m: return {'error': 'no lt/exec'}
    lt, execution = lt_m.group(1), ex_m.group(1)
    enc = DES.str_enc(username + password + lt, '1', '2', '3')
    s.post(CAS_BASE + '/device', data={'ul': str(len(username)), 'pl': str(len(password)), 'rsa': enc, 'method': 'login'},
        headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json, text/javascript, */*; q=0.01'})
    r = s.post(cas_url, data={'rsa': enc, 'ul': str(len(username)), 'pl': str(len(password)), 'lt': lt, 'execution': execution, '_eventId': 'submit'},
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Referer': cas_url}, allow_redirects=True, timeout=30)
    jwt_m = re.search(r'synjones-auth=([^&\"\s]+)', r.url)
    if jwt_m: return {'success': True, 'jwt': jwt_m.group(1)}
    jwt_m2 = re.search(r'synjones-auth=([^&\"\s]+)', r.text[:10000])
    if jwt_m2: return {'success': True, 'jwt': jwt_m2.group(1)}
    return {'error': 'no synjones-auth JWT', 'final_url': r.url[:300]}'''

func_end_marker = '''def api_call(jwt, form_data):'''

old_func = content[content.find(old_func_start):content.find(func_end_marker)]

new_func = '''def full_login(username, password):
    """Exact copy of Ahu_Plus performFullLogin: manual redirect following."""
    s = requests.Session(); s.verify = False; s.headers['User-Agent'] = UA

    # Step 1: GET ycard CAS entry -> 302 -> CAS login URL
    ycard_entry = YCARD_BASE + '/berserker-auth/cas/login/neusoftCas?targetUrl=https://ycard.ahu.edu.cn/berserker-base/redirect?appId=16&type=app'
    r = s.get(ycard_entry, allow_redirects=False, timeout=15)
    cas_url = r.headers.get('Location', '')
    if not cas_url:
        return {'error': 'no CAS redirect from ycard entry', 'step': 1}

    # Step 2: GET CAS login page -> extract lt + execution
    r = s.get(cas_url, timeout=15)
    html = r.text
    lt_m = re.search(r'name="lt"\s+value="([^"]+)"', html)
    ex_m = re.search(r'name="execution"\s+value="([^"]+)"', html)
    if not lt_m or not ex_m:
        return {'error': 'no lt/exec on CAS page', 'step': 2, 'html_snippet': html[:500]}
    lt, execution = lt_m.group(1), ex_m.group(1)

    # Step 3: DES encrypt
    enc = DES.str_enc(username + password + lt, '1', '2', '3')

    # Step 4: device pre-validation
    s.post(CAS_BASE + '/device',
        data={'ul': str(len(username)), 'pl': str(len(password)), 'rsa': enc, 'method': 'login'},
        headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json, text/javascript, */*; q=0.01'})

    # Step 5: POST CAS form, manually follow redirects (Ahu_Plus style)
    r = s.post(cas_url,
        data={'rsa': enc, 'ul': str(len(username)), 'pl': str(len(password)), 'lt': lt, 'execution': execution, '_eventId': 'submit'},
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Referer': cas_url},
        allow_redirects=False, timeout=30)

    chain = []
    next_url = r.headers.get('Location', '')
    chain.append(f'POST:{r.status_code}->{next_url[:100] if next_url else r.url[:100]}')

    # Follow redirect chain manually (max 10 hops)
    for i in range(10):
        if not next_url:
            chain.append(f'STOP:no_location at {r.url[:100]}')
            break
        r = s.get(next_url, allow_redirects=False, timeout=15)
        # Check for JWT in current URL
        jwt_m = re.search(r'synjones-auth=([^&"\s]+)', r.url)
        if not jwt_m:
            jwt_m = re.search(r'synjones-auth=([^&"\s]+)', r.text[:5000])
        if jwt_m:
            return {'success': True, 'jwt': jwt_m.group(1), 'chain': chain}
        next_url = r.headers.get('Location', '')
        chain.append(f'GET:{r.status_code}->{next_url[:120] if next_url else "stop:"+r.url[:100]}')

    # Check if we already have JWT in the last response URL
    jwt_m = re.search(r'synjones-auth=([^&"\s]+)', r.url)
    if jwt_m:
        return {'success': True, 'jwt': jwt_m.group(1)}

    return {'error': 'no synjones-auth JWT', 'chain': chain, 'final_url': r.url[:300], 'html_snippet': r.text[:500]}'''

content = content.replace(old_func, new_func)

with open(r'D:\document\寝室电费\backend\app.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Remove BOM
raw = open(r'D:\document\寝室电费\backend\app.py', 'rb').read()
if raw[:3] == b'\xef\xbb\xbf':
    open(r'D:\document\寝室电费\backend\app.py', 'wb').write(raw[3:])

# Verify syntax
import py_compile
try:
    py_compile.compile(r'D:\document\寝室电费\backend\app.py', doraise=True)
    print('Syntax OK')
except py_compile.PyCompileError as e:
    print(f'Syntax error: {e}')
