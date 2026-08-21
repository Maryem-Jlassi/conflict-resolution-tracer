import os, sys, json, pickle, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pheme_provenance_common import PHEMEData

EVAL = r'results/empirical_evaluation/provenance/evaluation'
DATA = os.path.join(EVAL, '_data')
PHEME_DIR = os.path.join(DATA, 'pheme_extracted')
RE_DIR = os.path.join(DATA, 'rumoureval', 'semeval2017-task8-dataset')
TEST_LABELS = os.path.join(DATA, 'rumoureval-test', 'semeval2017-task8-test-data')

def main():
    index_path = os.path.join(DATA, 'index.pkl')
    if os.path.exists(index_path):
        with open(index_path, 'rb') as fh:
            data = pickle.load(fh)
        print('index loaded from cache', flush=True)
    else:
        data = PHEMEData(PHEME_DIR)
        print('parsed pheme in %.1fs; threads=%d tweets=%d' % (data.t_load, len(data.threads), len(data.tweets)), flush=True)
        with open(index_path, 'wb') as fh:
            pickle.dump(data, fh, protocol=4)
        print('index cached', flush=True)

    # RumourEval labels
    label_files = [
        os.path.join(RE_DIR, 'traindev', 'rumoureval-subtaskA-train.json'),
        os.path.join(RE_DIR, 'traindev', 'rumoureval-subtaskA-dev.json'),
        os.path.join(EVAL, '_data', 'external', 'subtaska.json'),
    ]
    from pheme_provenance_common import load_rumoureval_labels
    labels = load_rumoureval_labels(label_files)
    print('labels loaded: %d' % len(labels), flush=True)

    # Join labelled -> pheme
    lab_in = {tid: st for tid, st in labels.items() if tid in data.tweets}
    print('labelled tweets in pheme: %d' % len(lab_in), flush=True)

    stance_dist = {}
    thr_set = set()
    for tid, st in lab_in.items():
        stance_dist[st] = stance_dist.get(st, 0) + 1
        thr_set.add(data.tweets[tid]['thread_id'])
    print('stance_dist:', stance_dist, flush=True)
    print('threads with labelled tweets: %d' % len(thr_set), flush=True)

    summary = {
        'labels_loaded': len(labels),
        'labelled_in_pheme': len(lab_in),
        'stance_dist': stance_dist,
        'labelled_threads': len(thr_set),
    }
    with open(os.path.join(EVAL, 'evaluation', '_survey_index.json') if False else os.path.join(DATA, '_survey_index.json'), 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, indent=1)
    print('written', flush=True)

if __name__ == '__main__':
    main()