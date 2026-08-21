with open('tests/unit/test_claim_bound_writes.py', 'r', encoding='utf-8') as f:
    text = f.read()
for i, line in enumerate(text.split('\n'), 1):
    if 'crt' in line.lower():
        print(f'{i}: {line}')
