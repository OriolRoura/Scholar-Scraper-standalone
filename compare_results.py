import json
from pathlib import Path

p = Path(__file__).parent
old_f = p / 'results2.json'
new_f = p / 'results.json'
old = json.loads(old_f.read_text(encoding='utf-8'))
new = json.loads(new_f.read_text(encoding='utf-8'))

out = {'top_level_missing_in_new': [], 'top_level_added_in_new': [], 'pubs': []}

# top-level keys compare on first element (author object)
old_keys = set(old[0].keys()) if old else set()
new_keys = set(new[0].keys()) if new else set()
out['top_level_missing_in_new'] = sorted(list(old_keys - new_keys))
out['top_level_added_in_new'] = sorted(list(new_keys - old_keys))

# Build map by author_pub_id or title for publications
def build_map(lst):
    m = {}
    for pub in lst:
        pid = pub.get('author_pub_id') or pub.get('title')
        if pid in m:
            # keep first occurrence
            continue
        m[pid] = pub
    return m

old_map = build_map(old[0].get('publications', [])) if old else {}
new_map = build_map(new[0].get('publications', [])) if new else {}

# Analyze all keys seen across both sets
all_pids = set(old_map.keys()) | set(new_map.keys())
for pid in sorted(all_pids):
    o = old_map.get(pid)
    n = new_map.get(pid)
    entry = {'id': pid, 'present_in_old': bool(o), 'present_in_new': bool(n), 'missing_keys_in_new': [], 'added_keys_in_new': []}
    if o and n:
        ok = set(o.keys())
        nk = set(n.keys())
        entry['missing_keys_in_new'] = sorted(list(ok - nk))
        entry['added_keys_in_new'] = sorted(list(nk - ok))
    out['pubs'].append(entry)

print(json.dumps(out, indent=2, ensure_ascii=False))
