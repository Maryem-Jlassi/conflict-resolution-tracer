import os, re
from collections import Counter

lcm_files = []
ext_counts = Counter()
for root, dirs, files in os.walk('.'):
    if '.git' in dirs:
        dirs.remove('.git')
    for f in files:
        path = os.path.join(root, f)
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                content = fh.read()
            if re.search(r'\blcm\b', content, re.IGNORECASE) or 'crt' in f.lower():
                lcm_files.append(path)
                ext_counts[f.split('.')[-1]] += 1
        except:
            pass

print(f'Total files with crt references: {len(lcm_files)}')
print(f'By extension: {dict(ext_counts)}')
print()
print('Files:')
for p in sorted(lcm_files):
    print(f'  {p}')
