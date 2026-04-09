#!/usr/bin/env python3
"""Fine-tune Qwen2.5-0.5B-Instruct on CTF game log data using MLX LoRA.
Produces a custom cybersecurity CTF model that can compete against cloud LLMs."""

import os
import json
import subprocess
import sys

TRAINING_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(TRAINING_DIR, 'ctf_training_data.jsonl')
MODEL_NAME = "Qwen/Qwen2.5-1.5B-Instruct"
OUTPUT_DIR = os.path.join(TRAINING_DIR, 'ctf-model-lora')
FUSED_DIR = os.path.join(TRAINING_DIR, 'ctf-model-fused')

# Split data into train/valid
print("Preparing train/valid split...")
with open(DATA_FILE) as f:
    lines = f.readlines()

# Shuffle deterministically
import random
random.seed(42)
random.shuffle(lines)

split = int(len(lines) * 0.9)
train_lines = lines[:split]
valid_lines = lines[split:]

train_path = os.path.join(TRAINING_DIR, 'train.jsonl')
valid_path = os.path.join(TRAINING_DIR, 'valid.jsonl')

with open(train_path, 'w') as f:
    f.writelines(train_lines)
with open(valid_path, 'w') as f:
    f.writelines(valid_lines)

print(f"Train: {len(train_lines)} examples")
print(f"Valid: {len(valid_lines)} examples")

# Run MLX LoRA fine-tuning
print(f"\nFine-tuning {MODEL_NAME} with LoRA...")
print("This will take ~10-30 minutes on Apple Silicon.\n")

cmd = [
    sys.executable, '-m', 'mlx_lm.lora',
    '--model', MODEL_NAME,
    '--data', TRAINING_DIR,
    '--adapter-path', OUTPUT_DIR,
    '--train',
    '--iters', '2000',
    '--batch-size', '4',
    '--num-layers', '8',
    '--learning-rate', '1e-4',
]

print(f"Running: {' '.join(cmd)}\n")
result = subprocess.run(cmd, cwd=TRAINING_DIR)

if result.returncode != 0:
    print(f"Training failed with exit code {result.returncode}")
    sys.exit(1)

print(f"\nLoRA adapters saved to: {OUTPUT_DIR}")

# Fuse the adapters into the base model
print(f"\nFusing adapters into base model...")
fuse_cmd = [
    sys.executable, '-m', 'mlx_lm.fuse',
    '--model', MODEL_NAME,
    '--adapter-path', OUTPUT_DIR,
    '--save-path', FUSED_DIR,
]
result = subprocess.run(fuse_cmd)

if result.returncode == 0:
    print(f"Fused model saved to: {FUSED_DIR}")
    print(f"\nTo create an Ollama model from this, run:")
    print(f"  ollama create ctf-custom -f {FUSED_DIR}/Modelfile")
else:
    print("Fusing failed — you can still use the LoRA adapter with mlx_lm.generate")

print("\nDone!")
