with open(r'D:\document\寝室电费\index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Add campus to doSyncQuery request body
old = 'var building = document.getElementById(\\'syncBuilding\\').value || \\'\\';'
new = 'var campus = document.getElementById(\\'syncCampus\\').value || \\'\\'; var building = document.getElementById(\\'syncBuilding\\').value || \\'\\';'
html = html.replace(old, new)

old2 = 'body: JSON.stringify({token: syncToken, feeitemid: 488, building: building, floor: floor, room: room})'
new2 = 'body: JSON.stringify({token: syncToken, feeitemid: 488, campus: campus, building: building, floor: floor, room: room})'
html = html.replace(old2, new2)

with open(r'D:\document\寝室电费\index.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('Frontend updated: sending campus param')
