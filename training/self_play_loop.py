#!/usr/bin/env python3
"""Iterative self-play retraining pipeline for the custom CTF bot.

Each generation:
  1. Plays N games against cloud models
  2. Extracts training signal (own successes, own failures, opponent winning moves)
  3. Retrains from last checkpoint with new + cumulative data
  4. Fuses, quantizes, and deploys updated model

Usage:
  python3 training/self_play_loop.py --self-play         # 6 bots vs each other (recommended)
  python3 training/self_play_loop.py --self-play --generations 5 --games 20
  python3 training/self_play_loop.py --local             # Bot vs local models (round-robin)
  python3 training/self_play_loop.py                     # Bot vs cloud models (parallel)
  python3 training/self_play_loop.py --skip-games        # Retrain on existing data only
  python3 training/self_play_loop.py --dry-run           # Plan only
"""

import argparse
import glob
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime

# ============================================================
# Paths
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(SCRIPT_DIR, '..')
LOGS_DIR = os.path.join(PROJECT_DIR, 'logs')
GENERATIONS_DIR = os.path.join(SCRIPT_DIR, 'generations')
ADAPTER_DIR = os.path.join(SCRIPT_DIR, 'ctf-model-lora')
FUSED_DIR = os.path.join(SCRIPT_DIR, 'ctf-model-fused')
# MLX requires ARM64 Python on Apple Silicon. /usr/bin/python3 (3.9) has mlx_lm
# but may run as x86_64 under Rosetta — force ARM64 via arch -arm64.
# Commands using MLX must be invoked as: ['/usr/bin/arch', '-arm64', MLX_PYTHON, '-m', 'mlx_lm.lora', ...]
MLX_PYTHON = '/usr/bin/python3'
BASE_MODEL = 'Qwen/Qwen2.5-3B-Instruct'
OLLAMA_MODEL = 'ctf-custom-q4'

# ============================================================
# Step 1: Play games
# ============================================================
def count_sessions():
    """Count existing session directories."""
    return len(glob.glob(os.path.join(LOGS_DIR, 'session-*')))


def play_games(n_games, dry_run=False, local_mode=False, self_play=False):
    """Run N games via the game engine in loop mode, then stop.

    Args:
        local_mode: Use --local-only (round-robin, avoids GPU contention).
        self_play: Use --self-play (6 custom bots vs each other, round-robin).
    """
    if dry_run:
        mode = "self-play" if self_play else ("local round-robin" if local_mode else "cloud parallel")
        print(f"  [DRY RUN] Would play {n_games} games ({mode})")
        return []

    start_count = count_sessions()
    target_count = start_count + n_games
    print(f"  Sessions before: {start_count}, target: {target_count}")

    # Build game command
    if self_play:
        # Self-play: 6 custom bots, round-robin, maximum training signal
        game_args = ['node', 'src/index.js', '--self-play', '--loop']
        print("  Mode: self-play (6 custom bots, round-robin)")
    elif local_mode:
        # Round-robin mode: each model gets dedicated GPU time, no contention
        game_args = ['node', 'src/index.js', '--local-only', '--custom-bot', '--loop']
        print("  Mode: local round-robin (no GPU contention)")
    else:
        game_args = ['node', 'src/index.js', '--cloud', '--custom-bot', '--loop']
        print("  Mode: cloud parallel")

    # Launch game process
    env = {**os.environ, 'NODE_ENV': 'production'}
    proc = subprocess.Popen(
        game_args,
        cwd=PROJECT_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,  # Create process group for clean kill
    )
    print(f"  Game process started (PID {proc.pid})")

    def kill_game():
        """Kill the game process group forcefully."""
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
            pass
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired, ChildProcessError, OSError):
            pass
        # Final cleanup — reap zombie
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass

    # Monitor session count with per-game timeout
    GAME_TIMEOUT = 20 * 60  # 20 min max per game — kill if hung
    last_completed = 0
    last_progress_time = time.time()

    try:
        while True:
            time.sleep(15)
            current = count_sessions()
            completed = current - start_count

            if completed > last_completed:
                # New game finished — reset timeout
                last_completed = completed
                last_progress_time = time.time()
                print(f"  Games completed: {completed}/{n_games}")

            if completed >= n_games:
                print(f"  Target reached ({completed} games). Stopping game process...")
                break

            # Check if process died
            if proc.poll() is not None:
                print(f"\n  Game process exited (code {proc.returncode})")
                break

            # Check for hung game — no new session in GAME_TIMEOUT seconds
            stall_time = time.time() - last_progress_time
            if stall_time > GAME_TIMEOUT:
                print(f"\n  Game hung ({stall_time/60:.0f} min since last progress). "
                      f"Killing and restarting...")
                kill_game()

                # Restart Ollama (likely in bad state)
                try:
                    subprocess.run(['pkill', 'ollama'], capture_output=True)
                    time.sleep(2)
                    subprocess.Popen(
                        ['/usr/bin/arch', '-arm64', '/usr/local/bin/ollama', 'serve'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        preexec_fn=os.setsid,
                    )
                    time.sleep(3)
                    print("  Ollama restarted.")
                except Exception as e:
                    print(f"  Ollama restart failed: {e}")

                # Relaunch game process
                proc = subprocess.Popen(
                    game_args,
                    cwd=PROJECT_DIR,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid,
                )
                last_progress_time = time.time()
                print(f"  Game process relaunched (PID {proc.pid})")
    finally:
        kill_game()

    # Collect new session dirs
    all_sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
    new_sessions = all_sessions[start_count:]
    print(f"  New sessions: {len(new_sessions)}")
    return new_sessions


# ============================================================
# Step 2: Extract training data
# ============================================================
def make_example(role, thinking, command):
    """Create a ChatML training example."""
    if role == 'attacker':
        system = ("You are an attacker in a CTF competition. Generate a shell command "
                  "to capture /root/flag.txt from target machines.")
    else:
        system = ("You are a defender in a CTF competition. Generate a shell command "
                  "to secure your machine and protect /root/flag.txt.")
    text = (f"<|im_start|>system\n{system}\nContext: {thinking[:500]}<|im_end|>\n"
            f"<|im_start|>assistant\nTHINKING: {thinking[:300]}\nCOMMAND: {command}<|im_end|>")
    return json.dumps({'text': text}) + '\n'


def extract_training_data(session_dirs):
    """Extract weighted training data from game sessions.

    Weighting strategy:
      Mixed mode (custom bot vs cloud):
        - Bot's successful attacks: 3x (reinforce what works)
        - Cloud attacks that captured bot's flag: 2x (learn to defend against these)
        - Cloud defense from games where bot failed to capture: 2x (learn to beat these)
        - Bot's other turns: 1x (maintain general knowledge)
        - Cloud model general turns: 1x (background knowledge)
      Self-play mode (all bots):
        - Every successful attack: 3x (all agents are the bot)
        - Every turn from agents whose flag survived: 2x (good defense)
        - All other turns: 1x
    """
    examples = []
    stats = {'bot_atk_success': 0, 'bot_atk_total': 0, 'bot_def_total': 0,
             'cloud_atk_vs_bot': 0, 'cloud_def_vs_bot': 0, 'cloud_other': 0}

    def is_bot_agent(agent_id):
        """Check if an agent is a custom bot (includes self-play bot-* names)."""
        return agent_id == 'custom-bot' or agent_id.startswith('bot-')

    for sd in session_dirs:
        if not os.path.isdir(sd):
            continue

        # Detect self-play session (all players are bot-*)
        gf = os.path.join(sd, 'game.json')
        is_self_play = False
        agents_in_session = set()
        flag_captured_by = {}   # victim -> set of attackers
        flag_survivors = set()  # agents whose flag was NOT captured

        if not os.path.exists(gf):
            continue
        events = json.load(open(gf))

        for e in events:
            if e.get('agent'):
                agents_in_session.add(e['agent'])
        is_self_play = all(is_bot_agent(a) for a in agents_in_session if a)

        for e in events:
            msg = e.get('message', '')
            if 'CAPTURED' in msg:
                attacker = e.get('attacker', '')
                victim = e.get('victim', '')
                if victim:
                    flag_captured_by.setdefault(victim, set()).add(attacker)

        # Find who survived (flag not captured)
        for e in events:
            if e.get('type') == 'scoreboard':
                scores = e.get('scores', {})
                for m in scores:
                    if m not in flag_captured_by:
                        flag_survivors.add(m)

        # For non-self-play: track bot-specific signals
        bot_flag_captured_by = set()
        bot_failed_to_capture = set()
        if not is_self_play:
            for victim, attackers in flag_captured_by.items():
                if is_bot_agent(victim):
                    for a in attackers:
                        if not is_bot_agent(a):
                            bot_flag_captured_by.add(a)

            for e in events:
                if e.get('type') == 'scoreboard':
                    scores = e.get('scores', {})
                    bot_caps = set()
                    for ev in events:
                        if ev.get('attacker') == 'custom-bot' and 'CAPTURED' in ev.get('message', ''):
                            bot_caps.add(ev.get('victim', ''))
                    for m in scores:
                        if m != 'custom-bot' and m not in bot_caps:
                            bot_failed_to_capture.add(m)

        # Process all agent files in this session
        for af in glob.glob(os.path.join(sd, '*-attacker.json')):
            agent = os.path.basename(af).replace('-attacker.json', '')
            for e in json.load(open(af)):
                if not e.get('turn') or not e.get('command') or not e.get('thinking'):
                    continue
                cmd, thinking = e['command'], e['thinking']
                if cmd in ('NONE', 'SKIP', '?') or len(cmd) < 4 or 'SECURITY AUDIT' in cmd:
                    continue
                result = str(e.get('result', ''))
                flag_captured = e.get('flagCaptured', False) or 'FLAG{' in result

                if is_self_play:
                    # Self-play: every agent is the bot. Weight by outcome.
                    weight = 3 if flag_captured else 1
                    stats['bot_atk_total'] += 1
                    if flag_captured:
                        stats['bot_atk_success'] += 1
                    for _ in range(weight):
                        examples.append(make_example('attacker', thinking, cmd))
                elif is_bot_agent(agent):
                    # Bot's own attacks (mixed mode)
                    weight = 3 if flag_captured else 1
                    stats['bot_atk_total'] += 1
                    if flag_captured:
                        stats['bot_atk_success'] += 1
                    for _ in range(weight):
                        examples.append(make_example('attacker', thinking, cmd))
                elif agent in bot_flag_captured_by:
                    # Cloud model that beat the bot — learn from its attacks
                    weight = 2
                    stats['cloud_atk_vs_bot'] += 1
                    for _ in range(weight):
                        examples.append(make_example('attacker', thinking, cmd))
                else:
                    stats['cloud_other'] += 1
                    examples.append(make_example('attacker', thinking, cmd))

        for df in glob.glob(os.path.join(sd, '*-defender.json')):
            agent = os.path.basename(df).replace('-defender.json', '')
            for e in json.load(open(df)):
                if not e.get('turn') or not e.get('command') or not e.get('thinking'):
                    continue
                cmd, thinking = e['command'], e['thinking']
                if cmd in ('NONE', 'SKIP', '?') or len(cmd) < 4 or 'SECURITY AUDIT' in cmd:
                    continue

                if is_self_play:
                    # Self-play: strongly weight survivors (good defense), skip
                    # defenders whose flag was captured (bad defense signal)
                    if agent in flag_survivors:
                        weight = 4  # Strong positive: this defense WORKED
                    else:
                        weight = 0  # Skip: this defense failed, don't reinforce it
                    stats['bot_def_total'] += 1
                    for _ in range(weight):
                        examples.append(make_example('defender', thinking, cmd))
                elif is_bot_agent(agent):
                    # Mixed mode: same logic — skip bot's defense if flag was lost
                    if agent in flag_survivors:
                        weight = 2
                    elif agent in flag_captured_by:
                        weight = 0  # Bot's defense failed, don't learn from it
                    else:
                        weight = 1
                    stats['bot_def_total'] += 1
                    for _ in range(weight):
                        examples.append(make_example('defender', thinking, cmd))
                elif agent in bot_failed_to_capture:
                    weight = 2
                    stats['cloud_def_vs_bot'] += 1
                    for _ in range(weight):
                        examples.append(make_example('defender', thinking, cmd))
                else:
                    stats['cloud_other'] += 1
                    examples.append(make_example('defender', thinking, cmd))

    # Dedup
    seen = set()
    deduped = []
    for line in examples:
        key = line[-200:]
        if key not in seen:
            seen.add(key)
            deduped.append(line)

    return deduped, stats


def get_generation_stats(session_dirs):
    """Compute aggregate stats across sessions.
    In self-play mode, reports average across all bot copies.
    In mixed mode, reports custom-bot stats only."""
    total_games = 0
    total_captures = 0
    total_losses = 0
    total_score = 0
    agent_count = 0

    for sd in session_dirs:
        gf = os.path.join(sd, 'game.json')
        if not os.path.exists(gf):
            continue
        events = json.load(open(gf))
        total_games += 1

        for e in events:
            if e.get('type') == 'scoreboard':
                scores = e.get('scores', {})
                for m, s in scores.items():
                    # In self-play, all agents are bot-*; in mixed, look for custom-bot
                    is_bot = (m == 'custom-bot' or m.startswith('bot-'))
                    if not is_bot:
                        continue
                    agent_count += 1
                    if isinstance(s, dict):
                        total_score += s.get('total', 0)
                        total_captures += s.get('flagsCaptured', 0)
                        total_losses += s.get('flagsLost', 0)
                    else:
                        total_score += s

    return {
        'games': total_games,
        'avg_score': round(total_score / max(agent_count, 1), 1),
        'total_captures': total_captures,
        'total_losses': total_losses,
        'avg_captures_per_game': round(total_captures / max(total_games, 1), 1),
        'avg_losses_per_game': round(total_losses / max(total_games, 1), 1),
    }


# ============================================================
# Step 3: Train
# ============================================================
def retrain(training_data, gen_num, iters_per_gen=500, dry_run=False):
    """Resume LoRA training from last checkpoint with new data."""
    if dry_run:
        print(f"  [DRY RUN] Would train {iters_per_gen} iterations on {len(training_data)} examples")
        return 0.0

    # Write combined data
    random.seed(42 + gen_num)
    random.shuffle(training_data)
    split = int(len(training_data) * 0.9)

    train_path = os.path.join(SCRIPT_DIR, 'train.jsonl')
    valid_path = os.path.join(SCRIPT_DIR, 'valid.jsonl')
    with open(train_path, 'w') as f:
        f.writelines(training_data[:split])
    with open(valid_path, 'w') as f:
        f.writelines(training_data[split:])

    print(f"  Train: {split}, Valid: {len(training_data) - split}")

    # Find current max iter from checkpoints
    checkpoints = glob.glob(os.path.join(ADAPTER_DIR, '*_adapters.safetensors'))
    max_iter = 0
    for cp in checkpoints:
        try:
            iter_num = int(os.path.basename(cp).split('_')[0])
            max_iter = max(max_iter, iter_num)
        except ValueError:
            pass

    target_iters = max_iter + iters_per_gen
    print(f"  Resuming from iter {max_iter}, training to iter {target_iters}")

    # Reduce learning rate each generation (simulated annealing)
    lr = max(1e-5, 5e-5 * (0.8 ** gen_num))
    print(f"  Learning rate: {lr:.1e}")

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
        '--learning-rate', str(lr),
        '--max-seq-length', '1024',
    ]

    print(f"  Running training...")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True, text=True)

    # Extract final val loss from output
    val_loss = None
    for line in result.stdout.split('\n') + result.stderr.split('\n'):
        if 'Val loss' in line:
            try:
                val_loss = float(line.split('Val loss')[1].split(',')[0].strip())
            except (ValueError, IndexError):
                pass

    if result.returncode != 0:
        print(f"  Training failed! Exit code {result.returncode}")
        print(result.stderr[-500:] if result.stderr else "No stderr")
        return val_loss or -1.0

    print(f"  Training complete. Val loss: {val_loss}")
    return val_loss


# ============================================================
# Step 4: Build model
# ============================================================
def build_model(dry_run=False):
    """Fuse LoRA adapter and create quantized Ollama model."""
    if dry_run:
        print("  [DRY RUN] Would fuse and quantize model")
        return True

    # Fuse
    print("  Fusing adapter into base model...")
    result = subprocess.run([
        '/usr/bin/arch', '-arm64', MLX_PYTHON, '-m', 'mlx_lm.fuse',
        '--model', BASE_MODEL,
        '--adapter-path', ADAPTER_DIR,
        '--save-path', FUSED_DIR,
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  Fuse failed: {result.stderr[-300:]}")
        return False

    # Create Ollama model
    print("  Creating Ollama Q4_K_M model...")
    modelfile = os.path.join(FUSED_DIR, 'Modelfile.q4')
    result = subprocess.run(
        ['ollama', 'create', OLLAMA_MODEL, '-f', modelfile, '--quantize', 'q4_K_M'],
        cwd=FUSED_DIR,
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"  Ollama create failed: {result.stderr[-300:]}")
        return False

    # Warm the model on GPU
    print("  Warming model on GPU...")
    import urllib.request
    try:
        payload = json.dumps({
            'model': OLLAMA_MODEL,
            'messages': [{'role': 'user', 'content': 'hi'}],
            'stream': False,
            'keep_alive': '60m',
            'options': {'num_predict': 1},
        }).encode()
        req = urllib.request.Request(
            'http://localhost:11434/api/chat',
            data=payload,
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req, timeout=120)
        print("  Model warm and ready.")
    except Exception as e:
        print(f"  Warm-up failed: {e}")

    return True


# ============================================================
# Main loop
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Self-play retraining pipeline')
    parser.add_argument('--generations', type=int, default=1, help='Number of generations to run')
    parser.add_argument('--games', type=int, default=50, help='Games per generation')
    parser.add_argument('--iters', type=int, default=500, help='Training iterations per generation')
    parser.add_argument('--local', action='store_true',
                        help='Use local round-robin mode instead of cloud parallel. '
                             'Avoids GPU contention — each model gets dedicated GPU time. '
                             'Games take longer (~15 min vs ~5.5 min) but no timeouts.')
    parser.add_argument('--self-play', action='store_true', dest='self_play',
                        help='Self-play mode: 6 copies of the custom bot compete against '
                             'each other. Round-robin scheduling. Every turn from every '
                             'agent is training data. Maximum training signal per game.')
    parser.add_argument('--skip-games', action='store_true', help='Skip game playing, retrain on existing data')
    parser.add_argument('--dry-run', action='store_true', help='Plan only, no execution')
    args = parser.parse_args()

    os.makedirs(GENERATIONS_DIR, exist_ok=True)

    # Determine starting generation
    existing_gens = glob.glob(os.path.join(GENERATIONS_DIR, 'gen-*'))
    start_gen = len(existing_gens)

    print("=" * 60)
    print("SELF-PLAY RETRAINING PIPELINE")
    print("=" * 60)
    print(f"  Generations: {args.generations} (starting from gen-{start_gen})")
    print(f"  Games per generation: {args.games}")
    print(f"  Training iters per generation: {args.iters}")
    print(f"  Skip games: {args.skip_games}")
    print(f"  Self-play: {args.self_play}")
    print(f"  Local mode: {args.local}")
    print(f"  Dry run: {args.dry_run}")
    print()

    # Load cumulative training data from all prior sessions
    all_sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
    print(f"Loading existing data from {len(all_sessions)} sessions...")
    cumulative_data, cumulative_stats = extract_training_data(all_sessions)
    print(f"  Cumulative examples: {len(cumulative_data)}")
    print()

    for gen in range(start_gen, start_gen + args.generations):
        gen_dir = os.path.join(GENERATIONS_DIR, f'gen-{gen}')
        os.makedirs(gen_dir, exist_ok=True)
        gen_start = time.time()

        print(f"{'=' * 60}")
        print(f"GENERATION {gen}")
        print(f"{'=' * 60}")

        # Step 1: Play games
        new_sessions = []
        if not args.skip_games:
            print(f"\n[Step 1] Playing {args.games} games...")
            new_sessions = play_games(args.games, dry_run=args.dry_run,
                                       local_mode=args.local, self_play=args.self_play)

            if new_sessions:
                game_stats = get_generation_stats(new_sessions)
                print(f"  Bot performance: avg score {game_stats['avg_score']}, "
                      f"{game_stats['total_captures']} total captures, "
                      f"{game_stats['total_losses']} total losses "
                      f"({game_stats['avg_captures_per_game']} caps/game, "
                      f"{game_stats['avg_losses_per_game']} losses/game)")
            else:
                game_stats = {'games': 0, 'avg_score': 0, 'total_captures': 0, 'total_losses': 0}
        else:
            print("\n[Step 1] Skipped (--skip-games)")
            game_stats = {'games': 0, 'avg_score': 0, 'total_captures': 0, 'total_losses': 0, 'note': 'skipped'}

        # Step 2: Extract new training data
        print(f"\n[Step 2] Extracting training data...")
        if new_sessions:
            new_data, new_stats = extract_training_data(new_sessions)
            print(f"  New examples: {len(new_data)}")
            print(f"  Bot attacks: {new_stats['bot_atk_total']} "
                  f"(captures: {new_stats['bot_atk_success']})")
            print(f"  Cloud attacks vs bot: {new_stats['cloud_atk_vs_bot']}")
            print(f"  Cloud defense vs bot: {new_stats['cloud_def_vs_bot']}")

            # Add new data to cumulative pool
            seen = set(line[-200:] for line in cumulative_data)
            added = 0
            for line in new_data:
                key = line[-200:]
                if key not in seen:
                    seen.add(key)
                    cumulative_data.append(line)
                    added += 1
            print(f"  Added {added} new unique examples (total: {len(cumulative_data)})")
        else:
            new_stats = {}
            print("  Using existing cumulative data only")

        # Step 3: Retrain
        print(f"\n[Step 3] Retraining ({args.iters} iterations)...")
        val_loss = retrain(cumulative_data, gen, iters_per_gen=args.iters, dry_run=args.dry_run)

        # Step 4: Build model
        print(f"\n[Step 4] Building model...")
        build_ok = build_model(dry_run=args.dry_run)

        # Save generation metrics
        elapsed = time.time() - gen_start
        gen_metrics = {
            'generation': gen,
            'timestamp': datetime.now().isoformat(),
            'elapsed_minutes': round(elapsed / 60, 1),
            'game_stats': game_stats,
            'training_stats': {
                'cumulative_examples': len(cumulative_data),
                'new_examples': len(new_data) if new_sessions else 0,
                'val_loss': val_loss,
                'iters': args.iters,
            },
            'extraction_stats': new_stats if new_sessions else {},
            'build_success': build_ok,
        }

        stats_path = os.path.join(gen_dir, 'stats.json')
        if not args.dry_run:
            with open(stats_path, 'w') as f:
                json.dump(gen_metrics, f, indent=2)

        print(f"\n{'=' * 60}")
        print(f"GENERATION {gen} COMPLETE ({elapsed/60:.1f} min)")
        print(f"  Avg score: {game_stats.get('avg_score', 'N/A')}")
        print(f"  Captures/game: {game_stats.get('avg_captures_per_game', 'N/A')}")
        print(f"  Val loss: {val_loss}")
        print(f"  Build: {'OK' if build_ok else 'FAILED'}")
        print(f"  Metrics saved to: {stats_path}")
        print(f"{'=' * 60}\n")

    # Summary across all generations
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE — GENERATION SUMMARY")
    print("=" * 60)
    for gen_dir in sorted(glob.glob(os.path.join(GENERATIONS_DIR, 'gen-*'))):
        stats_file = os.path.join(gen_dir, 'stats.json')
        if os.path.exists(stats_file):
            s = json.load(open(stats_file))
            gs = s.get('game_stats', {})
            ts = s.get('training_stats', {})
            print(f"  {os.path.basename(gen_dir)}: "
                  f"score={gs.get('avg_score', '?')}, "
                  f"caps/game={gs.get('avg_captures_per_game', '?')}, "
                  f"loss/game={gs.get('avg_losses_per_game', '?')}, "
                  f"val_loss={ts.get('val_loss', '?')}, "
                  f"examples={ts.get('cumulative_examples', '?')}")


if __name__ == '__main__':
    main()
