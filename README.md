# AI Capture the Flag (CTF)

An AI-vs-AI cybersecurity research platform. Multiple large language models
attack and defend isolated Docker containers in a capture-the-flag tournament,
producing logs, statistical reports, and training data for fine-tuning a
custom model.

The project supports two parallel research tracks:

1. **Comparative offense / defense study** of frontier and mid-size LLMs
   (GPT-OSS 120B, Gemini 3 Flash, GLM-5.1, Nemotron 3 Super, RNJ-1 8B).
2. **Custom-bot pipeline** — a 3 B Qwen2.5 model fine-tuned with LoRA on
   game replays, iteratively improved through self-play and a defense-focused
   training loop, then quantized to Q4_K_M and served locally via Ollama.

## Quick start

```bash
npm install
cp .env.example .env       # add OLLAMA_API_KEY (cloud) or GEMINI_API_KEY etc.
npm run build-docker       # build the privileged target containers
npm run up                 # boot containers
npm run start:cloud        # run one cloud match
```

Each match emits a directory under `logs/session-<timestamp>/` containing a
full event stream (`game.json`), per-agent transcripts, and a summary.

## Game modes

| Mode                          | Command                              | What happens                                  |
| ----------------------------- | ------------------------------------ | --------------------------------------------- |
| Cloud, all 5 models           | `npm run start:cloud`                | round-robin across cloud-hosted models        |
| Cloud + custom bot            | `npm run start:cloud:custom`         | 5 cloud models plus the local custom bot      |
| Local Ollama only             | `npm run start:local-only`           | all agents from a local Ollama instance       |
| Self-play (training)          | `npm run start:self-play`            | one model fights itself, used for replay data |
| Defense training (asymmetric) | `--defense-training`                 | attackers see vuln list; defender data only   |
| Loop forever                  | append `--loop` to any of the above  | restarts a new game when one finishes         |

Match structure: a 30-second **defense phase** (everyone hardens their box),
followed by a 5-minute **battle phase** where agents look for flags on
opponents' machines. Captures and bonus flags score points; losing your
flag costs points. Scores are persisted in each session's `game.json`.

## Repository layout

```
src/                       game engine, agents, container manager
  ai-agents/               attacker.js, defender.js, model adapters
  game-engine.js           round-robin scheduler, scoring
  config.js                CLI flags / env vars
  docker-manager.js        privileged container lifecycle
docker/                    Dockerfiles for the 8 vulnerable target machines
training/                  custom-bot LoRA pipeline (MLX)
  prepare_data.py          extract training pairs from session logs
  finetune.py              MLX LoRA fine-tune entry point
  self_play_loop.py        v1 → v6 self-play training loop
  defense_retrain.py       v7 defense-focused retrain (vuln-check feedback)
  defense_training_data.py weighted dataset builder for defense training
  ctf-model-lora/          active LoRA adapter
  ctf-model-fused/         fused full-precision model (input to quantization)
  gemini3_defense_chains.jsonl strong defender exemplars
reports/                   analysis pipeline + viewers
  generate_comprehensive_reports.py  technical + non-technical APA Word reports
  session_viewer.py        per-session HTML replay (commands + timeline)
  session_video.py         per-session MP4 replay
  videos/                  rendered MP4s
logs/                      one folder per game session
```

## Custom bot training pipeline

The custom bot is a Qwen2.5-3B-Instruct base trained with MLX LoRA on game
replays from the cloud agents. Versions and what changed:

- **v1–v4** — initial supervised fine-tunes on extracted attacker/defender
  command sequences.
- **v5** — first self-play loop. Rolled back: the loop reinforced the bot's
  own mistakes (defender regressed, attacker plateaued).
- **v6** — replaced self-play with a curated mix that down-weights losing
  moves. Strong offense (1.86 captures/game), weak defense (0.59 losses/game).
- **v7** — defense-focused retrain on top of v6: cheat-mode attackers, a
  vuln-check feedback loop that rewards defender turns that actually patched
  an unpatched vulnerability, and Gemini-3 defender chains as exemplars.
  Rolled back after evaluation regressed offense more than it gained on
  defense; v6 remains the active build.
- **v8** — in progress. Current adapter lives in `training/ctf-model-lora/`.

To rebuild the custom bot from current adapter state:

```bash
python3 training/finetune.py --fuse           # fuse LoRA into base
python3 training/self_play_loop.py --build    # quantize to Q4_K_M, push to Ollama
ollama list | grep ctf-custom                 # confirm `ctf-custom-q4` is registered
```

To run the v7 defense retrain (orchestrator handles play → extract → train →
quantize → deploy):

```bash
python3 training/defense_retrain.py --games 50 --iters 300
```

Pass `--rollback` to restore the v6 snapshot if results regress.

## Reports & viewers

```bash
# APA Word reports (technical + non-technical) with embedded charts
python3 reports/generate_comprehensive_reports.py

# Per-session HTML viewer with command translations and timelines
python3 reports/session_viewer.py --all
open reports/viewers/index.html

# Per-session MP4 replay (matplotlib + ffmpeg)
python3.11 reports/session_video.py logs/session-2026-04-20T16-21-35-241Z
python3.11 reports/session_video.py --all --recent 10
```

The reports include scoring trends, attack-method breakdowns, capture-vs-loss
balance, and the plain-language category mapping (e.g. "Login Attempts" for
SSH/Hydra) used in the non-technical version.

The video viewer renders a 30-second animated replay per session with a live
scoreboard, a sweeping timeline cursor, capture markers, and an event log.
ffmpeg is required.

## Requirements

- Node.js ≥ 20
- Docker (privileged containers)
- Python 3.11
  - `pip install python-docx matplotlib numpy scipy pandas mlx mlx-lm`
- ffmpeg (for `session_video.py`)
- An Ollama API key (for cloud matches) or a local Ollama instance (for the
  custom bot and `--local-only` matches)
- Optional: Gemini, Anthropic, or OpenAI keys to extend the model roster

## Research note

Sessions, training data, and rendered reports under `logs/`, `training/`, and
`reports/` are the raw artifacts behind the project's findings. The headline
result so far: a 3 B locally-served custom bot trained on tournament replays
matches mid-size cloud models on offense (≈ 1.86 captures/game) but trails
frontier models on defense — the v7 work explores how to close that gap with
asymmetric self-play.
