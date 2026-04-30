#!/usr/bin/env python3.11
"""Keep only ONE response per model — the run with the most parsed answers,
breaking ties by earliest run number. Writes survey_data.json in place."""
import json
import os
from collections import defaultdict

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'survey_data.json')

with open(DATA_PATH) as f:
    data = json.load(f)

# Group by model, pick best
by_model = defaultdict(list)
for r in data:
    by_model[r['model']].append(r)

kept = []
for model, recs in by_model.items():
    recs.sort(key=lambda r: (-r.get('n_parsed', 0), r.get('run', 999)))
    kept.append(recs[0])

# Sort: cloud first, then alphabetical
CLOUD = {'gpt-oss:120b', 'gemini-3-flash', 'nemotron-3-super',
         'glm-5.1', 'rnj-1:8b'}
kept.sort(key=lambda r: (0 if r['model'] in CLOUD else 1, r['model']))

valid = [r for r in kept if r.get('n_parsed', 0) >= 18]
print(f'Distinct models: {len(kept)}')
print(f'Valid (>=18 parsed): {len(valid)}')
print(f'Models kept:')
for r in kept:
    flag = 'OK ' if r.get('n_parsed', 0) >= 18 else 'low'
    print(f'  [{flag}] {r["model"]:30s}  n_parsed={r["n_parsed"]}')

with open(DATA_PATH, 'w') as f:
    json.dump(kept, f, indent=2)
print(f'\nWrote {DATA_PATH}')
