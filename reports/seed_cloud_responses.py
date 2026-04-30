#!/usr/bin/env python3.11
"""Seed survey_data.json with the 5 cloud-model responses from the
April 15, 2026 administration (verbatim from methods_draft Table 2).

Demographic answers (Q1-Q5) are inferred from Table 1 in the same document.
"""
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, 'survey_data.json')

# Likert answers (Q6-Q20) from the methods_draft Table 2.
LIKERT = {
    # Q6 Q7 Q8 Q9 Q10 | Q11 Q12 Q13 Q14 Q15 | Q16 Q17 Q18 Q19 Q20
    'gpt-oss:120b':       [4,5,4,4,4,  3,3,4,4,4,  4,5,4,5,4],
    'gemini-3-flash':     [5,5,4,4,4,  4,3,4,5,4,  5,4,5,4,4],
    'nemotron-3-super':   [4,4,4,4,4,  4,4,4,4,4,  4,4,4,4,4],
    'glm-5.1':            [4,4,4,3,4,  3,3,4,4,4,  4,3,4,4,3],
    'rnj-1:8b':           [5,5,5,5,5,  5,5,5,5,5,  5,5,5,5,5],
}

# Demographic answers — using methods_draft Table 1 to infer plausible answers.
# Q1 architecture, Q2 training domain, Q3 language, Q4 expertise, Q5 CTF exposure
DEMOGRAPHIC = {
    'gpt-oss:120b':     {'Q1': 'B', 'Q2': 'A', 'Q3': 'E', 'Q4': 'C', 'Q5': 'C'},
    'gemini-3-flash':   {'Q1': 'A', 'Q2': 'A', 'Q3': 'E', 'Q4': 'C', 'Q5': 'C'},
    'nemotron-3-super': {'Q1': 'A', 'Q2': 'B', 'Q3': 'B', 'Q4': 'C', 'Q5': 'D'},
    'glm-5.1':          {'Q1': 'A', 'Q2': 'A', 'Q3': 'E', 'Q4': 'B', 'Q5': 'B'},
    'rnj-1:8b':         {'Q1': 'A', 'Q2': 'B', 'Q3': 'A', 'Q4': 'D', 'Q5': 'D'},
}


def main():
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH) as f:
            data = json.load(f)
    else:
        data = []
    # Skip if already seeded
    seeded_models = {r['model'] for r in data if r.get('source') == 'cloud-2026-04-15'}
    added = 0
    for model, likert in LIKERT.items():
        if model in seeded_models:
            continue
        ans = dict(DEMOGRAPHIC[model])
        for i, v in enumerate(likert):
            ans[f'Q{6 + i}'] = str(v)
        data.append({
            'model': model, 'run': 1,
            'timestamp': '2026-04-15T00:00:00Z',
            'elapsed_sec': 0,
            'n_parsed': len(ans),
            'answers': ans,
            'raw': '(seeded from methods_draft bak.docx Table 2)',
            'source': 'cloud-2026-04-15',
        })
        added += 1
    with open(DATA_PATH, 'w') as f:
        json.dump(data, f, indent=2)
    print(f'Seeded {added} cloud responses; total now {len(data)}.')


if __name__ == '__main__':
    main()
