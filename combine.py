with open(r'D:\document\寝室电费\index_part1.txt', 'r', encoding='utf-8') as f:
    part1 = f.read()
with open(r'D:\document\寝室电费\index_js.txt', 'r', encoding='utf-8') as f:
    part2 = f.read()

full = part1 + part2

with open(r'D:\document\寝室电费\index.html', 'w', encoding='utf-8') as f:
    f.write(full)

# Verify
with open(r'D:\document\寝室电费\index.html', 'r', encoding='utf-8') as f:
    check = f.read()

print('Total size:', len(check), 'bytes')
print('Has 寝室:', '\u5bdd\u5ba4' in check)
print('Has 同步:', '\u540c\u6b65' in check)
print('Has script:', '</script>' in check)

# Clean up temp files
import os
for f in ['index_part1.txt', 'index_js.txt', 'build1.py', 'build2.py', 'build_html.py', 'fix_encoding.py']:
    path = os.path.join(r'D:\document\寝室电费', f)
    if os.path.exists(path):
        os.remove(path)
        print('Removed:', f)
