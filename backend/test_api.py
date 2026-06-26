import requests, urllib3, json, re, sys, os
sys.path.insert(0, r'D:\document\寝室电费\backend')
urllib3.disable_warnings()
from des_ahu import DES

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0.0.0 Safari/537.36'

# Read password from file or prompt
pwd_file = r'D:\document\寝室电费\backend\pwd.txt'
if os.path.exists(pwd_file):
    with open(pwd_file) as f:
        pwd = f.read().strip()
else:
    pwd = input('Password: ')

def login(u, p):
    s = requests.Session(); s.verify = False; s.headers['User-Agent'] = UA
    
    ycard_entry = 'https://ycard.ahu.edu.cn/berserker-auth/cas/login/neusoftCas?targetUrl=https://ycard.ahu.edu.cn/berserker-base/redirect?appId=16&type=app'
    r = s.get(ycard_entry, allow_redirects=False, timeout=15)
    cas_url = r.headers.get('Location', '')
    print('CAS URL:', cas_url[:80])
    
    r = s.get(cas_url, timeout=15)
    html = r.text
    lt_m = re.search(r'name="lt"\s+value="([^"]+)"', html)
    ex_m = re.search(r'name="execution"\s+value="([^"]+)"', html)
    lt, execution = lt_m.group(1), ex_m.group(1)
    print('Got lt/exec')
    
    enc = DES.str_enc(u + p + lt, '1', '2', '3')
    s.post('https://one.ahu.edu.cn/cas/device',
        data={'ul': str(len(u)), 'pl': str(len(p)), 'rsa': enc, 'method': 'login'},
        headers={'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    
    r = s.post(cas_url,
        data={'rsa': enc, 'ul': str(len(u)), 'pl': str(len(p)), 'lt': lt, 'execution': execution, '_eventId': 'submit'},
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Referer': cas_url},
        allow_redirects=True, timeout=30)
    
    jwt_m = re.search(r'synjones-auth=([^&\"\s]+)', r.url)
    if jwt_m:
        jwt = jwt_m.group(1)
        print('JWT obtained:', jwt[:20]+'...')
        return jwt, s
    print('No JWT. Final URL:', r.url[:200])
    return None, s

jwt, session = login('D124301061', pwd)
if not jwt:
    print('Login failed')
    sys.exit(1)

headers = {
    'User-Agent': UA,
    'synjones-auth': f'bearer {jwt}',
    'Accept': 'application/json',
    'Origin': 'https://ycard.ahu.edu.cn',
    'Referer': 'https://ycard.ahu.edu.cn/charge-app/'
}

# Test cascade: level 0 (campuses)
r = requests.post('https://ycard.ahu.edu.cn/charge/feeitem/getThirdData',
    data={'feeitemid': '488', 'type': 'select', 'level': '0'},
    headers=headers, verify=False, timeout=15)
data = r.json()
print('\n=== LEVEL 0: Campuses ===')
items = data.get('map', {}).get('data', [])
print('Count:', len(items))
if items:
    print('First item:', json.dumps(items[0], ensure_ascii=False))
    print('Keys:', list(items[0].keys()))
    for item in items[:5]:
        print(f'  {item}')

# Save first campus for next level
if items:
    campus_id = items[0].get('id', items[0].get('value', ''))
    print('\nUsing campus:', campus_id)
    
    # Level 1: buildings
    r = requests.post('https://ycard.ahu.edu.cn/charge/feeitem/getThirdData',
        data={'feeitemid': '488', 'type': 'select', 'level': '1', 'campus': campus_id},
        headers=headers, verify=False, timeout=15)
    data = r.json()
    print('\n=== LEVEL 1: Buildings ===')
    items = data.get('map', {}).get('data', [])
    print('Count:', len(items))
    if items:
        print('First item:', json.dumps(items[0], ensure_ascii=False))
        for item in items[:5]:
            print(f'  {item}')
        
        building_id = items[0].get('id', items[0].get('value', ''))
        
        # Level 2: floors
        r = requests.post('https://ycard.ahu.edu.cn/charge/feeitem/getThirdData',
            data={'feeitemid': '488', 'type': 'select', 'level': '2', 'campus': campus_id, 'building': building_id},
            headers=headers, verify=False, timeout=15)
        data = r.json()
        print('\n=== LEVEL 2: Floors ===')
        items = data.get('map', {}).get('data', [])
        print('Count:', len(items))
        if items:
            print('First item:', json.dumps(items[0], ensure_ascii=False))
            for item in items[:5]:
                print(f'  {item}')
            
            floor_id = items[0].get('id', items[0].get('value', ''))
            
            # Level 3: rooms
            r = requests.post('https://ycard.ahu.edu.cn/charge/feeitem/getThirdData',
                data={'feeitemid': '488', 'type': 'select', 'level': '3', 'campus': campus_id, 'building': building_id, 'floor': floor_id},
                headers=headers, verify=False, timeout=15)
            data = r.json()
            print('\n=== LEVEL 3: Rooms ===')
            items = data.get('map', {}).get('data', [])
            print('Count:', len(items))
            if items:
                print('First item:', json.dumps(items[0], ensure_ascii=False))
                for item in items[:3]:
                    print(f'  {item}')
                
                room_id = items[0].get('id', items[0].get('value', ''))
                
                # Test electricity query with these exact values
                print('\n=== ELECTRICITY QUERY ===')
                print(f'Params: feeitemid=488 campus={campus_id} building={building_id} floor={floor_id} room={room_id}')
                r = requests.post('https://ycard.ahu.edu.cn/charge/feeitem/getThirdData',
                    data={'feeitemid': '488', 'type': 'IEC', 'level': '3', 'campus': campus_id, 'building': building_id, 'floor': floor_id, 'room': room_id},
                    headers=headers, verify=False, timeout=15)
                result = r.json()
                print('Response:', json.dumps(result, ensure_ascii=False, indent=2)[:2000])
