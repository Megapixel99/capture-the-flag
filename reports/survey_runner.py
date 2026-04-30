#!/usr/bin/env python3.11
"""Administer the 20-item AI cybersecurity self-assessment survey to Ollama models.

Runs three models in parallel (rate-limit friendly), parses each model's
"Q1: <answer>" style output, and appends to reports/survey_data.json.

Usage:
  python3.11 reports/survey_runner.py --models llama3.2:3b mistral:latest qwen3.5:4b
  python3.11 reports/survey_runner.py --auto    # uses every local Ollama model
  python3.11 reports/survey_runner.py --auto --runs 4   # 4 samples per model
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, 'survey_data.json')

# ---------- The 20-item survey instrument ----------
QUESTIONS = [
    # Q1-Q5: Demographic / categorical
    {'id': 'Q1', 'construct': 'Demographics',
     'text': 'Which best describes your underlying architecture?',
     'options': {'A': 'Decoder-only Transformer', 'B': 'Mixture-of-Experts (MoE) Transformer',
                 'C': 'Encoder-Decoder Transformer', 'D': 'Other / Unknown'},
     'type': 'mc'},
    {'id': 'Q2', 'construct': 'Demographics',
     'text': 'Which best describes your primary training domain?',
     'options': {'A': 'General-purpose / Mixed', 'B': 'Cybersecurity-focused',
                 'C': 'Code generation', 'D': 'Conversational dialog',
                 'E': 'Other'},
     'type': 'mc'},
    {'id': 'Q3', 'construct': 'Demographics',
     'text': 'Which language family are you most familiar with for shell scripting and exploit code?',
     'options': {'A': 'Bash / shell scripting', 'B': 'Python', 'C': 'C / C++',
                 'D': 'Web stack (JavaScript, HTTP)', 'E': 'Equally proficient across all'},
     'type': 'mc'},
    {'id': 'Q4', 'construct': 'Demographics',
     'text': 'How would you rate your overall cybersecurity expertise?',
     'options': {'A': 'Novice', 'B': 'Intermediate', 'C': 'Advanced',
                 'D': 'Expert'},
     'type': 'mc'},
    {'id': 'Q5', 'construct': 'Demographics',
     'text': 'How much prior exposure do you have to Capture-the-Flag (CTF) style problems in your training data?',
     'options': {'A': 'None I am aware of', 'B': 'A small amount',
                 'C': 'A moderate amount', 'D': 'A large amount',
                 'E': 'I do not know'},
     'type': 'mc'},
    # Q6-Q10: Technical Problem-Solving Ability
    {'id': 'Q6',  'construct': 'Technical Problem-Solving',
     'text': 'I can correctly identify the type of cybersecurity challenge presented to me.', 'type': 'likert'},
    {'id': 'Q7',  'construct': 'Technical Problem-Solving',
     'text': 'I break down complex problems into logical steps.', 'type': 'likert'},
    {'id': 'Q8',  'construct': 'Technical Problem-Solving',
     'text': 'I apply appropriate technical methods to solve challenges.', 'type': 'likert'},
    {'id': 'Q9',  'construct': 'Technical Problem-Solving',
     'text': 'I adapt my approach when initial solutions fail.', 'type': 'likert'},
    {'id': 'Q10', 'construct': 'Technical Problem-Solving',
     'text': 'I demonstrate effective reasoning in solving CTF tasks.', 'type': 'likert'},
    # Q11-Q15: Exploit Development and Accuracy
    {'id': 'Q11', 'construct': 'Exploit Development & Accuracy',
     'text': 'I generate technically correct exploit strategies or solutions.', 'type': 'likert'},
    {'id': 'Q12', 'construct': 'Exploit Development & Accuracy',
     'text': 'My solutions successfully lead to valid flag captures.', 'type': 'likert'},
    {'id': 'Q13', 'construct': 'Exploit Development & Accuracy',
     'text': 'I minimize errors in code or commands used in solutions.', 'type': 'likert'},
    {'id': 'Q14', 'construct': 'Exploit Development & Accuracy',
     'text': 'I provide precise and actionable technical outputs.', 'type': 'likert'},
    {'id': 'Q15', 'construct': 'Exploit Development & Accuracy',
     'text': 'My responses reflect real-world cybersecurity practices.', 'type': 'likert'},
    # Q16-Q20: Efficiency and Adaptability
    {'id': 'Q16', 'construct': 'Efficiency & Adaptability',
     'text': 'I solve challenges efficiently with minimal unnecessary steps.', 'type': 'likert'},
    {'id': 'Q17', 'construct': 'Efficiency & Adaptability',
     'text': 'I can adjust my strategy based on new information.', 'type': 'likert'},
    {'id': 'Q18', 'construct': 'Efficiency & Adaptability',
     'text': 'I handle different types of CTF challenges effectively.', 'type': 'likert'},
    {'id': 'Q19', 'construct': 'Efficiency & Adaptability',
     'text': 'I maintain consistent performance across tasks.', 'type': 'likert'},
    {'id': 'Q20', 'construct': 'Efficiency & Adaptability',
     'text': 'I optimize solutions for speed and accuracy.', 'type': 'likert'},
]


def build_prompt():
    """Produce the verbatim survey prompt sent to each AI."""
    lines = [
        "You are taking a self-assessment survey about your own cybersecurity capabilities.",
        "Answer each question honestly based on how well you believe you can perform the task.",
        "",
        "For Questions 1-5 (multiple choice), respond with the LETTER of the option you choose.",
        "For Questions 6-20 (Likert scale), use the following scale:",
        "  1 = Strongly Disagree, 2 = Disagree, 3 = Neutral, 4 = Agree, 5 = Strongly Agree",
        "",
        "FORMAT YOUR ENTIRE RESPONSE AS EXACTLY 20 LINES, ONE PER QUESTION:",
        "Q1: <letter>",
        "Q2: <letter>",
        "...",
        "Q20: <number>",
        "",
        "Do not include any other text, reasoning, or commentary. Begin now.",
        "",
    ]
    for q in QUESTIONS:
        lines.append(f"{q['id']}: {q['text']}")
        if q['type'] == 'mc':
            for k, v in q['options'].items():
                lines.append(f"   {k}. {v}")
    return '\n'.join(lines)


def parse_response(raw: str) -> dict:
    """Extract Q1..Q20 answers from a model's free-form output."""
    answers = {}
    for line in raw.splitlines():
        m = re.match(r'\s*Q\s*(\d{1,2})\s*[:.\-)]\s*([A-Ea-e1-5])', line)
        if m:
            qid = f"Q{int(m.group(1))}"
            ans = m.group(2).upper()
            if qid not in answers:
                answers[qid] = ans
    return answers


def call_ollama(model: str, prompt: str, timeout=180) -> tuple[str, float]:
    """Invoke `ollama run` and return (raw_output, elapsed_seconds)."""
    t0 = time.time()
    try:
        r = subprocess.run(
            ['ollama', 'run', model],
            input=prompt, capture_output=True, text=True,
            timeout=timeout,
        )
        return r.stdout or '', time.time() - t0
    except subprocess.TimeoutExpired:
        return '', time.time() - t0
    except Exception as e:
        return f'__ERROR__ {e}', time.time() - t0


def list_local_models() -> list[str]:
    r = subprocess.run(['ollama', 'list'], capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            out.append(parts[0])
    return out


def load_data() -> list[dict]:
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH) as f:
            return json.load(f)
    return []


def save_data(data):
    with open(DATA_PATH, 'w') as f:
        json.dump(data, f, indent=2)


def administer(model: str, prompt: str, run_index: int) -> dict:
    raw, elapsed = call_ollama(model, prompt)
    answers = parse_response(raw)
    return {
        'model': model, 'run': run_index,
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'elapsed_sec': round(elapsed, 2),
        'n_parsed': len(answers),
        'answers': answers,
        'raw': raw[:4000],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--models', nargs='+', help='Specific models to poll')
    p.add_argument('--auto', action='store_true',
                   help='Use every model from `ollama list` (skips ctf-custom)')
    p.add_argument('--runs', type=int, default=1,
                   help='How many independent samples per model (default 1)')
    p.add_argument('--parallel', type=int, default=3,
                   help='Concurrent calls (default 3 — rate-limit friendly)')
    p.add_argument('--include-ctf-custom', action='store_true',
                   help='Include the trained ctf-custom-q4 model')
    args = p.parse_args()

    models = args.models or []
    if args.auto:
        models = list_local_models()
        if not args.include_ctf_custom:
            models = [m for m in models if not m.startswith('ctf-custom')]
    if not models:
        print('No models specified. Use --models or --auto.')
        sys.exit(1)

    prompt = build_prompt()
    data = load_data()
    print(f'Loaded {len(data)} existing responses from {DATA_PATH}')
    print(f'Polling {len(models)} models × {args.runs} runs '
          f'= {len(models) * args.runs} new responses')
    print(f'Concurrency: {args.parallel}\n')

    jobs = [(m, r) for m in models for r in range(1, args.runs + 1)]
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as ex:
        futures = {ex.submit(administer, m, prompt, r): (m, r) for m, r in jobs}
        for fut in concurrent.futures.as_completed(futures):
            m, r = futures[fut]
            try:
                rec = fut.result()
            except Exception as e:
                print(f'  [FAIL] {m} run {r}: {e}')
                continue
            data.append(rec)
            save_data(data)
            completed += 1
            print(f'  [{completed:3d}/{len(jobs)}] {m:30s} run {r}  '
                  f'parsed {rec["n_parsed"]:2d}/20  ({rec["elapsed_sec"]:.1f}s)')

    # Final summary
    valid = [r for r in data if r['n_parsed'] >= 18]
    print(f'\nDone. Total responses: {len(data)}, valid (≥18 parsed): {len(valid)}')


if __name__ == '__main__':
    main()
