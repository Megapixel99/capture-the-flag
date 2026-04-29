#!/usr/bin/env python3
"""Generate a self-contained HTML page summarizing a single CTF session for
non-technical readers. No external dependencies — just open the HTML file in
a browser. Also supports a bulk mode that generates an index.html listing
every session.

Usage:
  python3 reports/session_viewer.py <session-dir>           # One session
  python3 reports/session_viewer.py --all                   # All sessions + index
  python3 reports/session_viewer.py --all --recent 20       # Last 20 sessions
  python3 reports/session_viewer.py --output viewers/       # Custom output dir
"""

import argparse
import glob
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, '..', 'logs')
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'viewers')


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

MODEL_FRIENDLY_NAMES = {
    'custom-bot': 'Custom Bot (3B fine-tuned)',
    'gpt-oss': 'GPT-OSS 120B (OpenAI)',
    'glm5': 'GLM-5.1 (Zhipu AI)',
    'gemini3': 'Gemini 3 Flash (Google)',
    'nemotron': 'Nemotron 3 Super (NVIDIA)',
    'rnj': 'RNJ-1 8B (Zhipu AI)',
    'bot-alpha': 'Bot Alpha',
    'bot-bravo': 'Bot Bravo',
    'bot-charlie': 'Bot Charlie',
    'bot-delta': 'Bot Delta',
    'bot-echo': 'Bot Echo',
    'bot-foxtrot': 'Bot Foxtrot',
}

MODEL_COLORS = {
    'custom-bot': '#14B8A6',
    'gpt-oss':   '#4A90D9',
    'glm5':      '#9B59B6',
    'gemini3':   '#50C878',
    'nemotron':  '#F5A623',
    'rnj':       '#E85D4A',
    'bot-alpha':   '#FF6B9D',
    'bot-bravo':   '#FF9F6B',
    'bot-charlie': '#FFD36B',
    'bot-delta':   '#6BFF9F',
    'bot-echo':    '#6B9FFF',
    'bot-foxtrot': '#D36BFF',
}


def friendly(pid):
    return MODEL_FRIENDLY_NAMES.get(pid, pid)


# ---------------------------------------------------------------------------
# Command → plain-English translation for non-technical readers
# ---------------------------------------------------------------------------

# Patterns are evaluated in order; first match wins. Regex is case-insensitive.
ATTACK_PATTERNS = [
    # SSH credential attacks
    (r"sshpass\s+-p\s+['\"]?toor['\"]?.*ssh.*root@", "Tried to log in as administrator using the common password \"toor\""),
    (r"sshpass\s+-p\s+['\"]?service123['\"]?.*ssh.*ctfservice@", "Tried to log in using the leaked service-account password"),
    (r"sshpass\s+-p\s+['\"]?\S+['\"]?.*ssh", "Tried to log in over SSH with a guessed password"),
    (r"hydra.*ssh", "Ran rapid password guessing against SSH (brute-force attack)"),
    (r"hydra", "Ran a rapid password-guessing attack"),
    # Web service exploits
    (r"curl.*ping\?host=.*[;|`].*flag", "Exploited the web service to trick it into reading the secret flag file"),
    (r"curl.*ping\?host=", "Tried to inject a command through the web service's ping feature"),
    (r"curl.*/exec\?cmd=", "Tried to run a command through a web service endpoint"),
    (r"curl.*flag", "Tried to fetch the flag through the web service"),
    (r"nikto", "Scanned a web service for known weaknesses"),
    # Scanning / reconnaissance
    (r"nmap.*-p-", "Scanned the target machine for every open network port"),
    (r"nmap.*-p\s*\d+", "Scanned specific ports on the target machine"),
    (r"nmap", "Mapped the target machine's open network services"),
    (r"nc\s+.*-z|netcat\s+.*-z|nc\s+-vz", "Probed specific network ports to see what's listening"),
    # Flag grabbing patterns via already-compromised access
    (r"cat\s+/root/flag\.txt|cat.*flag\.txt", "Read the flag file directly"),
    (r"find.*-exec.*flag|find.*-exec.*/bin/sh", "Used a misconfigured system tool to read protected files"),
    (r"john|hashcat", "Attempted to crack password hashes"),
    # HTTP probes
    (r"curl\s+.*(admin|login|server|config|\.env|api)", "Probed web service pages for weak spots"),
    (r"curl\s+.*https?://", "Fetched a web page from the target"),
    (r"curl", "Contacted the target's web service"),
    # Generic recon
    (r"^echo .* > /dev/tcp", "Probed a network port manually"),
]

DEFENSE_PATTERNS = [
    # Password hardening
    (r"chpasswd|passwd\s+root|echo\s+['\"]?root:.*\|\s*chpasswd", "Changed the administrator password to a strong random value"),
    (r"echo\s+['\"]?ctfservice:|passwd\s+ctfservice", "Changed the service account's password"),
    # File permission fixes
    (r"chmod\s+(640|600|000)\s+/etc/shadow|chmod\s+.*/etc/shadow", "Restricted access to the password hash file"),
    (r"chmod\s+u-s\s+/usr/bin/find|chmod.*find", "Removed dangerous root privileges from the 'find' tool"),
    (r"chmod\s+u-s\b", "Removed special administrator privileges from a system binary"),
    (r"chmod\s+(600|640|644|700|750|755)\s+/opt/scripts/backup\.sh|chmod.*backup\.sh", "Secured the scheduled backup script"),
    (r"chmod\s+.*cron", "Locked down a scheduled-task file"),
    (r"chmod\s+.*database\.yml|chmod.*\.env", "Restricted access to a config file containing passwords"),
    (r"rm\s+.*bash_history|\>\s*.*bash_history", "Wiped the shell history (was leaking credentials)"),
    # Sudo
    (r"rm\s+.*sudoers\.d/ctfservice|visudo|sudoers", "Removed the unsafe sudo shortcut for the service account"),
    # Firewall
    (r"iptables\s+-[AI]\s+INPUT.*DROP|iptables.*-P\s+INPUT\s+DROP", "Set up a firewall to block unwanted incoming connections"),
    (r"iptables", "Configured firewall rules"),
    (r"ufw\s+enable|ufw\s+deny", "Turned on the host firewall"),
    # Web service
    (r"sed\s+-i.*server\.py|sed\s+.*ping.*server\.py", "Patched the vulnerable web service code"),
    (r"kill.*server\.py|pkill.*server\.py|pkill.*python.*8080|systemctl\s+stop.*webapp|systemctl\s+stop.*web", "Shut down the vulnerable web service"),
    (r"rm\s+.*server\.py|mv\s+.*server\.py|>\s*.*server\.py", "Removed the vulnerable web service"),
    # Service / process
    (r"systemctl\s+stop|service\s+.*stop", "Stopped a running service"),
    (r"systemctl\s+restart\s+ssh|service\s+ssh\s+restart", "Restarted SSH (after changing its config)"),
    (r"sed\s+-i.*PermitRootLogin.*no|echo\s+['\"]?PermitRootLogin\s+no", "Disabled remote administrator login"),
    (r"PasswordAuthentication\s+no", "Required key-based login instead of passwords"),
    # Monitoring / auditing
    (r"^ls\s|ls\s+-", "Looked at files on the system"),
    (r"cat\s+/etc/shadow", "Inspected the password hash file (for audit)"),
    (r"cat\s+/etc/passwd", "Listed all user accounts"),
    (r"cat\s+/etc/sudoers|cat\s+/etc/sudoers\.d", "Reviewed the sudo configuration"),
    (r"find\s+.*-perm.*4000|find\s+.*-perm.*-4000|find.*suid", "Searched for files with dangerous admin privileges"),
    (r"^ps\s|\bps\s+aux|\bps\s+ef", "Checked what programs are running"),
    (r"\bss\b|\bnetstat\b", "Checked which network services are listening"),
    (r"\bgrep\b", "Searched for specific text in files"),
    (r"^cat\s+/|^cat\s", "Read the contents of a file"),
    (r"^find\s", "Searched the filesystem"),
    (r"curl\s+.*(localhost|127\.0\.0\.1)", "Tested its own web service locally"),
    (r"chattr\s+\+i|lsattr", "Protected the flag file from being modified"),
]


def describe_command(cmd, role):
    """Return a plain-English description of a shell command.
    Non-technical readers see this instead of the raw shell command."""
    if not cmd:
        return "(no command)"
    cmd_lower = cmd.lower().strip()
    patterns = ATTACK_PATTERNS if role == 'attacker' else DEFENSE_PATTERNS
    for regex, desc in patterns:
        if re.search(regex, cmd_lower, re.IGNORECASE):
            return desc
    # Fallback — show the first tool name in plain language
    first_tool = cmd.strip().split(None, 1)[0].split('/')[-1]
    generic_map = {
        'echo': 'Ran a simple command',
        'cat': 'Read a file',
        'ls': 'Listed files in a directory',
        'ps': 'Checked running programs',
        'grep': 'Searched text in files',
        'find': 'Searched the filesystem',
        'curl': 'Made a web request',
        'wget': 'Downloaded a file',
        'ssh': 'Tried to log into another machine',
        'chmod': 'Changed file permissions',
        'chown': 'Changed file ownership',
        'sed': 'Edited a file in-place',
        'awk': 'Extracted text from a file',
        'sudo': 'Ran a command as administrator',
        'kill': 'Stopped a running process',
        'pkill': 'Stopped a running process by name',
        'rm': 'Deleted a file',
        'mv': 'Moved or renamed a file',
        'cp': 'Copied a file',
        'iptables': 'Configured the firewall',
        'systemctl': 'Managed a system service',
    }
    if first_tool in generic_map:
        return generic_map[first_tool]
    if role == 'attacker':
        return "Ran a custom attack command"
    return "Ran a system command"


def color_for(pid):
    return MODEL_COLORS.get(pid, '#888888')


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def load_session(session_dir):
    """Load the raw data for a session. Returns a dict of structured data,
    or None if the session has no usable game.json."""
    gf = os.path.join(session_dir, 'game.json')
    if not os.path.exists(gf):
        return None
    try:
        events = json.load(open(gf))
    except (json.JSONDecodeError, OSError):
        return None

    # Identify known timestamps
    game_start = None
    defense_start = None
    battle_start = None
    game_end = None
    for e in events:
        msg = e.get('message', '')
        ts = parse_ts(e.get('timestamp'))
        if msg.startswith('Game started') and game_start is None:
            game_start = ts
        elif msg.startswith('Defense phase started') and defense_start is None:
            defense_start = ts
        elif msg.startswith('Battle phase started') and battle_start is None:
            battle_start = ts
        elif msg.startswith('Battle phase ended') or 'Game ended' in msg or 'GAME OVER' in msg.upper():
            game_end = ts

    if game_start is None:
        # Fallback: first event's timestamp
        for e in events:
            ts = parse_ts(e.get('timestamp'))
            if ts:
                game_start = ts
                break
    if game_end is None:
        # Fallback: last scoreboard or last event
        for e in reversed(events):
            ts = parse_ts(e.get('timestamp'))
            if ts:
                game_end = ts
                break

    # Game config (mode detection)
    config = {}
    for e in events:
        if isinstance(e.get('config'), dict):
            config = e['config']
            break

    # Players and their IPs
    network_info = {}
    for e in events:
        if 'networkInfo' in e and isinstance(e['networkInfo'], dict):
            network_info = e['networkInfo']
            break

    player_ids = list(network_info.keys())
    if not player_ids:
        # Fallback — gather from agent fields
        player_ids = sorted(set(e.get('agent', '') for e in events if e.get('agent')))

    # Captures in chronological order.
    # The game engine writes TWO distinct event shapes:
    #   - Main flag: message starts "FLAG CAPTURED:", flag text in `flag`
    #   - Bonus flag: message starts "BONUS CAPTURED:", flag text in `bonus`,
    #     with a `tier` field and no isFirstCapture (bonuses can be found by
    #     multiple attackers but each pays out fully once per attacker)
    captures = []
    for e in events:
        msg = e.get('message', '')
        is_bonus = 'BONUS CAPTURED' in msg or bool(e.get('bonus'))
        is_flag = 'FLAG CAPTURED' in msg
        if not (is_bonus or is_flag):
            continue
        captures.append({
            'ts': parse_ts(e.get('timestamp')),
            'attacker': e.get('attacker', ''),
            'victim': e.get('victim', ''),
            'flag': e.get('flag', '') or e.get('bonus', ''),
            'is_first': bool(e.get('isFirstCapture')),
            'points': e.get('pointsAwarded', 0),
            'is_bonus': is_bonus,
            'tier': e.get('tier', ''),
        })

    # Vulnerability checks (list of (ts, {player: remaining_vulns}))
    vuln_checks = []
    for e in events:
        if e.get('type') == 'game_event' and e.get('message') == 'Vulnerability check':
            report = e.get('report', {})
            vuln_checks.append({'ts': parse_ts(e.get('timestamp')), 'report': report})

    # Vulnerability injections
    injections = []
    for e in events:
        msg = e.get('message', '')
        if 'Vulnerability injected' in msg or 'VULNERABILITY INJECTED' in msg:
            injections.append({
                'ts': parse_ts(e.get('timestamp')),
                'victim': e.get('player') or e.get('victim', ''),
                'vuln': e.get('vulnName') or msg,
            })

    # Final scores
    final_scores = {}
    for e in events:
        if e.get('type') == 'scoreboard':
            scores = e.get('scores', {})
            if scores:
                final_scores = scores

    # Per-player turn counts (from agent files)
    turn_counts = defaultdict(lambda: {'attacker': 0, 'defender': 0})
    command_samples = defaultdict(lambda: {'attacker': [], 'defender': []})
    first_actions = {}
    for role in ('attacker', 'defender'):
        for af in glob.glob(os.path.join(session_dir, f'*-{role}.json')):
            agent = os.path.basename(af).replace(f'-{role}.json', '')
            try:
                data = json.load(open(af))
            except (json.JSONDecodeError, OSError):
                continue
            for turn in data:
                if not turn.get('turn') or not turn.get('command'):
                    continue
                cmd = turn['command']
                if cmd in ('NONE', 'SKIP', '?'):
                    continue
                turn_counts[agent][role] += 1
                # Keep more samples so we can dedupe by plain-English description
                if len(command_samples[agent][role]) < 15:
                    command_samples[agent][role].append(cmd)

    return {
        'session_dir': session_dir,
        'session_name': os.path.basename(session_dir),
        'events': events,
        'config': config,
        'network_info': network_info,
        'player_ids': player_ids,
        'game_start': game_start,
        'defense_start': defense_start,
        'battle_start': battle_start,
        'game_end': game_end,
        'captures': captures,
        'vuln_checks': vuln_checks,
        'injections': injections,
        'final_scores': final_scores,
        'turn_counts': dict(turn_counts),
        'command_samples': dict(command_samples),
    }


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def winner_of(final_scores):
    """Return (winner_id, winning_total) or (None, None) if no scores."""
    if not final_scores:
        return None, None
    best = None
    for pid, s in final_scores.items():
        total = s.get('total', 0) if isinstance(s, dict) else s
        if best is None or total > best[1]:
            best = (pid, total)
    return best if best else (None, None)


def duration_str(start, end):
    if not start or not end:
        return '?'
    seconds = max(0, (end - start).total_seconds())
    mins, secs = divmod(int(seconds), 60)
    return f'{mins}m {secs}s'


def game_mode_label(config, agents):
    if config.get('selfPlay') or all(a.startswith('bot-') for a in agents if a):
        if config.get('defenseTraining'):
            return 'Defense Training (cheat-mode attackers, self-play)'
        return 'Self-Play (6 custom-bot copies)'
    if config.get('cloudMode'):
        if 'custom-bot' in agents:
            return 'Cloud + Custom Bot (6 competitors)'
        return 'Cloud Only (5 cloud models)'
    if config.get('localOnly'):
        return 'Local Only (open-source models)'
    if config.get('segmented'):
        return 'Segmented (DMZ + internal)'
    return 'Standard CTF'


def narrative_summary(data):
    """Produce a 2-3 sentence plain-English summary of what happened."""
    caps = data['captures']
    scores = data['final_scores']
    winner_id, winner_pts = winner_of(scores)
    duration = duration_str(data['game_start'], data['game_end'])

    if not caps:
        return (f"The game lasted {duration}. No flags were captured — "
                f"every defender successfully protected their machine.")

    main_caps = [c for c in caps if not c['is_bonus']]
    bonus_caps = [c for c in caps if c['is_bonus']]
    first_cap = main_caps[0] if main_caps else caps[0]

    lead = (f"The game lasted {duration}. "
            f"{friendly(winner_id)} won with {winner_pts} points")

    def first_cap_line():
        mins_in = ''
        if first_cap['ts'] and data['game_start']:
            s = int((first_cap['ts'] - data['game_start']).total_seconds())
            m, sec = divmod(s, 60)
            mins_in = f" at {m}m{sec}s"
        return (f"The first flag capture was by {friendly(first_cap['attacker'])}, "
                f"who took {friendly(first_cap['victim'])}'s flag{mins_in}.")

    totals = (f"Across the game, {len(main_caps)} main flag(s) and "
              f"{len(bonus_caps)} bonus flag(s) were captured.")

    return f"{lead}. {first_cap_line()} {totals}"


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

HTML_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 1100px; margin: 0 auto; padding: 40px 20px; color: #222;
       background: #FAFBFC; line-height: 1.5; }
h1 { font-size: 2em; margin: 0 0 0.2em; color: #1a1a1a; }
h2 { font-size: 1.4em; margin-top: 2em; margin-bottom: 0.5em; color: #333;
     border-bottom: 2px solid #e0e0e0; padding-bottom: 0.3em; }
.subtitle { color: #666; margin-bottom: 2em; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
             gap: 16px; margin: 1em 0 2em; }
.stat-box { background: white; padding: 16px; border-radius: 8px;
            border: 1px solid #e5e7eb; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
.stat-label { font-size: 0.8em; color: #6b7280; text-transform: uppercase;
              letter-spacing: 0.05em; }
.stat-value { font-size: 1.5em; font-weight: 600; margin-top: 4px; color: #111827; }
.narrative { background: #F3F4F6; padding: 16px 20px; border-left: 4px solid #4A90D9;
             border-radius: 4px; font-size: 1.05em; margin: 1em 0; }
table { width: 100%; border-collapse: collapse; margin: 1em 0;
        background: white; border-radius: 8px; overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
th { background: #F9FAFB; padding: 12px; text-align: left; font-weight: 600;
     border-bottom: 1px solid #E5E7EB; color: #374151; font-size: 0.9em; }
td { padding: 12px; border-bottom: 1px solid #F3F4F6; }
tr:last-child td { border-bottom: none; }
.rank { font-weight: 700; font-size: 1.1em; width: 40px; text-align: center; }
.rank-1 { color: #F59E0B; } .rank-2 { color: #9CA3AF; } .rank-3 { color: #B45309; }
.color-dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%;
             margin-right: 8px; vertical-align: middle; }
.timeline { position: relative; margin: 1em 0; background: white;
            border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; overflow-x: auto; }
.timeline-svg { min-width: 700px; display: block; }
.timeline-legend { display: flex; flex-wrap: wrap; gap: 18px; padding: 0 8px 10px;
                   font-size: 0.85em; color: #4B5563; align-items: center; }
.timeline-legend .lg-dot { display: inline-block; width: 10px; height: 10px;
                           border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.timeline-legend .lg-diamond { display: inline-block; width: 10px; height: 10px;
                               transform: rotate(45deg); margin-right: 8px; margin-left: 2px;
                               vertical-align: middle; }
.timeline-legend .lg-tri { display: inline-block; width: 0; height: 0;
                           border-left: 5px solid transparent; border-right: 5px solid transparent;
                           border-bottom: 9px solid #EF4444; margin-right: 6px;
                           vertical-align: middle; }
.timeline-caption { font-size: 0.85em; color: #4B5563; padding: 10px 12px 4px;
                    line-height: 1.55; border-top: 1px solid #F3F4F6; margin-top: 8px; }
.timeline-caption strong { color: #111827; }
.capture-list { list-style: none; padding: 0; }
.capture-item { padding: 10px 14px; margin-bottom: 6px; background: white;
                border-left: 4px solid #4A90D9; border-radius: 4px;
                box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
.capture-item .cap-badge { display: inline-block; margin-left: 6px; padding: 1px 8px;
                           border-radius: 10px; font-size: 0.75em; font-weight: 600; }
.cap-badge-bonus { background: #F3E8FF; color: #6B21A8; }
.cap-badge-first { background: #FEF3C7; color: #92400E; }
.cap-badge-sub { background: #E5E7EB; color: #4B5563; }
.note-caption { font-size: 0.85em; color: #4B5563; background: #F9FAFB;
                padding: 10px 14px; border-left: 3px solid #9CA3AF;
                border-radius: 4px; margin-top: 12px; line-height: 1.55; }
.note-caption strong { color: #111827; }
.note-caption em { color: #374151; }
.capture-time { color: #9CA3AF; font-family: monospace; font-size: 0.9em; }
.capture-arrow { color: #6B7280; margin: 0 8px; }
.player-card { background: white; border: 1px solid #E5E7EB; border-radius: 8px;
               padding: 16px; margin-bottom: 12px; }
.player-header { display: flex; align-items: center; font-weight: 600;
                 font-size: 1.1em; margin-bottom: 8px; }
.player-metrics { display: flex; gap: 24px; font-size: 0.9em; color: #4B5563;
                  margin-bottom: 10px; }
.player-metrics strong { color: #111827; font-weight: 600; }
.cmd-samples { font-family: ui-monospace, monospace; font-size: 0.85em;
               color: #4B5563; background: #F9FAFB; padding: 8px 12px;
               border-radius: 4px; white-space: pre-wrap; word-break: break-all; }
.action-group { margin-top: 10px; }
.action-label { font-size: 0.85em; color: #6B7280; text-transform: uppercase;
                letter-spacing: 0.03em; margin-bottom: 4px; font-weight: 600; }
.action-list { list-style: none; padding: 0; margin: 0; }
.action-list li { padding: 6px 12px; background: #F9FAFB; margin-bottom: 4px;
                  border-radius: 4px; font-size: 0.95em; display: flex;
                  justify-content: space-between; align-items: center; }
.raw-cmd { display: inline-block; margin-left: 10px; color: #9CA3AF;
           cursor: help; font-size: 0.9em; }
.raw-cmd:hover { color: #4A90D9; }
.back-link { display: inline-block; margin-bottom: 1em; color: #4A90D9;
             text-decoration: none; }
.back-link:hover { text-decoration: underline; }
.mode-badge { display: inline-block; padding: 4px 12px; border-radius: 16px;
              background: #DBEAFE; color: #1E40AF; font-size: 0.85em;
              margin-bottom: 1em; }
.empty-state { color: #6B7280; font-style: italic; padding: 20px;
               background: white; border-radius: 8px; text-align: center; }
"""


def render_timeline_svg(data):
    """Render a horizontal SVG timeline of major events.
    Returns (legend_html, svg_string) so the legend can be rendered as HTML
    above the chart, separate from the SVG phase labels."""
    start = data['game_start']
    end = data['game_end']
    if not start or not end:
        return '', ''
    total_seconds = max(1, (end - start).total_seconds())

    width = 900
    height = 90
    margin_left = 60
    margin_right = 20
    chart_w = width - margin_left - margin_right
    # Push the axis down a bit so phase labels above it don't collide with anything.
    center_y = 45

    def x_pos(ts):
        if not ts:
            return margin_left
        secs = max(0, (ts - start).total_seconds())
        return margin_left + (secs / total_seconds) * chart_w

    # HTML legend (above SVG) — flex row that can't collide with SVG text.
    # The dot/diamond markers are colored by the ATTACKER, matching the
    # player colors shown in the Final Scoreboard and "What Each Player Did"
    # sections. Every capture event produces one marker — so if two attackers
    # read the same flag, you'll see two dots. This is why the number of
    # markers can exceed the number of unique flags captured.
    legend_html = (
        '<div class="timeline-legend">'
        '<span><span class="lg-dot" style="background:#9CA3AF"></span>Flag captured (colored by attacker)</span>'
        '<span><span class="lg-diamond" style="background:#9CA3AF"></span>Bonus flag captured (colored by attacker)</span>'
        '<span><span class="lg-tri" style="border-bottom-color:#EF4444"></span>Vulnerability injected</span>'
        '</div>'
    )

    # Build the SVG parts
    parts = [f'<svg class="timeline-svg" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">']
    # Base axis
    parts.append(f'<line x1="{margin_left}" y1="{center_y}" x2="{width - margin_right}" y2="{center_y}" '
                 f'stroke="#D1D5DB" stroke-width="2"/>')
    # Phase backgrounds + labels (labels sit just above the axis, no legend to collide with)
    if data['defense_start'] and data['battle_start']:
        def_x1 = x_pos(data['defense_start'])
        def_x2 = x_pos(data['battle_start'])
        parts.append(f'<rect x="{def_x1}" y="{center_y - 18}" width="{def_x2 - def_x1}" '
                     f'height="36" fill="#E0F2FE" opacity="0.6"/>')
        parts.append(f'<text x="{(def_x1 + def_x2)/2}" y="{center_y - 22}" '
                     f'text-anchor="middle" font-size="11" fill="#0369A1">Defense Phase</text>')
    if data['battle_start'] and end:
        bat_x1 = x_pos(data['battle_start'])
        bat_x2 = x_pos(end)
        parts.append(f'<rect x="{bat_x1}" y="{center_y - 18}" width="{bat_x2 - bat_x1}" '
                     f'height="36" fill="#FEF3C7" opacity="0.5"/>')
        parts.append(f'<text x="{(bat_x1 + bat_x2)/2}" y="{center_y - 22}" '
                     f'text-anchor="middle" font-size="11" fill="#92400E">Battle Phase</text>')

    # Capture events — circles on the axis. Multiple captures can happen at
    # the same timestamp (e.g. a single attacker reading two flags at once);
    # if two dots would overlap horizontally, stack them vertically so every
    # capture is visible. Overlap threshold: 2 * max_radius pixels.
    max_radius = 7
    overlap_threshold = max_radius * 2
    # Sort by x to detect clusters in order
    capture_list = []
    for cap in data['captures']:
        capture_list.append((x_pos(cap['ts']), cap))
    capture_list.sort(key=lambda p: p[0])

    # Group captures that fall within the overlap window
    groups = []
    for cx, cap in capture_list:
        if groups and cx - groups[-1][-1][0] < overlap_threshold:
            groups[-1].append((cx, cap))
        else:
            groups.append([(cx, cap)])

    for group in groups:
        n = len(group)
        for idx, (cx, cap) in enumerate(group):
            attacker_color = color_for(cap['attacker'])
            radius = 7 if cap['is_first'] else 5
            # Vertical offset: stack alternately above/below the axis when
            # multiple markers share the same x position.
            if n == 1:
                cy = center_y
            else:
                # Spread them in a small vertical stack centered on the axis.
                # Max stack height ~24px fits inside the chart band (40px tall).
                spacing = 12
                offset = (idx - (n - 1) / 2) * spacing
                cy = center_y + offset
            if cap['is_bonus']:
                parts.append(f'<rect x="{cx - radius}" y="{cy - radius}" '
                             f'width="{2*radius}" height="{2*radius}" fill="{attacker_color}" '
                             f'stroke="white" stroke-width="1.5" transform="rotate(45 {cx} {cy})"/>')
            else:
                parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" '
                             f'fill="{attacker_color}" stroke="white" stroke-width="1.5"/>')

    # Injection events — small red triangles below axis
    for inj in data['injections']:
        ix = x_pos(inj['ts'])
        parts.append(f'<polygon points="{ix},{center_y + 8} {ix - 4},{center_y + 16} '
                     f'{ix + 4},{center_y + 16}" fill="#EF4444"/>')

    # Time labels
    labels = [
        (margin_left, '0:00'),
        (margin_left + chart_w, duration_str(start, end)),
    ]
    if data['battle_start']:
        mid_x = x_pos(data['battle_start'])
        if abs(mid_x - margin_left) > 30 and abs(mid_x - (margin_left + chart_w)) > 30:
            elapsed = int((data['battle_start'] - start).total_seconds())
            m, s = divmod(elapsed, 60)
            labels.append((mid_x, f'{m}:{s:02d}'))
    for lx, ltext in labels:
        parts.append(f'<text x="{lx}" y="{height - 5}" text-anchor="middle" '
                     f'font-size="10" fill="#6B7280">{html.escape(ltext)}</text>')

    parts.append('</svg>')
    return legend_html, ''.join(parts)


def render_session_html(data):
    name = data['session_name']
    # Format session timestamp as MM/DD/YYYY HH:mm:SS UTC.
    # Session names look like "session-2026-04-18T01-34-40-324Z" — parse the
    # components and render a clean date.
    m = re.match(r'session-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})', name)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        pretty_date = f'{mo}/{d}/{y} {hh}:{mm}:{ss} UTC'
    else:
        pretty_date = name.replace('session-', '')

    winner_id, winner_pts = winner_of(data['final_scores'])
    mode = game_mode_label(data['config'], data['player_ids'])
    duration = duration_str(data['game_start'], data['game_end'])

    # Rank players by score
    ranked = []
    for pid, s in data['final_scores'].items():
        total = s.get('total', 0) if isinstance(s, dict) else s
        caps = s.get('flagsCaptured', 0) if isinstance(s, dict) else 0
        lost = s.get('flagsLost', 0) if isinstance(s, dict) else 0
        bonus = s.get('bonusesCaptured', 0) if isinstance(s, dict) else 0
        ranked.append((pid, total, caps, lost, bonus))
    ranked.sort(key=lambda r: -r[1])

    # Build scoreboard table
    score_rows = []
    for idx, (pid, total, caps, lost, bonus) in enumerate(ranked):
        rank = idx + 1
        medal = '🥇' if rank == 1 else '🥈' if rank == 2 else '🥉' if rank == 3 else str(rank)
        dot = f'<span class="color-dot" style="background:{color_for(pid)}"></span>'
        score_rows.append(
            f'<tr><td class="rank rank-{min(rank, 3)}">{medal}</td>'
            f'<td>{dot}<strong>{html.escape(friendly(pid))}</strong></td>'
            f'<td><strong>{total}</strong></td><td>{caps}</td><td>{lost}</td><td>{bonus}</td></tr>'
        )
    if score_rows:
        # Check whether captures total differs from losses total — if so add a note.
        total_caps_in_scores = sum(r[2] for r in ranked)
        total_lost_in_scores = sum(r[3] for r in ranked)
        note_html = ''
        if total_caps_in_scores != total_lost_in_scores:
            note_html = (
                '<p class="note-caption">'
                '<strong>Why "Captures" total doesn\'t match "Lost" total:</strong> '
                f'Players captured {total_caps_in_scores} flags in this game, but only '
                f'{total_lost_in_scores} flags were actually lost. The same flag can be '
                'captured by multiple attackers — the first attacker to reach it increments '
                'both their "Captures" column <em>and</em> the victim\'s "Lost" column. '
                'Every subsequent attacker who also reads that flag gets credit in their '
                '"Captures" column (for a smaller point value), but the victim\'s "Lost" '
                'count stays at 1 because their flag was only truly lost once.'
                '</p>'
            )
        score_table = (
            '<table><tr><th></th><th>Player</th><th>Score</th><th>Captures</th>'
            '<th>Lost</th><th>Bonuses</th></tr>' + ''.join(score_rows) + '</table>' + note_html
        )
    else:
        score_table = '<div class="empty-state">No final scores recorded.</div>'

    # Captures list — color border matches the attacker's player color
    if data['captures']:
        cap_items = []
        start = data['game_start']
        for cap in data['captures']:
            mins = sec = 0
            if cap['ts'] and start:
                elapsed = int((cap['ts'] - start).total_seconds())
                mins, sec = divmod(elapsed, 60)
            attacker_color = color_for(cap['attacker'])
            victim_color = color_for(cap['victim'])
            # Badge tells the reader whether this was a first or subsequent capture,
            # which matters because the dot count and "Lost" column depend on it.
            if cap['is_bonus']:
                tier_label = f' ({cap["tier"]})' if cap.get('tier') else ''
                badge = f'<span class="cap-badge cap-badge-bonus">bonus flag{tier_label}</span>'
            elif cap['is_first']:
                badge = '<span class="cap-badge cap-badge-first">first to capture this flag</span>'
            else:
                badge = '<span class="cap-badge cap-badge-sub">subsequent capture (flag already taken)</span>'
            cap_items.append(
                f'<li class="capture-item" style="border-left-color:{attacker_color}">'
                f'<span class="capture-time">{mins:02d}:{sec:02d}</span> '
                f'<span class="color-dot" style="background:{attacker_color}"></span>'
                f'<strong>{html.escape(friendly(cap["attacker"]))}</strong>'
                f'<span class="capture-arrow">→</span>'
                f'<span class="color-dot" style="background:{victim_color}"></span>'
                f'<strong>{html.escape(friendly(cap["victim"]))}</strong>'
                f' &nbsp;<span style="color:#6B7280">+{cap["points"]} pts</span>{badge}</li>'
            )
        captures_html = '<ul class="capture-list">' + ''.join(cap_items) + '</ul>'
    else:
        captures_html = '<div class="empty-state">No flags were captured this game.</div>'

    # Per-player cards — translate commands to plain-English descriptions
    player_cards = []
    for pid in sorted(data['player_ids']):
        counts = data['turn_counts'].get(pid, {'attacker': 0, 'defender': 0})
        samples = data['command_samples'].get(pid, {'attacker': [], 'defender': []})

        # Translate attacks: dedupe descriptions so we show variety
        atk_descriptions = []
        seen = set()
        for cmd in samples['attacker']:
            desc = describe_command(cmd, 'attacker')
            if desc not in seen:
                seen.add(desc)
                atk_descriptions.append((desc, cmd))
            if len(atk_descriptions) >= 3:
                break

        def_descriptions = []
        seen = set()
        for cmd in samples['defender']:
            desc = describe_command(cmd, 'defender')
            if desc not in seen:
                seen.add(desc)
                def_descriptions.append((desc, cmd))
            if len(def_descriptions) >= 3:
                break

        action_block = ''
        if atk_descriptions or def_descriptions:
            parts_list = []
            if atk_descriptions:
                parts_list.append('<div class="action-group"><div class="action-label">What they tried as attacker:</div><ul class="action-list">')
                for desc, raw in atk_descriptions:
                    parts_list.append(
                        f'<li>{html.escape(desc)}'
                        f'<span class="raw-cmd" title="{html.escape(raw)}">ⓘ</span></li>'
                    )
                parts_list.append('</ul></div>')
            if def_descriptions:
                parts_list.append('<div class="action-group"><div class="action-label">What they did as defender:</div><ul class="action-list">')
                for desc, raw in def_descriptions:
                    parts_list.append(
                        f'<li>{html.escape(desc)}'
                        f'<span class="raw-cmd" title="{html.escape(raw)}">ⓘ</span></li>'
                    )
                parts_list.append('</ul></div>')
            action_block = ''.join(parts_list)

        player_cards.append(
            f'<div class="player-card">'
            f'<div class="player-header">'
            f'<span class="color-dot" style="background:{color_for(pid)}"></span>'
            f'{html.escape(friendly(pid))}</div>'
            f'<div class="player-metrics">'
            f'<span><strong>{counts["attacker"]}</strong> attacker turns</span>'
            f'<span><strong>{counts["defender"]}</strong> defender turns</span>'
            f'</div>{action_block}</div>'
        )

    narrative = narrative_summary(data)
    timeline_legend, timeline_svg = render_timeline_svg(data)

    # Describe actual phase durations measured from this session's timestamps.
    # Expected durations come from the game config (defensePhaseMinutes,
    # battlePhaseMinutes). Actual durations often differ because:
    #   - Defense phase: includes agent-initialization and container-start time
    #     before the first defender turn can fire.
    #   - Battle phase: extends a few seconds past the deadline when an agent
    #     is mid-turn (the engine waits for the current turn to finish).
    #   - Battle phase: cuts short when all-but-one flag has been captured
    #     (early game-over trigger).
    def _fmt_dur(seconds):
        seconds = int(round(seconds))
        m, s = divmod(seconds, 60)
        if m == 0:
            return f"{s} sec"
        if s == 0:
            return f"{m} min"
        return f"{m} min {s} sec"

    def_dur = None
    if data['defense_start'] and data['battle_start']:
        def_dur = (data['battle_start'] - data['defense_start']).total_seconds()
    bat_dur = None
    if data['battle_start'] and data['game_end']:
        bat_dur = (data['game_end'] - data['battle_start']).total_seconds()

    # Expected durations from the session's own config (falls back to defaults)
    cfg = data['config']
    expected_def_sec = int(round(float(cfg.get('defensePhaseMinutes', 0.5)) * 60))
    expected_bat_sec = int(round(float(cfg.get('battlePhaseMinutes', 5)) * 60))

    def _diff_note(actual, expected):
        if actual is None:
            return ''
        delta = actual - expected
        if abs(delta) <= 2:
            return ' (matches the target)'
        if delta > 0:
            return f' ({_fmt_dur(delta)} longer than the {_fmt_dur(expected)} target)'
        return f' ({_fmt_dur(-delta)} shorter than the {_fmt_dur(expected)} target)'

    caption_parts = []
    if def_dur is not None:
        caption_parts.append(
            f"<strong>Defense phase (blue):</strong> {_fmt_dur(def_dur)} — only defenders are active"
            f"{_diff_note(def_dur, expected_def_sec)}.")
    if bat_dur is not None:
        caption_parts.append(
            f"<strong>Battle phase (yellow):</strong> {_fmt_dur(bat_dur)} — all players active"
            f"{_diff_note(bat_dur, expected_bat_sec)}.")
    caption_parts.append(
        "<strong>Why phases don't match the target exactly:</strong> the defense phase includes time "
        "for the AI agents and containers to initialize before the first command runs; the battle "
        "phase extends while any agent finishes a turn that started before the deadline; and the "
        "battle phase can end early once all but one flag has been captured."
    )
    # Count markers and unique flag events so we can describe them in the caption
    num_capture_markers = len(data['captures'])
    unique_flags_taken = len(set((c['victim'], c['is_bonus']) for c in data['captures']))
    if num_capture_markers and num_capture_markers != unique_flags_taken:
        caption_parts.append(
            "<strong>Why you may see more dots than flags captured:</strong> each colored marker is one "
            "capture event. A single flag can be read by multiple attackers during a game — the first "
            "attacker to read it gets full points and the defender's flag is marked lost once; every "
            f"attacker after that still produces a marker (for partial points). In this game there were "
            f"{num_capture_markers} capture events on {unique_flags_taken} distinct flags."
        )
    else:
        caption_parts.append(
            "Each colored marker on the timeline is one capture event, colored to match the attacker "
            "in the scoreboard above. A single flag can generate multiple markers if more than one "
            "attacker reads it."
        )
    timeline_caption = ' '.join(caption_parts)

    # Total stats cards
    total_caps = sum(1 for c in data['captures'] if not c['is_bonus'])
    total_bonus = sum(1 for c in data['captures'] if c['is_bonus'])
    total_injections = len(data['injections'])

    html_body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Game Session — {html.escape(pretty_date)}</title>
<style>{HTML_CSS}</style></head><body>
<a class="back-link" href="index.html">← All sessions</a>
<div class="mode-badge">{html.escape(mode)}</div>
<h1>CTF Game Session</h1>
<div class="subtitle">{html.escape(pretty_date)} &middot; Duration: {duration}</div>

<div class="narrative">{html.escape(narrative)}</div>

<div class="stat-grid">
  <div class="stat-box"><div class="stat-label">Winner</div>
    <div class="stat-value">{html.escape(friendly(winner_id)) if winner_id else '—'}</div></div>
  <div class="stat-box"><div class="stat-label">Winning Score</div>
    <div class="stat-value">{winner_pts if winner_pts is not None else '—'}</div></div>
  <div class="stat-box"><div class="stat-label">Flags Captured</div>
    <div class="stat-value">{total_caps}</div></div>
  <div class="stat-box"><div class="stat-label">Bonus Flags</div>
    <div class="stat-value">{total_bonus}</div></div>
  <div class="stat-box"><div class="stat-label">Vulnerability Injections</div>
    <div class="stat-value">{total_injections}</div></div>
</div>

<h2>Final Scoreboard</h2>
{score_table}

<h2>Game Timeline</h2>
<div class="timeline">
  {timeline_legend}
  {timeline_svg}
  <div class="timeline-caption">{timeline_caption}</div>
</div>

<h2>Flag Captures</h2>
{captures_html}

<h2>What Each Player Did</h2>
{''.join(player_cards) if player_cards else '<div class="empty-state">No player data.</div>'}

<p style="color:#9CA3AF; margin-top:3em; font-size:0.85em; text-align:center;">
Generated from <code>{html.escape(name)}</code> — raw game data available in the session directory.
</p>
</body></html>"""
    return html_body


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------

INDEX_CSS = HTML_CSS + """
.session-link { display: block; padding: 14px 18px; margin-bottom: 8px;
                background: white; border: 1px solid #E5E7EB; border-radius: 8px;
                text-decoration: none; color: #222; transition: all 0.15s; }
.session-link:hover { border-color: #4A90D9; box-shadow: 0 2px 8px rgba(74,144,217,0.15); }
.session-date { color: #6B7280; font-size: 0.9em; }
.session-winner { float: right; color: #059669; font-weight: 600; }
"""


def render_index_html(summaries):
    rows = []
    for s in summaries:
        winner_id, winner_pts = s['winner']
        date_str = s['session_name'].replace('session-', '').replace('T', ' ').replace('Z', '')[:19]
        viewer_file = s['session_name'] + '.html'
        winner_label = (f'<span class="session-winner">🏆 {html.escape(friendly(winner_id))} — {winner_pts} pts</span>'
                        if winner_id else '<span class="session-winner">—</span>')
        rows.append(
            f'<a class="session-link" href="{viewer_file}">'
            f'{winner_label}'
            f'<div><strong>{html.escape(s["mode"])}</strong> &middot; {html.escape(s["duration"])}'
            f'</div><div class="session-date">{html.escape(date_str)}</div></a>'
        )
    content = ''.join(rows) if rows else '<div class="empty-state">No sessions found.</div>'
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>CTF Game Sessions</title><style>{INDEX_CSS}</style></head><body>
<h1>CTF Game Sessions</h1>
<div class="subtitle">{len(summaries)} session{'s' if len(summaries) != 1 else ''} — click any row to see details.</div>
{content}
</body></html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_one(session_dir, output_dir):
    data = load_session(session_dir)
    if data is None:
        return None
    html_str = render_session_html(data)
    out_path = os.path.join(output_dir, data['session_name'] + '.html')
    with open(out_path, 'w') as f:
        f.write(html_str)

    # Summary for index
    winner = winner_of(data['final_scores'])
    return {
        'session_name': data['session_name'],
        'winner': winner,
        'mode': game_mode_label(data['config'], data['player_ids']),
        'duration': duration_str(data['game_start'], data['game_end']),
        'game_start': data['game_start'],
    }


def main():
    parser = argparse.ArgumentParser(description='Generate human-readable HTML for game sessions.')
    parser.add_argument('session', nargs='?', help='Path to a session directory')
    parser.add_argument('--all', action='store_true', help='Generate viewers for all sessions + index')
    parser.add_argument('--recent', type=int, default=0, help='With --all, only process the last N sessions')
    parser.add_argument('--output', default=DEFAULT_OUTPUT_DIR, help='Output directory (default: reports/viewers/)')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    if args.all:
        sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
        if args.recent > 0:
            sessions = sessions[-args.recent:]
        summaries = []
        print(f"Processing {len(sessions)} sessions → {args.output}/")
        for sd in sessions:
            result = generate_one(sd, args.output)
            if result:
                summaries.append(result)
        # Sort summaries newest first
        summaries.sort(key=lambda x: x['game_start'] or datetime.min, reverse=True)
        index_html = render_index_html(summaries)
        index_path = os.path.join(args.output, 'index.html')
        with open(index_path, 'w') as f:
            f.write(index_html)
        print(f"Generated {len(summaries)} session pages + index.html")
        print(f"Open: {index_path}")
        return

    if not args.session:
        parser.print_help()
        sys.exit(1)

    result = generate_one(args.session, args.output)
    if result is None:
        print(f"Could not load session from {args.session}", file=sys.stderr)
        sys.exit(1)
    out_path = os.path.join(args.output, result['session_name'] + '.html')
    print(f"Generated: {out_path}")


if __name__ == '__main__':
    main()
