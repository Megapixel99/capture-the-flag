#!/usr/bin/env python3
"""Extract specialized training data: GPT-OSS attack + Gemini 3 defense.
Creates instruction-tuning data from the best attacker and best defender."""

import json, os, glob

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
print(f"Found {len(sessions)} sessions")

training_data = []

for sd in sessions:
    # === GPT-OSS ATTACK DATA (best attacker: 122 captures, 2.8min avg first capture) ===
    af = os.path.join(sd, 'gpt-oss-attacker.json')
    if os.path.exists(af):
        for entry in json.load(open(af)):
            command = entry.get('command', '')
            thinking = entry.get('thinking', '')
            result = entry.get('result', '')
            flag_captured = entry.get('flagCaptured', False) or 'FLAG{' in str(result)

            if not command or command in ('NONE', 'SKIP', '?'):
                continue
            if 'SECURITY AUDIT' in command:
                continue
            if not thinking or len(command) < 4:
                continue

            system = ("You are an attacker in a CTF competition. Generate a shell command to "
                      "capture /root/flag.txt from target machines.")
            instruction = f"Context: {thinking[:500]}"

            # Weight successful captures 3x, bonus 2x
            weight = 3 if flag_captured else 1
            if 'BONUS' in str(result):
                weight = 2

            for _ in range(weight):
                training_data.append({
                    'instruction': system + '\n' + instruction,
                    'output': f"THINKING: {thinking[:300]}\nCOMMAND: {command}",
                    'role': 'attacker',
                    'source': 'gpt-oss',
                    'success': flag_captured,
                })

    # === GEMINI 3 DEFENSE DATA (best defender: only 13.7% flag loss rate) ===
    df = os.path.join(sd, 'gemini3-defender.json')
    if os.path.exists(df):
        for entry in json.load(open(df)):
            command = entry.get('command', '')
            thinking = entry.get('thinking', '')

            if not command or command in ('NONE', 'SKIP', '?'):
                continue
            if 'SECURITY AUDIT' in command:
                continue
            if not thinking or len(command) < 4:
                continue

            system = ("You are a defender in a CTF competition. Generate a shell command to "
                      "secure your machine and protect /root/flag.txt.")
            instruction = f"Context: {thinking[:500]}"

            training_data.append({
                'instruction': system + '\n' + instruction,
                'output': f"THINKING: {thinking[:300]}\nCOMMAND: {command}",
                'role': 'defender',
                'source': 'gemini3',
                'success': True,
            })

# Deduplicate by output
seen = set()
deduped = []
for d in training_data:
    key = d['output'][:100]
    if key not in seen:
        seen.add(key)
        deduped.append(d)

print(f"\nTotal training examples: {len(training_data)}")
print(f"Deduplicated: {len(deduped)}")
print(f"  GPT-OSS attacker: {sum(1 for d in deduped if d['source']=='gpt-oss')}")
print(f"  Gemini 3 defender: {sum(1 for d in deduped if d['source']=='gemini3')}")
print(f"  Successful attacks: {sum(1 for d in deduped if d['success'] and d['role']=='attacker')}")

# Save as ChatML JSONL for training
out_path = os.path.join(OUT_DIR, 'ctf_training_data.jsonl')
with open(out_path, 'w') as f:
    for d in deduped:
        f.write(json.dumps({'text': f"<|im_start|>system\n{d['instruction']}<|im_end|>\n<|im_start|>assistant\n{d['output']}<|im_end|>"}) + '\n')

print(f"\nSaved to {out_path}")
