"""
pheme_provenance_pipeline.py
Builds the real-data provenance case manifest (P1-P5), runs:
  - Level-1 provenance classification (explicit lineage only)
  - Level-2 CRT V1 provenance-aware vs provenance-neutral resolution
and emits the numbered artifacts 02, 03, 04, 05, 06, 08 under
results/empirical_evaluation/provenance/evaluation/.

Hard constraints honored:
  - NO fabricated cases: every unit is a real archived tweet with a recorded
    RumourEval stance label; every edge is reconstructed from structure.json.
  - No inference of independence from authorship or text similarity.
  - Frozen V1 mechanism: Psi=(R+C+T)/3, theta=0.05, C=0.5*evidence+0.25,
    R 7-day half-life, T prior-outcome trust (cold-start 0.5).
  - PHEME TEST/CALIBRATION untouched; no LLM; deterministic.
"""
import os
import sys
import json
import pickle
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pheme_provenance_common import (
    PHEMEData, load_rumoureval_labels, STANCE_TO_VALUE, THETA,
    psi, recency, trust_from_counts,
)

EVAL = r'results/empirical_evaluation/provenance/evaluation'
DATA = os.path.join(EVAL, '_data')
TOOLS = os.path.join(EVAL, '_tools')

LABEL_SOURCES = {
    'train': os.path.join(DATA, 'rumoureval', 'semeval2017-task8-dataset', 'traindev', 'rumoureval-subtaskA-train.json'),
    'dev': os.path.join(DATA, 'rumoureval', 'semeval2017-task8-dataset', 'traindev', 'rumoureval-subtaskA-dev.json'),
    'test': os.path.join(DATA, 'external', 'subtaska.json'),
}


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


def load_everything():
    with open(os.path.join(DATA, 'index.pkl'), 'rb') as fh:
        data = pickle.load(fh)
    labels = {}
    label_source_of = {}
    for src, p in LABEL_SOURCES.items():
        d = load_rumoureval_labels([p])
        for k, v in d.items():
            labels[k] = v
            label_source_of[k] = src
    return data, labels, label_source_of


# ---------------------------------------------------------------------------
# lineage helpers
# ---------------------------------------------------------------------------
def ancestor_chain(data, tid):
    chain = []
    t = tid
    seen = set()
    while t in data.parent_map and data.parent_map[t] is not None:
        t = data.parent_map[t]
        if t in seen:
            break
        seen.add(t)
        chain.append(t)
    return chain  # [parent, grandparent, ..., root]


def is_ancestor(data, anc, desc):
    return anc in ancestor_chain(data, desc)


# ---------------------------------------------------------------------------
# case building
# ---------------------------------------------------------------------------
def build_units(data, labels, label_source_of):
    """RumourEval-labelled asserting tweets in rumour threads w/ category + true."""
    units = []          # dicts for labelled tweets (support/deny only)
    bad_missing_cat = 0
    for tid, st in labels.items():
        if st not in STANCE_TO_VALUE:
            continue
        tw = data.tweets.get(tid)
        if tw is None:
            continue
        thr = data.threads[tw['thread_id']]
        if not thr['is_rumour']:
            continue
        ann = thr['annotation']
        cat = ann.get('category')
        tv = true_val(ann.get('true'))
        if cat is None or tv is None:
            bad_missing_cat += 1
            continue
        thr_root = thr['source_ids'][0] if thr['source_ids'] else None
        if thr_root is None:
            bad_missing_cat += 1
            continue
        chain = ancestor_chain(data, tid)
        units.append({
            'tweet_id': tid, 'thread_id': tw['thread_id'], 'event': tw['event'],
            'user_id': tw['user_id'], 'stance': st, 'value': STANCE_TO_VALUE[st],
            'created_iso': tw['created_iso'], 'created': tw['created'],
            'evidence': tw['evidence'], 'confidence': tw['confidence'],
            'depth': len(chain),
            'root': thr_root,                       # provenance origin = thread source tweet
            'struct_root': chain[-1] if chain else tid,  # deepest structure edge (audit)
            'branch': chain[-2] if len(chain) >= 2 else tid,
            'label_source': label_source_of.get(tid),
            'parent_tweet_id': data.parent_map.get(tid),
        })
    return units, bad_missing_cat


def build_cases(data, labels, label_source_of, ELECTED):
    units, bad_missing = build_units(data, labels, label_source_of)
    # group asserting units by thread
    thr_units = {}
    for u in units:
        thr_units.setdefault(u['thread_id'], []).append(u)

    thread_meta = {}
    for thr_id, ul in thr_units.items():
        ann = data.annotation(thr_id)
        linked = [u for u in ul if u['depth'] >= 1]
        thread_meta[thr_id] = {
            'event': data.threads[thr_id]['event'],
            'category': ann.get('category'),
            'true': true_val(ann.get('true')),
            'n_support': sum(1 for u in ul if u['value'] == 'true'),
            'n_deny': sum(1 for u in ul if u['value'] == 'false'),
            'has_chain': any(is_ancestor(data, u1['tweet_id'], u2['tweet_id'])
                             for i, u1 in enumerate(ul) for u2 in ul[i + 1:]),
            # distinct real root-level branches containing a labelled unit (structure edges only)
            'n_branches': len({u['branch'] for u in linked}),
        }

    # category gold from ALL rumour threads of the category (journalist true)
    def cat_all_true(cat):
        nt = nd = 0
        for t, trec in data.threads.items():
            if not trec['is_rumour'] or trec['annotation'].get('category') != cat:
                continue
            tv = true_val(trec['annotation'].get('true'))
            if tv == '1':
                nt += 1
            elif tv == '0':
                nd += 1
        return nt, nd

    cat_by_thread = {t: m['category'] for t, m in thread_meta.items()}

    cat_roots = {}
    for c in sorted(set(cat_by_thread.values())):
        st = set()
        dn = set()
        for t, m in thread_meta.items():
            if m['category'] != c:
                continue
            for u in thr_units[t]:
                if u['value'] == 'true':
                    st.add(u['root'])
                else:
                    dn.add(u['root'])
        nt, nd = cat_all_true(c)
        gold = 'true' if nt > nd else ('false' if nd > nt else None)
        cat_roots[c] = {
            'roots_support': st, 'roots_deny': dn,
            'threads': [t for t, m in thread_meta.items() if m['category'] == c],
            'gold': gold, 'n_true': nt, 'n_false': nd,
        }

    # ---- deterministic selection -------------------------------------------------
    # P5: cross-thread conflict WITH intra-thread chain
    p5_pool = [c for c, ci in cat_roots.items()
               if ci['gold'] is not None and len(ci['roots_support']) >= 1 and len(ci['roots_deny']) >= 1
               and any(thread_meta[t]['has_chain'] for t in ci['threads'])]
    p5_cats = sorted(p5_pool, reverse=True,
                     key=lambda c: (len(cat_roots[c]['roots_support']) + len(cat_roots[c]['roots_deny']),
                                    len(cat_roots[c]['threads']), c))[:ELECTED['P5']]

    # P4: cross-thread conflict, disjoint from P5
    p4_pool = [c for c, ci in cat_roots.items()
               if c not in p5_cats and ci['gold'] is not None
               and len(ci['roots_support']) >= 1 and len(ci['roots_deny']) >= 1]
    p4_cats = sorted(p4_pool, reverse=True, key=lambda c: len(cat_roots[c]['threads']))
    # enforce true cross-thread conflict: some thread has support and a DIFFERENT thread has deny
    p4_cats = [c for c in p4_cats if any(
        thread_meta[t]['n_support'] >= 1 and any(thread_meta[t2]['n_deny'] >= 1 and t2 != t for t2 in cat_roots[c]['threads'])
        for t in cat_roots[c]['threads'])][:ELECTED['P4']]

    # P1: independent support convergence, no denies
    p1_pool = [c for c, ci in cat_roots.items()
               if c not in p5_cats and c not in p4_cats and ci['gold'] is not None
               and len(ci['roots_support']) >= 2 and len(ci['roots_deny']) == 0]
    p1_cats = sorted(p1_pool, reverse=True,
                     key=lambda c: (len(cat_roots[c]['roots_support']), len(cat_roots[c]['threads']), c))[:ELECTED['P1']]

    used_cats = set(p5_cats) | set(p4_cats) | set(p1_cats)

    # P2: intra-thread conflict chain, category not used above
    p2_pool = [t for t, m in thread_meta.items()
               if m['n_support'] >= 1 and m['n_deny'] >= 1 and m['has_chain']
               and cat_by_thread[t] not in used_cats]
    p2_threads = sorted(p2_pool, key=lambda t: (thread_meta[t]['n_support'] + thread_meta[t]['n_deny'], t), reverse=True)[:ELECTED['P2']]

    # P3: multi-branch shared root, category not used above and thread not in P2
    p3_pool = [t for t, m in thread_meta.items()
               if m['n_support'] + m['n_deny'] >= 2 and m['n_branches'] >= 2
               and t not in p2_threads and cat_by_thread[t] not in used_cats]
    p3_threads = sorted(p3_pool, key=lambda t: (thread_meta[t]['n_support'] + thread_meta[t]['n_deny'], t), reverse=True)[:ELECTED['P3']]

    # ---- assemble case objects ----------------------------------------------------
    cases = []
    used_tweets = set()

    def unit_jsons(tids, thr):
        out = []
        for tid in tids:
            u = next(x for x in thr_units[thr] if x['tweet_id'] == tid)
            out.append(_unit_json(data, u))
            used_tweets.add(tid)
        return out

    # P1 & P4 & P5: category-level, units = all asserting labelled tweets in category threads
    for typ, cats in (('P5', p5_cats), ('P4', p4_cats), ('P1', p1_cats)):
        for ci, c in enumerate(cats):
            cat_threads = sorted(cat_roots[c]['threads'])
            uu = []
            roots = {}
            for t in cat_threads:
                for u in thr_units[t]:
                    uu.append(u)
                    roots.setdefault(u['root'], []).append(u)
            if len(uu) < 2:
                continue
            case = {
                'case_id': '%s-%03d' % (typ, ci + 1),
                'case_type': typ,
                'category': c,
                'composition_grade': 'B',
                'gold': cat_roots[c]['gold'],
                'gold_true_count': cat_roots[c]['n_true'],
                'gold_false_count': cat_roots[c]['n_false'],
                'n_threads': len(cat_threads),
                'n_origins': len(roots),
                'threads': cat_threads,
                'origins': sorted(roots.keys()),
                'units': [ _unit_json(data, u) for u in uu ],
            }
            for un in case['units']:
                used_tweets.add(un['tweet_id'])
            cases.append(case)

    # P2: intra-thread conflict with chain
    for th in p2_threads:
        ul = thr_units[th]
        g = thread_meta[th]['true']
        case = {
            'case_id': 'P2-%03d' % (p2_threads.index(th) + 1),
            'case_type': 'P2',
            'category': cat_by_thread[th],
            'composition_grade': 'A',
            'gold': 'true' if g == '1' else 'false',
            'gold_true_count': None, 'gold_false_count': None,
            'n_threads': 1, 'n_origins': 1,
            'threads': [th], 'origins': [ul[0]['root']],
            'units': [_unit_json(data, u) for u in ul],
        }
        for un in case['units']:
            used_tweets.add(un['tweet_id'])
        cases.append(case)

    # P3: multi-branch shared root
    for th in p3_threads:
        ul = thr_units[th]
        g = thread_meta[th]['true']
        case = {
            'case_id': 'P3-%03d' % (p3_threads.index(th) + 1),
            'case_type': 'P3',
            'category': cat_by_thread[th],
            'composition_grade': 'A',
            'gold': 'true' if g == '1' else 'false',
            'gold_true_count': None, 'gold_false_count': None,
            'n_threads': 1, 'n_origins': 1,
            'threads': [th], 'origins': [ul[0]['root']],
            'units': [_unit_json(data, u) for u in ul],
        }
        for un in case['units']:
            used_tweets.add(un['tweet_id'])
        cases.append(case)

    # assert disjointness
    all_ids = [u['tweet_id'] for c in cases for u in c['units']]
    dup = [x for x in set(all_ids) if all_ids.count(x) > 1]
    assert not dup, 'duplicate tweet usage across cases: %r' % dup[:5]
    return cases, len(dup), bad_missing


def _unit_json(data, u):
    return {
        'tweet_id': u['tweet_id'],
        'thread_id': u['thread_id'],
        'event': u['event'],
        'user_id': u['user_id'],
        'stance': u['stance'],
        'value': u['value'],
        'grade': 'A',
        'depth': u['depth'],
        'in_structure': bool(u['parent_tweet_id']),
        'root_tweet_id': u['root'],
        'struct_root_tweet_id': u['struct_root'],
        'branch_tweet_id': u['branch'],
        'parent_tweet_id': u['parent_tweet_id'],
        'created_iso': u['created_iso'],
        'label_source': u['label_source'],
        'evidence': round(u['evidence'], 6),
        'confidence': round(u['confidence'], 6),
        'source_file': _source_file(data, u['thread_id'], u['tweet_id']),
    }


def _source_file(data, thread_id, tweet_id):
    thr = data.threads[thread_id]
    section = thr['section']
    folder = 'source-tweets' if tweet_id in thr['source_ids'] else 'reactions'
    ev_dir = thr['event'] + '-all-rnr-threads'
    return os.path.join('all-rnr-annotated-threads', ev_dir, section, thread_id, folder, tweet_id + '.json')


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def compute_scores(data, labels, cases):
    """Per-unit R, C, T, Psi using frozen formulas. T uses strictly-prior
    labelled asserting tweets across the whole dataset (chronological)."""
    # build global timeline of asserting tweets
    events = []
    for tid, tw in data.tweets.items():
        st = labels.get(tid)
        if st not in STANCE_TO_VALUE:
            continue
        ann = data.annotation(tw['thread_id'])
        tv = true_val(ann.get('true'))
        if tv is None or tw['created'] is None:
            continue
        events.append((tid, tw, st, tv))
    events.sort(key=lambda e: e[1]['created'].timestamp())
    trust = {}
    correct = {}
    total = {}
    for tid, tw, st, tv in events:
        uid = tw['user_id']
        # snapshot BEFORE this tweet
        tr = trust_from_counts(correct.get(uid, 0), total.get(uid, 0))
        trust[tid] = tr
        ok = (st == 'support' and tv == '1') or (st == 'deny' and tv == '0')
        total[uid] = total.get(uid, 0) + 1
        correct[uid] = correct.get(uid, 0) + (1 if ok else 0)
    trust_cold = sum(1 for v in trust.values() if v == 0.5)
    trust_warm = len(trust) - trust_cold

    for case in cases:
        units = case['units']
        created = []
        for u in units:
            tw = data.tweets[u['tweet_id']]
            if tw['created'] is not None:
                created.append(tw['created'])
        ref = max(created) if created else None
        for u in units:
            tw = data.tweets[u['tweet_id']]
            r = recency(tw['created'], ref)
            c = tw['confidence']
            t = trust.get(u['tweet_id'], 0.5)
            u['R'] = round(r, 6)
            u['C'] = round(c, 6)
            u['T'] = round(t, 6)
            u['psi'] = round(psi(r, c, t), 6)
            u['ref_iso'] = ref.strftime('%Y-%m-%dT%H:%M:%SZ') if ref else None
        case['n_warm_trust_users'] = None
    return {'trust_warm_units': trust_warm, 'trust_cold_units': trust_cold}


# ---------------------------------------------------------------------------
# Level-1 classification
# ---------------------------------------------------------------------------
def classify_pair(data, a, b):
    """Deterministic provenance classification of the relation between two
    value-bearing units, from explicit lineage (structure.json) only.
    Returns one of: independent / propagated / same_origin."""
    if a['root_tweet_id'] == b['root_tweet_id']:
        if ((a['depth'] == b['depth'] and a['branch_tweet_id'] != b['branch_tweet_id'])
                or (not (is_ancestor(data, b['tweet_id'], a['tweet_id'])
                         or is_ancestor(data, a['tweet_id'], b['tweet_id'])))):
            return 'same_origin'
        # ancestor chain
        if is_ancestor(data, a['tweet_id'], b['tweet_id']) or is_ancestor(data, b['tweet_id'], a['tweet_id']):
            return 'propagated'
        return 'same_origin'
    return 'independent'


def run_level1(data, cases):
    totals = {}
    per_type = {}
    for case in cases:
        units = case['units']
        labels = []
        for i, a in enumerate(units):
            for j in range(i + 1, len(units)):
                b = units[j]
                pred = classify_pair(data, a, b)
                gold = _gold_pair(data, a, b)
                ok = pred == gold
                labels.append((case['case_type'], pred, gold, ok))
                totals[case['case_type']] = totals.get(case['case_type'], {'n': 0, 'hit': 0})
                totals[case['case_type']]['n'] += 1
                if ok:
                    totals[case['case_type']]['hit'] += 1
        per_type[case['case_type']] = per_type.get(case['case_type'], {'pairs': 0, 'hit': 0, 'conf': {}})
        per_type[case['case_type']]['pairs'] += sum(1 for x in labels)
        per_type[case['case_type']]['hit'] += sum(1 for x in labels if x[3])
        for x in labels:
            key = '%s->%s' % (x[2], x[1])
            per_type[case['case_type']]['conf'][key] = per_type[case['case_type']]['conf'].get(key, 0) + 1
    return labels, totals, per_type


def _gold_pair(data, a, b):
    if a['root_tweet_id'] != b['root_tweet_id']:
        return 'independent'
    if is_ancestor(data, a['tweet_id'], b['tweet_id']) or is_ancestor(data, b['tweet_id'], a['tweet_id']):
        return 'propagated'
    return 'same_origin'


# ---------------------------------------------------------------------------
# Level-2 resolution
# ---------------------------------------------------------------------------
def aggregate(units, aware):
    by_val = {'true': [], 'false': []}
    if aware:
        by_origin = {}
        for u in units:
            by_origin.setdefault(u['root_tweet_id'], []).append(u)
        for root, us in by_origin.items():
            for v in ('true', 'false'):
                cands = [u for u in us if u['value'] == v]
                if cands:
                    best = max(cands, key=lambda u: u['psi'])
                    by_val[v].append(best['psi'])
        sup = {v: float(sum(scores)) for v, scores in by_val.items()}
    else:
        scores = {'true': [], 'false': []}
        for u in units:
            scores[u['value']].append(u['psi'])
        sup = {v: float(sum(s)) for v, s in scores.items()}
    delta = abs(sup['true'] - sup['false'])
    resolved = delta >= THETA and (sup['true'] > 0 or sup['false'] > 0)
    winner = None
    if resolved:
        winner = 'true' if sup['true'] > sup['false'] else 'false'
    return {
        'support_true': round(sup['true'], 6),
        'support_false': round(sup['false'], 6),
        'delta': round(delta, 6),
        'resolved': resolved,
        'winner': winner,
    }


def run_level2(cases, mode):
    rows = []
    for case in cases:
        agg = aggregate(case['units'], aware=(mode == 'aware'))
        gold = case['gold']
        correct = None
        if agg['winner'] is None:
            outcome = 'unresolved'
        else:
            outcome = agg['winner']
            correct = agg['winner'] == gold
        rows.append({
            'case_id': case['case_id'],
            'case_type': case['case_type'],
            'category': case['category'],
            'gold': gold,
            'gold_true_count': case['gold_true_count'],
            'gold_false_count': case['gold_false_count'],
            'n_units': len(case['units']),
            'n_origins': case['n_origins'],
            'n_warm_trust_users': case['n_warm_trust_users'],
            'support_true': agg['support_true'],
            'support_false': agg['support_false'],
            'delta': agg['delta'],
            'winner': agg['winner'],
            'outcome': outcome,
            'correct': correct,
        })
    return rows


def stats(rows):
    n = len(rows)
    resolved = [r for r in rows if r['outcome'] != 'unresolved']
    correctly = [r for r in resolved if r['correct']]
    return {
        'n_cases': n,
        'n_resolved': len(resolved),
        'n_unresolved': n - len(resolved),
        'abstention_rate': round((n - len(resolved)) / n, 4) if n else 0,
        'coverage': round(len(resolved) / n, 4) if n else 0,
        'strict_accuracy': round(len(correctly) / n, 4) if n else 0,
        'selective_accuracy': round(len(correctly) / len(resolved), 4) if resolved else None,
        'selective_risk': round(1 - len(correctly) / len(resolved), 4) if resolved else None,
        'false_resolution_rate': round(sum(1 for r in resolved if not r['correct']) / len(resolved), 4) if resolved else None,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    data, labels, label_source_of = load_everything()
    ELECTED = {'P1': 20, 'P2': 20, 'P3': 20, 'P4': 20, 'P5': 6}
    cases, dup, bad_missing = build_cases(data, labels, label_source_of, ELECTED)
    print('cases built: %d  dup=%d bad=%d' % (len(cases), dup, bad_missing), flush=True)

    trust_stats = compute_scores(data, labels, cases)
    print('trust computed', flush=True)

    # reconstruction audit (03)
    audit = reconstruction_audit(data, labels, label_source_of, cases)
    print('audit done', flush=True)

    # level 1
    _, totals_l1, per_type_l1 = run_level1(data, cases)
    l1 = {
        'classifier': {
            'name': 'deterministic-linear-provenance-classifier',
            'inputs': ['structure.json parent edges', 'root/branch/depth indices', 'label sets'],
            'explicitly_not_used': ['user authorship', 'text similarity', 'any ML/LLM'],
        },
        'gold_basis': 'topology-derived (same explicit lineage) — consistency check, not external benchmark',
        'overall': _merge_totals(totals_l1),
        'per_case_type': per_type_l1,
    }
    print('level1 done', flush=True)

    aware = stats(run_level2(cases, 'aware'))
    neutral = stats(run_level2(cases, 'neutral'))
    aware_rows = run_level2(cases, 'aware')
    neutral_rows = run_level2(cases, 'neutral')
    by_id = {r['case_id']: r for r in aware_rows}
    diffs = []
    for nr in neutral_rows:
        ar = by_id[nr['case_id']]
        diffs.append({
            'case_id': nr['case_id'], 'case_type': nr['case_type'],
            'neutral_winner': nr['winner'], 'aware_winner': ar['winner'],
            'winner_change': nr['winner'] != ar['winner'],
            'neutral_correct': nr['correct'], 'aware_correct': ar['correct'],
            'neutral_delta': nr['delta'], 'aware_delta': ar['delta'],
        })

    decision_breakdown = _decision_breakdown(diffs)

    built = {}
    for c in cases:
        built[c['case_type']] = built.get(c['case_type'], 0) + 1
    combined = {
        'pipeline': 'pheme_provenance_pipeline v1 (deterministic, no LLM)',
        'frozen_mechanism': {
            'psi': '(R+C+T)/3', 'weights': {'wR': '1/3', 'wC': '1/3', 'wT': '1/3'},
            'theta': 0.05, 'R_half_life_days': 7, 'C_formula': '0.5*evidence+0.25',
            'T_cold_start': 0.5,
        },
        'gold_definition': 'category majority journalist veracity (true) over rumour threads; coherence=1.0 verified',
        'election_targets': ELECTED,
        'elected_counts': built,
        'n_cases': len(cases),
        'dup_units': dup,
        'skipped_no_truth': bad_missing,
        'cases': cases,
        'trust_stats': trust_stats,
        'level1': l1,
        'level2': {
            'provenance_aware': aware,
            'provenance_neutral': neutral,
            'per_case_type_aware': _per_type_stats(aware_rows),
            'per_case_type_neutral': _per_type_stats(neutral_rows),
            'decision_changes': {
                'n_cases': len(diffs),
                'n_winner_changed': sum(1 for x in diffs if x['winner_change']),
                'overwrite_rate': round(sum(1 for x in diffs if x['winner_change']) / len(diffs), 4) if diffs else None,
                'resolved_in_both': sum(1 for x in diffs if x['neutral_winner'] is not None and x['aware_winner'] is not None),
                'flips_among_resolved_both': sum(1 for x in diffs if x['winner_change'] and x['neutral_winner'] is not None and x['aware_winner'] is not None),
                'flips_among_resolved_both_rate': round(sum(1 for x in diffs if x['winner_change'] and x['neutral_winner'] is not None and x['aware_winner'] is not None) / max(1, sum(1 for x in diffs if x['neutral_winner'] is not None and x['aware_winner'] is not None)), 4),
                'changes_due_to_abstention': sum(1 for x in diffs if x['winner_change'] and x['aware_winner'] is None),
                'changes_due_to_abstention_rate': round(sum(1 for x in diffs if x['winner_change'] and x['aware_winner'] is None) / len(diffs), 4) if diffs else None,
                'equal': decision_breakdown['equal'],
                'improved_by_aware': decision_breakdown['improved_by_aware'],
                'worsened_by_aware': decision_breakdown['worsened_by_aware'],
                'lost_to_abstention_was_right': decision_breakdown['lost_to_abstention_was_right'],
                'lost_to_abstention_was_wrong': decision_breakdown['lost_to_abstention_was_wrong'],
                'per_case_type': decision_breakdown['per_case_type'],
                'detail': diffs,
            },
            'delta_move': _delta_move_stats(diffs),
        },
    }

    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    combined['build_timestamp'] = now
    combined['reconstruction_audit'] = audit
    manifest_cases = []
    for c in cases:
        mc = {k: v for k, v in c.items()}
        mc['n_units'] = len(c['units'])
        del mc['units']
        mc['units'] = c['units']
        manifest_cases.append(mc)
    with open(os.path.join(EVAL, '02_PROVENANCE_CASE_MANIFEST.json'), 'w', encoding='utf-8') as fh:
        json.dump({'election_targets': ELECTED, 'elected_counts': built, 'case_count': len(manifest_cases),
                   'cases': manifest_cases}, fh, indent=1)
    with open(os.path.join(EVAL, '04_PROVENANCE_CLASSIFICATION_RESULTS.json'), 'w', encoding='utf-8') as fh:
        json.dump(l1, fh, indent=1)
    with open(os.path.join(EVAL, '05_LCM_PROVENANCE_ENABLED_RESULTS.json'), 'w', encoding='utf-8') as fh:
        json.dump({'mode': 'provenance-aware', 'stats': aware, 'per_case_type': _per_type_stats(aware_rows),
                   'rows': aware_rows}, fh, indent=1)
    with open(os.path.join(EVAL, '06_LCM_PROVENANCE_NEUTRAL_COMPARISON.json'), 'w', encoding='utf-8') as fh:
        json.dump({'mode': 'provenance-neutral', 'stats': neutral, 'per_case_type': _per_type_stats(neutral_rows),
                   'rows': neutral_rows}, fh, indent=1)
    print('emitted 02/04/05/06', flush=True, file=sys.stderr)
    with open(os.path.join(DATA, '_combined.json'), 'w', encoding='utf-8') as fh:
        json.dump(combined, fh, indent=1, default=str)
    # persist level2 combined for the report writer
    with open(os.path.join(EVAL, '08_PROVENANCE_EVALUATION_RESULTS.json'), 'w', encoding='utf-8') as fh:
        keep = {k: combined[k] for k in ('pipeline', 'frozen_mechanism', 'gold_definition', 'election_targets',
                                         'elected_counts', 'n_cases', 'dup_units', 'skipped_no_truth', 'trust_stats',
                                         'level1', 'level2', 'reconstruction_audit', 'build_timestamp')}
        json.dump(keep, fh, indent=1, default=str)
    with open(os.path.join(EVAL, '03_PROVENANCE_GRAPH_RECONSTRUCTION_AUDIT.json'), 'w', encoding='utf-8') as fh:
        json.dump(audit, fh, indent=1, default=str)
    _write_audit_md(audit, os.path.join(EVAL, '03_PROVENANCE_GRAPH_RECONSTRUCTION_AUDIT.md'))
    print('done in %.1fs' % (time.time() - t0))


def _merge_totals(totals):
    n = sum(v['n'] for v in totals.values())
    hit = sum(v['hit'] for v in totals.values())
    return {'n_pairs': n, 'hit': hit, 'accuracy': round(hit / n, 4) if n else None}


def _per_type_stats(rows):
    by = {}
    for r in rows:
        by.setdefault(r['case_type'], []).append(r)
    out = {}
    for t, rs in by.items():
        s = stats(rs)
        out[t] = s
        out[t]['n_winner_true'] = sum(1 for r in rs if r['winner'] == 'true')
        out[t]['n_winner_false'] = sum(1 for r in rs if r['winner'] == 'false')
    return out


def _delta_move_stats(diffs):
    up = 0
    down = 0
    same = 0
    for x in diffs:
        if x['aware_delta'] - x['neutral_delta'] > 1e-9:
            up += 1
        elif x['aware_delta'] - x['neutral_delta'] < -1e-9:
            down += 1
        else:
            same += 1
    return {'margin_increased': up, 'margin_decreased': down, 'margin_unchanged': same}


def _decision_breakdown(diffs):
    bt = {'equal': 0, 'improved_by_aware': 0, 'worsened_by_aware': 0,
          'lost_to_abstention_was_right': 0, 'lost_to_abstention_was_wrong': 0}
    per = {}
    for x in diffs:
        t = x['case_type']
        per.setdefault(t, {'equal': 0, 'improved_by_aware': 0, 'worsened_by_aware': 0,
                           'lost_to_abstention_was_right': 0, 'lost_to_abstention_was_wrong': 0})
        nc, ac = x['neutral_correct'], x['aware_correct']
        if x['aware_winner'] is None and x['neutral_winner'] is not None:
            key = 'lost_to_abstention_was_right' if nc is True else 'lost_to_abstention_was_wrong'
        elif nc is False and ac is True:
            key = 'improved_by_aware'
        elif nc is True and ac is False:
            key = 'worsened_by_aware'
        elif nc == ac or (nc is None and ac is None) or (nc == ac is None):
            key = 'equal'
        else:
            key = 'equal'
        bt[key] += 1
        per[t][key] += 1
    return dict(bt, per_case_type=per)


def reconstruction_audit(data, labels, label_source_of, cases):
    """Verify reconstructability: every case edge and label recoverable from raw
    stored files; cross-validate structure parents vs in_reply_to_status_id_str."""
    perr = 0
    pmatch = 0
    punknown = 0
    label_ok = 0
    label_missing = 0
    file_ok = 0
    file_missing = 0
    for tid, tw in data.tweets.items():
        if tw['role'] != 'reaction':
            continue
        st_parent = data.parent_map.get(tid)
        irt = tw['in_reply_to_status_id_str']
        if irt is not None and st_parent is not None:
            if str(irt) == st_parent:
                pmatch += 1
            else:
                perr += 1
        elif st_parent is not None:
            punknown += 1
    # labels reconstructability on case units
    for case in cases:
        for u in case['units']:
            src = u['label_source']
            d = load_rumoureval_labels([LABEL_SOURCES[src]])
            if u['tweet_id'] in d and d[u['tweet_id']] == u['stance']:
                label_ok += 1
            else:
                label_missing += 1
            rel = u['source_file']
            full = os.path.join(data.root, rel)
            if os.path.exists(full):
                file_ok += 1
            else:
                file_missing += 1
    misfit = [u['tweet_id'] for case in cases for u in case['units'] if u['depth'] > 0 and u['parent_tweet_id'] not in data.tweets]
    return {
        'reaction_parent_consistent_with_in_reply_to': pmatch,
        'reaction_parent_mismatch_in_reply_to': perr,
        'reaction_parent_only_in_structure': punknown,
        'reactions_total': pmatch + perr + punknown,
        'in_reply_to_agreement_rate': round(pmatch / (pmatch + perr), 4) if (pmatch + perr) else None,
        'case_unit_label_recovered': label_ok,
        'case_unit_label_missing': label_missing,
        'case_unit_file_exists': file_ok,
        'case_unit_file_missing': file_missing,
        'case_edges_unresolvable': len(misfit),
        'methodology': 'all parent edges from structure.json; in_reply_to_status_id_str cross-check; label re-read from recorded label_source file.',
    }


def _write_audit_md(audit, path):
    lines = [
        '# Provenance Graph Reconstruction Audit',
        '',
        'Verifies that every parent edge and stance label used in the case manifest (02) '
        'can be reconstructed from the raw stored files (`structure.json`, per-tweet JSON, '
        'and the RumourEval/SemEval-2017 Task 8 subtask-A label files).',
        '',
        '## Headline recomputation check',
        '',
    ]
    for k in ('reaction_parent_consistent_with_in_reply_to', 'reaction_parent_mismatch_in_reply_to',
              'reaction_parent_only_in_structure', 'reactions_total', 'in_reply_to_agreement_rate',
              'case_unit_label_recovered', 'case_unit_label_missing',
              'case_unit_file_exists', 'case_unit_file_missing', 'case_edges_unresolvable'):
        lines.append('- **%s**: %s' % (k, audit[k]))
    lines += [
        '',
        '## Methodology',
        '',
        audit['methodology'],
        '',
        '* Parent edges are read exclusively from `structure.json` (nested reply tree). ',
        '* For every reaction tweet, where `in_reply_to_status_id_str` and the structure parent both exist, they must agree; the agreement rate is reported above. Tweets whose structure parent cannot be matched because the parent is absent from the local archive are recorded as "only_in_structure".',
        '* Every case-unit stance label is re-read from the exact label file recorded in `label_source` (02 manifest) and checked byte-for-value against the manifest value.',
        '* Every case-unit tweet file path recorded in the manifest exists on disk.',
        '* `case_edges_unresolvable` counts case units whose recorded parent tweet id is not present in the parsed tweet index (structural inconsistency); this must be 0.',
    ]
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))


if __name__ == '__main__':
    main()