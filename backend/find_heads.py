import os
import re

versions_dir = 'alembic/versions'
migrations = {}

for f in os.listdir(versions_dir):
    if f.endswith('.py'):
        path = os.path.join(versions_dir, f)
        with open(path) as fp:
            content = fp.read()
            rev_match = re.search(r"revision: str = '([^']+)'", content)
            down_match = re.search(r"down_revision: Union\[str, Sequence\[str\], None\] = '([^']+)'", content)
            if not down_match:
                down_match = re.search(r"down_revision: Union\[str, Sequence\[str\], None\] = ([^\n]+)", content)
            if rev_match:
                rev = rev_match.group(1)
                down = down_match.group(1) if down_match else None
                # Clean up down_revision
                if down:
                    down = down.strip().strip("'").strip('"').strip()
                    if down == 'None':
                        down = None
                migrations[rev] = {'file': f, 'down_revision': down}

# Find heads (revisions that no other migration points to)
all_revs = set(migrations.keys())
referenced = set(m['down_revision'] for m in migrations.values() if m['down_revision'])
heads = all_revs - referenced

print('HEADS (no children):')
for h in heads:
    print(f'  {h}: {migrations[h]["file"]}')

print()
print('Full chain from base:')
def print_chain(rev, indent=0):
    if rev not in migrations:
        return
    m = migrations[rev]
    print('  ' * indent + f'{rev}: {m["file"]}')
    for r, m2 in migrations.items():
        if m2['down_revision'] == rev:
            print_chain(r, indent + 1)

for rev, m in migrations.items():
    if m['down_revision'] is None:
        print_chain(rev)