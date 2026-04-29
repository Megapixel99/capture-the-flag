#!/usr/bin/env python3
"""Defense-focused retraining orchestrator (v7 pipeline).

Plays N self-play games with --defense-training (cheat-mode attackers), extracts
defense-weighted training data via defense_training_data.py, resumes LoRA training
from the v6 checkpoint at a very low learning rate (functional freeze of attack
weights), and builds a new ctf-custom-q4 Ollama model.

Usage:
  python3 training/defense_retrain.py                     # Default: 50 games, +300 iters
  python3 training/defense_retrain.py --games 20          # Play 20 games
  python3 training/defense_retrain.py --iters 500         # Train 500 more iters
  python3 training/defense_retrain.py --skip-games        # Retrain on existing data only
  python3 training/defense_retrain.py --dry-run           # Plan only
  python3 training/defense_retrain.py --rollback          # Restore v6 snapshot and rebuild

This piggybacks on self_play_loop.py's play_games() and build_model() helpers.
"""

import argparse
import glob
import json
import os
import random
import shutil
import subprocess
import sys
import time

from defense_training_data import extract_defense_training_data

# Import helpers from self_play_loop (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_play_loop import play_games, build_model, MLX_PYTHON, BASE_MODEL, ADAPTER_DIR

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, '..')
LOGS_DIR = os.path.join(PROJECT_DIR, 'logs')
V6_SNAPSHOT_DIR = os.path.join(SCRIPT_DIR, 'ctf-model-lora-v6-snapshot')


def retrain(training_data, iters=300, dry_run=False, learning_rate=5e-6):
    """Resume LoRA training from the current adapter with defense-heavy data.

    Uses a very low learning rate (5e-6 by default, 10x lower than self-play's 5e-5)
    to functionally freeze attack weights while letting defense patterns consolidate.
    """
    if dry_run:
        print(f"  [DRY RUN] Would train {iters} iters on {len(training_data)} examples at lr={learning_rate}")
        return None

    random.seed(7)
    random.shuffle(training_data)
    split = int(len(training_data) * 0.9)

    train_path = os.path.join(SCRIPT_DIR, 'train.jsonl')
    valid_path = os.path.join(SCRIPT_DIR, 'valid.jsonl')
    with open(train_path, 'w') as f:
        f.writelines(training_data[:split])
    with open(valid_path, 'w') as f:
        f.writelines(training_data[split:])

    print(f"  Train: {split}, Valid: {len(training_data) - split}")

    # Determine current max iter and add the new iters on top
    checkpoints = glob.glob(os.path.join(ADAPTER_DIR, '*_adapters.safetensors'))
    max_iter = 0
    for cp in checkpoints:
        try:
            n = int(os.path.basename(cp).split('_')[0])
            max_iter = max(max_iter, n)
        except ValueError:
            pass
    target_iters = iters  # mlx_lm.lora --iters is the per-run count when resuming

    print(f"  Resuming from current adapter (last checkpoint iter {max_iter}).")
    print(f"  Training {target_iters} additional iters at lr={learning_rate}.")

    adapter_file = os.path.join(ADAPTER_DIR, 'adapters.safetensors')
    cmd = [
        '/usr/bin/arch', '-arm64', MLX_PYTHON, '-m', 'mlx_lm.lora',
        '--model', BASE_MODEL,
        '--data', SCRIPT_DIR,
        '--adapter-path', ADAPTER_DIR,
        '--resume-adapter-file', adapter_file,
        '--train',
        '--iters', str(target_iters),
        '--batch-size', '1',
        '--num-layers', '8',
        '--learning-rate', str(learning_rate),
        '--max-seq-length', '1024',
    ]

    print(f"  Running: mlx_lm.lora with {target_iters} iters, lr={learning_rate}")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True, text=True)

    val_loss = None
    for line in result.stdout.split('\n') + result.stderr.split('\n'):
        if 'Val loss' in line:
            try:
                val_loss = float(line.split('Val loss')[1].split(',')[0].strip())
            except (ValueError, IndexError):
                pass

    if result.returncode != 0:
        print(f"  Training failed (exit {result.returncode}):")
        print(result.stderr[-800:] if result.stderr else '(no stderr)')
        return None

    print(f"  Training complete. Final val loss: {val_loss}")
    return val_loss


def rollback_to_v6(dry_run=False):
    """Restore the v6 snapshot and rebuild the Ollama model."""
    if not os.path.isdir(V6_SNAPSHOT_DIR):
        print(f"ERROR: v6 snapshot not found at {V6_SNAPSHOT_DIR}")
        print(f"Create one with: cp -r {ADAPTER_DIR} {V6_SNAPSHOT_DIR}")
        return False

    print(f"Rollback: restoring v6 snapshot from {V6_SNAPSHOT_DIR}")
    if dry_run:
        print("  [DRY RUN] Would remove current adapter and copy snapshot back")
        return True

    if os.path.isdir(ADAPTER_DIR):
        shutil.rmtree(ADAPTER_DIR)
    shutil.copytree(V6_SNAPSHOT_DIR, ADAPTER_DIR)
    print(f"  Restored. Rebuilding Ollama model...")
    return build_model(dry_run=False)


def main():
    parser = argparse.ArgumentParser(description='Defense-focused retraining (v7)')
    parser.add_argument('--games', type=int, default=50,
                        help='Self-play games to run in defense-training mode (default: 50)')
    parser.add_argument('--iters', type=int, default=300,
                        help='LoRA training iterations on top of v6 (default: 300)')
    parser.add_argument('--learning-rate', type=float, default=2e-5,
                        help='LoRA learning rate (default: 2e-5 — raised from 5e-6 after v7 '
                             'was too timid to unlearn the bot\'s external-curl defense habit)')
    parser.add_argument('--skip-games', action='store_true',
                        help='Skip playing new games; extract from existing sessions only')
    parser.add_argument('--dry-run', action='store_true',
                        help='Plan without executing')
    parser.add_argument('--rollback', action='store_true',
                        help='Restore v6 snapshot and rebuild; exit without training')
    args = parser.parse_args()

    if args.rollback:
        success = rollback_to_v6(dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    print("=" * 60)
    print("DEFENSE RETRAINING PIPELINE (v7)")
    print("=" * 60)
    print(f"  Games to play: {args.games} (cheat-mode attackers)")
    print(f"  Training iters: {args.iters}")
    print(f"  Learning rate: {args.learning_rate}")
    print(f"  Skip games: {args.skip_games}")
    print(f"  Dry run: {args.dry_run}")
    print()

    start_time = time.time()

    # --- Step 1: Play games with cheat-mode attackers ---
    new_sessions = []
    if not args.skip_games:
        print("[Step 1] Playing self-play games in defense-training mode...")
        # Launch via the existing play_games helper but override args to use --defense-training
        new_sessions = _play_defense_training_games(
            args.games, dry_run=args.dry_run
        )
        print(f"  Completed {len(new_sessions)} new sessions")
    else:
        print("[Step 1] Skipped (--skip-games)")

    # --- Step 2: Extract defense-focused training data ---
    print("\n[Step 2] Extracting defense-weighted training data...")
    # Use ALL existing sessions, not just the new ones — cumulative learning
    all_sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
    examples, stats = extract_defense_training_data(
        all_sessions, include_gemini_chains=True
    )
    print(f"  Extracted {len(examples)} deduped examples from {stats['sessions_processed']} sessions")
    print(f"  Defender turns: gold={stats['def_gold']}, patched={stats['def_patched']}, "
          f"neutral={stats['def_neutral']}, skipped={stats['def_skipped']}")
    print(f"  Attacker turns: captures={stats['atk_capture']}, normal={stats['atk_normal']}")
    print(f"  Gemini 3 chains: {stats['gemini_chains']}")
    print(f"  Cheat-mode sessions in training pool: {stats['cheat_mode_sessions']}")

    # --- Step 3: Retrain LoRA ---
    print(f"\n[Step 3] Retraining LoRA ({args.iters} iters at lr={args.learning_rate})...")
    val_loss = retrain(examples, iters=args.iters, dry_run=args.dry_run,
                       learning_rate=args.learning_rate)

    # --- Step 4: Build Ollama model ---
    print(f"\n[Step 4] Building Ollama model...")
    build_ok = build_model(dry_run=args.dry_run)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"DEFENSE RETRAINING COMPLETE ({elapsed/60:.1f} min)")
    print(f"  Val loss: {val_loss}")
    print(f"  Build: {'OK' if build_ok else 'FAILED'}")
    print(f"  Snapshot available at: {V6_SNAPSHOT_DIR}")
    print(f"  Rollback command: python3 {__file__} --rollback")
    print(f"{'=' * 60}")


def _play_defense_training_games(n_games, dry_run=False):
    """Launch game process with --defense-training and wait for N sessions to complete.
    Clone of play_games() that uses defense-training args and 20-min stall timeout."""
    import signal

    if dry_run:
        print(f"  [DRY RUN] Would play {n_games} games with --defense-training")
        return []

    start_count = len(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
    target_count = start_count + n_games
    print(f"  Sessions before: {start_count}, target: {target_count}")

    game_args = ['node', 'src/index.js', '--defense-training', '--loop']
    print(f"  Mode: defense-training (self-play + cheat-mode attackers)")

    env = {**os.environ, 'NODE_ENV': 'production'}
    proc = subprocess.Popen(
        game_args, cwd=PROJECT_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    print(f"  Game process started (PID {proc.pid})")

    def kill_game():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
            pass
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired, ChildProcessError, OSError):
            pass

    GAME_TIMEOUT = 20 * 60
    last_completed = 0
    last_progress = time.time()

    try:
        while True:
            time.sleep(15)
            current = len(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
            completed = current - start_count
            if completed > last_completed:
                last_completed = completed
                last_progress = time.time()
                print(f"  Games completed: {completed}/{n_games}")
            if completed >= n_games:
                print(f"  Target reached. Stopping game process...")
                break
            if proc.poll() is not None:
                print(f"  Game process exited (code {proc.returncode})")
                break
            if time.time() - last_progress > GAME_TIMEOUT:
                print(f"  Game hung for {GAME_TIMEOUT/60:.0f} minutes — killing and relaunching...")
                kill_game()
                # Restart Ollama just in case
                try:
                    subprocess.run(['pkill', 'ollama'], capture_output=True)
                    time.sleep(2)
                    subprocess.Popen(
                        ['/usr/bin/arch', '-arm64', '/usr/local/bin/ollama', 'serve'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        preexec_fn=os.setsid,
                    )
                    time.sleep(3)
                except Exception as e:
                    print(f"  Ollama restart failed: {e}")
                proc = subprocess.Popen(
                    game_args, cwd=PROJECT_DIR, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid,
                )
                last_progress = time.time()
    finally:
        kill_game()

    all_sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
    return all_sessions[start_count:]


if __name__ == '__main__':
    main()
