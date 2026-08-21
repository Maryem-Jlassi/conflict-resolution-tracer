import os, sys, json, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pheme_provenance_common import load_rumoureval_labels, STANCE_TO_VALUE

EVAL = r'results/empirical_evaluation/provenance/evaluation'
DATA = os.path.join(EVAL, '_data')

def true_val(v):
    if v is None:
        return None
    if v is True:
        return '1'
    if v is False:
        return '0'
    s = str(v).strip().lower()
    if s in ('1', 'true'):
        return '1'
    if s in ('0', 'false'):
        return '0'
    return None

def depth_of(data, tid):
    d = 0
    t = tid
    seen = set()
    while t in data.parent_map and data.parent_map[t] is not None:
        t = data.parent_map[t]
        d += 1
        if t in seen or d > 200:
            break
        seen.add(t)
    return d

def branch_of(data, tid):
    t = tid
    # climb to direct child of root
    while t in data.parent_map and data.parent_map[t] is not None:
        p = data.parent_map[t]
        if p not in data.parent_map:
            return t
        t = p
    return t

def root_of(data, tid):
    t = tid
    seen = set()
    while t in data.parent_map and data.parent_map[t] is not None:
        p = data.parent_map[t]
        if p in seen:
            break
        seen.add(t)
        t = p
    return t

def is_ancestor(data, anc, desc):
    t = desc
    seen = set()
    while t in data.parent_map:
        t = data.parent_map[t]
        if t == anc:
            return True
        if t in seen:
            return False
        seen.add(t)
    return False

def main():
    with open(os.path.join(DATA, 'index.pkl'), 'rb') as fh:
        data = pickle.load(fh)
    label_files = [
        os.path.join(EVAL, '_data', 'rumoureval', 'semeval2017-task8-dataset', 'traindev', 'rumoureval-subtaskA-train.json'),
        os.path.join(EVAL, '_data', 'rumoureval', 'semeval2017-task8-dataset', 'traindev', 'rumoureval-subtaskA-dev.json'),
        os.path.join(EVAL, '_data', 'external', 'subtaska.json'),
    ]
    labels = load_rumoureval_labels(label_files)

    out = {}
    # join labelled asserting tweets to threads (rumour only)
    per_thread = {}
    n_nonrumour = 0
    for tid, st in labels.items():
        tw = data.tweets.get(tid)
        if tw is None:
            continue
        thr = data.threads[tw['thread_id']]
        if not thr['is_rumour']:
            n_nonrumour += 1
            continue
        per_thread.setdefault(tw['thread_id'], []).append((tid, st))

    out['labelled_nonrumour_tweets'] = n_nonrumour
    out['rumour_threads_with_labelled'] = len(per_thread)

    thread_info = {}
    for thr_id, items in per_thread.items():
        ann = data.annotation(thr_id)
        cat = ann.get('category')
        tv = true_val(ann.get('true'))
        units = []
        for tid, st in items:
            v = STANCE_TO_VALUE.get(st)
            units.append({
                'tweet_id': tid,
                'stance': st,
                'value': v,
                'depth': depth_of(data, tid),
                'branch': branch_of(data, tid),
                'root': root_of(data, tid),
            })
        thread_info[thr_id] = {
            'event': data.threads[thr_id]['event'],
            'category': cat,
            'true': tv,
            'misinformation': ann.get('misinformation'),
            'units': units,
            'n_support': sum(1 for u in units if u['value'] == 'true'),
            'n_deny': sum(1 for u in units if u['value'] == 'false'),
            # chain: any labelled descendant pair
            'has_chain': any(is_ancestor(data, u1['tweet_id'], u2['tweet_id'])
                             for i, u1 in enumerate(units) for u2 in units[i + 1:]),
            # distinct root-level branches among labelled units
            'n_branches': len({(u['branch']) for u in units}),
        }
    out['thread_info'] = thread_info

    # category aggregates (rumour threads only)
    by_cat = {}
    for thr_id, ti in thread_info.items():
        cat = ti['category']
        by_cat.setdefault(cat, []).append(thr_id)

    cat_out = {}
    for cat, thr_ids in by_cat.items():
        tv_list = [ti['true'] for ti in (thread_info[t] for t in thr_ids) if ti['true'] is not None]
        # all rumour threads in category with true (including unlabelled)
        true_all = []
        for tid, trec in data.threads.items():
            if trec['is_rumour'] and trec['annotation'].get('category') == cat:
                tv = true_val(trec['annotation'].get('true'))
                if tv is not None:
                    true_all.append(tv)
        nd = sum(1 for x in true_all if x == '0')
        nt = sum(1 for x in true_all if x == '1')
        gold = '1' if nt > nd else ('0' if nd > nt else None)
        roots_support = {thread_info[c]['units'][j]['root'] for c in thr_ids
                         for j in range(len(thread_info[c]['units'])) if thread_info[c]['units'][j]['value'] == 'true'}
        roots_deny = {thread_info[c]['units'][j]['root'] for c in thr_ids
                      for j in range(len(thread_info[c]['units'])) if thread_info[c]['units'][j]['value'] == 'false'}
        has_chain_cat = any(thread_info[c]['has_chain'] for c in thr_ids)
        cat_out[cat] = {
            'n_threads_labelled': len(thr_ids),
            'n_roots_support': len(roots_support),
            'n_roots_deny': len(roots_deny),
            'n_true_all': nt,
            'n_false_all': nd,
            'gold': gold,
            'gold_coherence': (max(nt, nd) / len(true_all)) if true_all else None,
            'has_chain_cat': has_chain_cat,
            'n_branches_max': max((thread_info[c]['n_branches'] for c in thr_ids), default=0),
            'threads': sorted(thr_ids),
        }
    out['categories'] = cat_out

    # candidate pools
    P1 = sorted([c for c, ci in cat_out.items() if ci['gold'] is not None
                 and ci['n_roots_support'] >= 2 and ci['n_roots_deny'] == 0],
                key=lambda c: (-cat_out[c]['n_roots_support'], -len(cat_out[c]['threads'])))
    P4 = sorted([c for c, ci in cat_out.items() if ci['gold'] is not None
                 and ci['n_roots_support'] >= 1 and ci['n_roots_deny'] >= 1],
                key=lambda c: (-(cat_out[c]['n_roots_support'] + cat_out[c]['n_roots_deny']), -len(cat_out[c]['threads'])))
    P5 = sorted([c for c in P4 if cat_out[c]['has_chain_cat']],
                key=lambda c: (-(cat_out[c]['n_roots_support'] + cat_out[c]['n_roots_deny']), -len(cat_out[c]['threads'])))
    P2 = sorted([thr for thr, ti in thread_info.items()
                 if ti['n_support'] >= 1 and ti['n_deny'] >= 1 and ti['has_chain']],
                key=lambda t: (-(thread_info[t]['n_support'] + thread_info[t]['n_deny']), t))
    P3 = sorted([thr for thr, ti in thread_info.items()
                 if ti['n_support'] + ti['n_deny'] >= 2 and ti['n_branches'] >= 2],
                key=lambda t: (-(thread_info[t]['n_support'] + thread_info[t]['n_deny']), t))
    out['pools'] = {'P1': P1, 'P4': P4, 'P5': P5, 'P2': P2, 'P3': P3}

    with open(os.path.join(DATA, '_survey.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=1, default=str)
    print('P1=%d P4=%d P5=%d P2=%d P3=%d' % (len(P1), len(P4), len(P5), len(P2), len(P3)))
    print('gold coherence: ', sorted(set((str(ci['gold_coherence']) for ci in cat_out.values()))))
    print('sample P2:', P2[:5])
    print('sample P3:', P3[:5])

if __name__ == '__main__':
    main()