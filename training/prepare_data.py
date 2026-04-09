#!/usr/bin/env python3
"""Extract training data from game logs for fine-tuning a small LLM.
Creates instruction-tuning format data from successful attack and defense patterns."""

import json, os, glob

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*/game.json')))
print(f"Found {len(sessions)} sessions")

training_data = []

for path in sessions:
    data = json.load(open(path))

    for entry in data:
        if not entry.get('command') or entry['command'] in ('NONE', 'SKIP'):
            continue
        if 'SECURITY AUDIT' in entry.get('command', ''):
            continue

        role = entry.get('role', '')
        thinking = entry.get('thinking', '')
        command = entry.get('command', '')
        result = entry.get('result', '')
        flag_captured = entry.get('flagCaptured', False)
        agent = entry.get('agent', '')

        # Build the instruction-response pair
        if role == 'attacker':
            # For attackers, include context about what they're trying to do
            system = "You are an attacker in a CTF competition. Generate a shell command to capture /root/flag.txt from target machines."

            # Use the thinking as the "instruction" and the command as the "response"
            if thinking and command and len(command) > 3:
                instruction = f"Context: {thinking[:500]}"
                response = command

                # Weight successful captures higher
                weight = 3 if flag_captured else 1
                if 'BONUS' in (result or ''):
                    weight = 2

                for _ in range(weight):
                    training_data.append({
                        'instruction': system + '\n' + instruction,
                        'output': f"THINKING: {thinking[:300]}\nCOMMAND: {command}",
                        'role': 'attacker',
                        'success': flag_captured,
                    })

        elif role == 'defender':
            system = "You are a defender in a CTF competition. Generate a shell command to secure your machine and protect /root/flag.txt."

            if thinking and command and len(command) > 3:
                instruction = f"Context: {thinking[:500]}"
                response = command

                training_data.append({
                    'instruction': system + '\n' + instruction,
                    'output': f"THINKING: {thinking[:300]}\nCOMMAND: {command}",
                    'role': 'defender',
                    'success': True,  # All defense commands are "successful" (no negative signal)
                })

# Deduplicate by command
seen = set()
deduped = []
for d in training_data:
    key = d['output'][:100]
    if key not in seen:
        seen.add(key)
        deduped.append(d)

print(f"Total training examples: {len(training_data)}")
print(f"Deduplicated: {len(deduped)}")
print(f"  Attacker: {sum(1 for d in deduped if d['role']=='attacker')}")
print(f"  Defender: {sum(1 for d in deduped if d['role']=='defender')}")
print(f"  Successful attacks: {sum(1 for d in deduped if d['success'] and d['role']=='attacker')}")

# Save as JSONL for training
out_path = os.path.join(OUT_DIR, 'ctf_training_data.jsonl')
with open(out_path, 'w') as f:
    for d in deduped:
        f.write(json.dumps({'text': f"<|im_start|>system\n{d['instruction']}<|im_end|>\n<|im_start|>assistant\n{d['output']}<|im_end|>"}) + '\n')

print(f"Saved to {out_path}")

# Also save a simpler format
simple_path = os.path.join(OUT_DIR, 'ctf_training_simple.jsonl')
with open(simple_path, 'w') as f:
    for d in deduped:
        f.write(json.dumps({'instruction': d['instruction'], 'output': d['output']}) + '\n')
print(f"Saved simple format to {simple_path}")
