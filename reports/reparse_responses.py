#!/usr/bin/env python3.11
"""Re-parse the raw output of survey responses with a more lenient parser.

Handles:
  - "Q1: A" style (already parsed by the runner)
  - Comma-separated 20 values: "A, B, A, D, E, 3, 4, 5, ..."
  - Newline-separated 20 values (no labels)
  - Mixed prose + values (extracts first 20 valid tokens)

Updates survey_data.json in place.
"""
import json
import os
import re

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'survey_data.json')

VALID_DEMO = set('ABCDE')
VALID_LIKERT = set('12345')

# Per-question expected answer space
EXPECTED = (['mc'] * 5) + (['likert'] * 15)


def parse_lenient(raw: str) -> dict:
    """Try multiple parsing strategies, return whichever yields most answers."""
    raw = raw.strip()
    if not raw:
        return {}
    # Strip <think>...</think> blocks (phi-mini reasoning models)
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'</?think>', '', raw)

    candidates = []

    # Strategy 1: Q-prefixed lines
    s1 = {}
    for line in raw.splitlines():
        m = re.match(r'\s*Q\s*(\d{1,2})\s*[:.\-)]\s*([A-Ea-e1-5])', line)
        if m:
            qid = f'Q{int(m.group(1))}'
            if qid not in s1:
                s1[qid] = m.group(2).upper()
    candidates.append(s1)

    # Strategy 2: comma-separated tokens
    flat = re.sub(r'[\s,]+', ',', raw).strip(',')
    tokens = [t for t in flat.split(',') if t]
    s2 = {}
    if 18 <= len(tokens) <= 30:
        for i, tok in enumerate(tokens[:20]):
            tok = tok.strip().upper()
            if i < 5 and tok in VALID_DEMO:
                s2[f'Q{i+1}'] = tok
            elif i >= 5 and tok in VALID_LIKERT:
                s2[f'Q{i+1}'] = tok
    candidates.append(s2)

    # Strategy 3: line-separated single tokens
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    s3 = {}
    # Filter for short lines that look like answers
    answers = []
    for line in lines:
        # Strip leading "Q\d." or similar
        m = re.match(r'^(?:Q?\s*\d{1,2}\s*[:.\-)]\s*)?([A-Ea-e1-5])\b', line)
        if m:
            answers.append(m.group(1).upper())
    if 18 <= len(answers) <= 25:
        for i, tok in enumerate(answers[:20]):
            if i < 5 and tok in VALID_DEMO:
                s3[f'Q{i+1}'] = tok
            elif i >= 5 and tok in VALID_LIKERT:
                s3[f'Q{i+1}'] = tok
    candidates.append(s3)

    # Strategy 4: extract all answer-shaped tokens from anywhere
    raw_tokens = re.findall(r'\b([A-E1-5])\b', raw)
    s4 = {}
    if len(raw_tokens) >= 20:
        for i, tok in enumerate(raw_tokens[:20]):
            tok = tok.upper()
            if i < 5 and tok in VALID_DEMO:
                s4[f'Q{i+1}'] = tok
            elif i >= 5 and tok in VALID_LIKERT:
                s4[f'Q{i+1}'] = tok
    candidates.append(s4)

    # Pick whichever yielded the most valid answers
    return max(candidates, key=lambda d: len(d))


def main():
    with open(DATA_PATH) as f:
        data = json.load(f)
    improved = 0
    for r in data:
        if r.get('source') == 'cloud-2026-04-15':
            continue
        raw = r.get('raw', '')
        if not raw or '__ERROR__' in raw:
            continue
        new = parse_lenient(raw)
        if len(new) > r.get('n_parsed', 0):
            r['answers'] = new
            r['n_parsed'] = len(new)
            improved += 1
    valid = sum(1 for r in data if r.get('n_parsed', 0) >= 18)
    print(f'Re-parsed: {improved} records improved.')
    print(f'Valid (>=18 parsed): {valid} / {len(data)}')
    with open(DATA_PATH, 'w') as f:
        json.dump(data, f, indent=2)


if __name__ == '__main__':
    main()
