#!/usr/bin/env python3
"""Extract defense-focused training data with vulnerability-check feedback loop.

This is the asymmetric-training data pipeline for v7. It weights defender turns by
whether they actually patched an unpatched vulnerability (per vuln_check events in
game.json), and includes Gemini 3 Flash defense chains as positive exemplars.

Weighting scheme (per defender turn):
  - Addressed >=1 unpatched vuln AND player survived:  6x (gold signal)
  - Addressed >=1 unpatched vuln AND player flag lost: 3x (patched but too slow)
  - Addressed 0 vulns AND player survived:             1x (harmless background)
  - Turn BEFORE flag was captured:                     0x (bad defense — skip)
  - Turn AFTER flag was captured:                      0x (irrelevant — skip)

Attacker turns (cheat-mode and normal):
  - Successful flag capture: 3x (preserve offense capability)
  - Cheat-mode attacker turn: 1x (don't over-reinforce "told the answer")
  - Normal attacker turn: 1x

External data:
  - training/gemini3_defense_chains.jsonl: Gemini 3 defense conversation chains,
    weighted 2x as positive exemplars.
"""

import glob
import json
import os
from datetime import datetime


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, '..', 'logs')
GEMINI_CHAINS_PATH = os.path.join(SCRIPT_DIR, 'gemini3_defense_chains.jsonl')

VULN_NAMES = ['root_password', 'sudo', 'web_service', 'shadow_perms', 'cron', 'suid_find']

# Weight multiplier for Gemini 3 defense chains (raised from 2 → 5 to dominate
# the training pool over the bot's own neutral/bad defense habits).
GEMINI_CHAIN_WEIGHT = 5

# Any defender command containing one of these external IPs is treated as an
# attack on another machine, not a defense action — assign weight 0.
OTHER_IPS = {'10.10.0.10', '10.10.0.20', '10.10.0.30',
             '10.10.0.40', '10.10.0.50', '10.10.0.60'}


def _is_external_defense(cmd, own_ip=None):
    """Detect defender commands that target another machine's IP.
    These are attack behaviors misapplied as defense and should be filtered.
    Own IP is allowed (the defender curling itself is fine for self-testing)."""
    if not cmd:
        return False
    for ip in OTHER_IPS:
        if ip in cmd and ip != own_ip:
            return True
    return False


def _parse_timestamp(ts):
    """Parse ISO 8601 timestamp from game logs. Return None if unparseable."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def _parse_vuln_report(report_value):
    """Vuln check reports store remaining (unpatched) vulns as a comma-separated string.
    Returns a set of vuln names."""
    if isinstance(report_value, list):
        return set(v.strip() for v in report_value if v)
    if isinstance(report_value, str):
        return set(v.strip() for v in report_value.split(',') if v.strip())
    return set()


def _build_vuln_timeline(events):
    """Build a per-player list of (timestamp, set-of-unpatched-vulns) tuples,
    sorted chronologically. Uses game_event entries with message 'Vulnerability check'.
    """
    timelines = {}
    for e in events:
        if e.get('type') != 'game_event':
            continue
        if e.get('message') != 'Vulnerability check':
            continue
        report = e.get('report', {})
        if not isinstance(report, dict):
            continue
        ts = _parse_timestamp(e.get('timestamp'))
        if ts is None:
            continue
        for player_id, vulns_value in report.items():
            unpatched = _parse_vuln_report(vulns_value)
            timelines.setdefault(player_id, []).append((ts, unpatched))
    for player_id in timelines:
        timelines[player_id].sort(key=lambda x: x[0])
    return timelines


def _flag_lost_time(events, player_id):
    """Return timestamp when player_id's flag was first captured, or None if it survived."""
    for e in events:
        msg = e.get('message', '')
        if 'CAPTURED' in msg and e.get('victim') == player_id:
            return _parse_timestamp(e.get('timestamp'))
    return None


def _is_self_play_session(agents):
    """True if all agents are self-play bot-* copies or custom-bot."""
    if not agents:
        return False
    for a in agents:
        if not (a == 'custom-bot' or a.startswith('bot-')):
            return False
    return True


def _is_bot_agent(agent_id):
    return agent_id == 'custom-bot' or agent_id.startswith('bot-')


def _make_example(role, thinking, command):
    """Build a ChatML-format training example matching the existing pipeline."""
    if role == 'attacker':
        system = ("You are an attacker in a CTF competition. Generate a shell command "
                  "to capture /root/flag.txt from target machines.")
    else:
        system = ("You are a defender in a CTF competition. Generate a shell command "
                  "to secure your machine and protect /root/flag.txt.")
    text = (f"<|im_start|>system\n{system}\nContext: {thinking[:500]}<|im_end|>\n"
            f"<|im_start|>assistant\nTHINKING: {thinking[:300]}\nCOMMAND: {command}<|im_end|>")
    return json.dumps({'text': text}) + '\n'


def _weight_defender_turn(turn_ts, player_id, flag_lost_ts, timeline,
                          attackers_cheating, command=None, own_ip=None):
    """Return the training weight (0-6) for a defender turn based on vuln-check feedback.

    Args:
        turn_ts: datetime of the defender turn
        player_id: id of the defender
        flag_lost_ts: datetime when this player's flag was captured (None if survived)
        timeline: sorted list of (ts, unpatched_set) checkpoints for this player
        attackers_cheating: True if this session used cheat-mode attackers
        command: the actual shell command (used to filter external-IP attacks mislabeled as defense)
        own_ip: the defender's own IP (allowed in curl commands for self-testing)
    """
    # Fix #1: filter out curl/probe commands targeting other machines — those are
    # attack behaviors, not defense. Weight 0.
    if command and _is_external_defense(command, own_ip):
        return 0

    if turn_ts is None:
        return 1  # fallback neutral

    # Skip turns after the flag was captured — defense is irrelevant once lost
    if flag_lost_ts and turn_ts > flag_lost_ts:
        return 0

    # Find the vuln-check before and after this turn
    before = None
    after = None
    for ts, vulns in timeline:
        if ts <= turn_ts:
            before = (ts, vulns)
        elif ts > turn_ts and after is None:
            after = (ts, vulns)
            break

    # If we have both checkpoints, compute which vulns were patched between them
    addressed = set()
    if before and after:
        addressed = before[1] - after[1]  # set difference: what disappeared

    player_survived = flag_lost_ts is None

    if len(addressed) >= 1:
        # Actually patched something observable
        if player_survived:
            return 6  # gold — patched and kept the flag
        return 3      # patched, but too slow to save the flag

    # No vuln-check evidence of patching
    if not player_survived:
        return 0      # turn happened while flag was being taken — bad defense
    return 1          # harmless background action


def extract_defense_training_data(session_dirs, include_gemini_chains=True,
                                   only_defense_training_sessions=False):
    """Extract weighted training examples from the given session directories.

    Args:
        session_dirs: list of session directory paths
        include_gemini_chains: if True, include gemini3_defense_chains.jsonl (2x weight)
        only_defense_training_sessions: if True, only include sessions where the
            attacker used cheat-mode (detected via the config logged in game.json)

    Returns:
        (examples, stats) — list of ChatML lines and a statistics dict
    """
    examples = []
    stats = {
        'sessions_processed': 0,
        'cheat_mode_sessions': 0,
        'def_gold': 0,              # weight 6
        'def_patched': 0,           # weight 3
        'def_neutral': 0,           # weight 1
        'def_skipped': 0,           # weight 0 (flag lost OR external-IP command)
        'def_ext_ip_filtered': 0,   # subset of skipped: commands targeting other IPs
        'atk_capture': 0,           # weight 3
        'atk_normal': 0,            # weight 1
        'gemini_chains': 0,
    }

    for sd in session_dirs:
        gf = os.path.join(sd, 'game.json')
        if not os.path.exists(gf):
            continue
        try:
            events = json.load(open(gf))
        except (json.JSONDecodeError, OSError):
            continue

        agents = set(e.get('agent', '') for e in events if e.get('agent'))
        if not agents:
            continue

        # Detect cheat-mode: defenseTraining was set in the logged game config
        cheat_mode = False
        for e in events:
            cfg = e.get('config')
            if isinstance(cfg, dict) and cfg.get('defenseTraining'):
                cheat_mode = True
                break

        if only_defense_training_sessions and not cheat_mode:
            continue

        stats['sessions_processed'] += 1
        if cheat_mode:
            stats['cheat_mode_sessions'] += 1

        # Build per-player vuln-check timelines and flag-loss timestamps
        timelines = _build_vuln_timeline(events)
        flag_lost_ts = {p: _flag_lost_time(events, p) for p in agents}

        # Extract per-player own IP from the networkInfo event
        player_ips = {}
        for e in events:
            if e.get('message') == 'Game initialized' and 'networkInfo' in e:
                for pid, info in e['networkInfo'].items():
                    if isinstance(info, dict) and info.get('ip'):
                        player_ips[pid] = info['ip']
                break

        # Process attacker files
        for af in glob.glob(os.path.join(sd, '*-attacker.json')):
            agent = os.path.basename(af).replace('-attacker.json', '')
            try:
                turns = json.load(open(af))
            except (json.JSONDecodeError, OSError):
                continue
            for e in turns:
                if not e.get('turn') or not e.get('command') or not e.get('thinking'):
                    continue
                cmd = e['command']
                thinking = e['thinking']
                if cmd in ('NONE', 'SKIP', '?') or len(cmd) < 4 or 'SECURITY AUDIT' in cmd:
                    continue

                result = str(e.get('result', ''))
                captured_flag = e.get('flagCaptured', False) or 'FLAG{' in result

                if captured_flag:
                    weight = 3
                    stats['atk_capture'] += 1
                else:
                    weight = 1
                    stats['atk_normal'] += 1

                ex = _make_example('attacker', thinking, cmd)
                examples.extend([ex] * weight)

        # Process defender files with vuln-check correlation
        for df in glob.glob(os.path.join(sd, '*-defender.json')):
            agent = os.path.basename(df).replace('-defender.json', '')
            try:
                turns = json.load(open(df))
            except (json.JSONDecodeError, OSError):
                continue

            player_timeline = timelines.get(agent, [])
            player_flag_lost_ts = flag_lost_ts.get(agent)

            for e in turns:
                if not e.get('turn') or not e.get('command') or not e.get('thinking'):
                    continue
                cmd = e['command']
                thinking = e['thinking']
                if cmd in ('NONE', 'SKIP', '?') or len(cmd) < 4 or 'SECURITY AUDIT' in cmd:
                    continue

                turn_ts = _parse_timestamp(e.get('timestamp'))
                own_ip = player_ips.get(agent)
                # Track external-IP filtering explicitly
                is_external = _is_external_defense(cmd, own_ip)
                if is_external:
                    stats['def_ext_ip_filtered'] += 1
                weight = _weight_defender_turn(
                    turn_ts, agent, player_flag_lost_ts, player_timeline, cheat_mode,
                    command=cmd, own_ip=own_ip,
                )
                if weight == 6:
                    stats['def_gold'] += 1
                elif weight == 3:
                    stats['def_patched'] += 1
                elif weight == 1:
                    stats['def_neutral'] += 1
                else:
                    stats['def_skipped'] += 1

                if weight > 0:
                    ex = _make_example('defender', thinking, cmd)
                    examples.extend([ex] * weight)

    # Merge in Gemini 3 defense chains as positive exemplars.
    # Weight 5x (up from 2x) so these dominate the bot's own neutral defense
    # habits in the training pool.
    if include_gemini_chains and os.path.exists(GEMINI_CHAINS_PATH):
        with open(GEMINI_CHAINS_PATH) as f:
            chains = f.readlines()
        for line in chains:
            for _ in range(GEMINI_CHAIN_WEIGHT):
                examples.append(line)
        stats['gemini_chains'] = len(chains)
        stats['gemini_chain_weight'] = GEMINI_CHAIN_WEIGHT

    # Dedup by last 200 chars
    seen = set()
    deduped = []
    for line in examples:
        key = line[-200:]
        if key not in seen:
            seen.add(key)
            deduped.append(line)

    return deduped, stats


if __name__ == '__main__':
    # Quick smoke test: extract from all sessions, print stats
    all_sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
    print(f"Scanning {len(all_sessions)} sessions...")
    examples, stats = extract_defense_training_data(all_sessions, include_gemini_chains=True)
    print(f"\nStats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"\nTotal (deduped) examples: {len(examples)}")
