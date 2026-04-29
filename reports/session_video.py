#!/usr/bin/env python3
"""Render a CTF game session as a short animated MP4.

The video is a 30-fps replay of one session: a timeline cursor sweeps
left-to-right while a live scoreboard updates and capture events pop into
view. The whole game is compressed to ~25-40 seconds so a non-technical
viewer can watch the whole match at a glance.

Requires ffmpeg in PATH (matplotlib's MovieWriter shells out to it).

Usage:
  python3 reports/session_video.py <session-dir>
  python3 reports/session_video.py <session-dir> --output mygame.mp4
  python3 reports/session_video.py --all                # all sessions
  python3 reports/session_video.py <session-dir> --duration 45  # video length
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.patches as patches
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, '..', 'logs')
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'videos')

# Match the colors / friendly names used by the HTML viewer for consistency.
MODEL_FRIENDLY = {
    'custom-bot': 'Custom Bot',
    'gpt-oss': 'GPT-OSS 120B',
    'glm5': 'GLM-5.1',
    'gemini3': 'Gemini 3 Flash',
    'nemotron': 'Nemotron 3 Super',
    'rnj': 'RNJ-1 8B',
    'bot-alpha': 'Bot Alpha',
    'bot-bravo': 'Bot Bravo',
    'bot-charlie': 'Bot Charlie',
    'bot-delta': 'Bot Delta',
    'bot-echo': 'Bot Echo',
    'bot-foxtrot': 'Bot Foxtrot',
}
MODEL_COLORS = {
    'custom-bot': '#14B8A6',
    'gpt-oss':    '#4A90D9',
    'glm5':       '#9B59B6',
    'gemini3':    '#50C878',
    'nemotron':   '#F5A623',
    'rnj':        '#E85D4A',
}


def friendly(pid): return MODEL_FRIENDLY.get(pid, pid)
def color_for(pid): return MODEL_COLORS.get(pid, '#888888')


def parse_ts(ts):
    if not ts: return None
    try: return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except (ValueError, AttributeError): return None


def load_session(session_dir):
    """Pull the events we need to animate the session."""
    gf = os.path.join(session_dir, 'game.json')
    if not os.path.exists(gf):
        return None
    try:
        events = json.load(open(gf))
    except (json.JSONDecodeError, OSError):
        return None

    game_start = defense_start = battle_start = game_end = None
    for e in events:
        msg = e.get('message', '')
        ts = parse_ts(e.get('timestamp'))
        if msg.startswith('Game started') and game_start is None: game_start = ts
        if msg.startswith('Defense phase started') and defense_start is None: defense_start = ts
        if msg.startswith('Battle phase started') and battle_start is None: battle_start = ts
        if msg.startswith('Battle phase ended'): game_end = ts
    if game_start is None:
        for e in events:
            if (ts := parse_ts(e.get('timestamp'))): game_start = ts; break
    if game_end is None:
        for e in reversed(events):
            if (ts := parse_ts(e.get('timestamp'))): game_end = ts; break

    # Players from networkInfo or from agent fields
    network_info = next((e['networkInfo'] for e in events if isinstance(e.get('networkInfo'), dict)), {})
    player_ids = list(network_info.keys()) or sorted(set(e.get('agent', '') for e in events if e.get('agent')))

    captures = []
    for e in events:
        msg = e.get('message', '')
        if 'CAPTURED' not in msg:
            continue
        atk = e.get('attacker'); vic = e.get('victim')
        if not atk or atk == vic:
            continue
        is_bonus = 'BONUS CAPTURED' in msg or bool(e.get('bonus'))
        captures.append({
            'ts': parse_ts(e.get('timestamp')),
            'attacker': atk, 'victim': vic,
            'points': e.get('pointsAwarded', 0),
            'is_bonus': is_bonus,
            'is_first': bool(e.get('isFirstCapture')),
        })

    # Vulnerability injections
    injections = []
    for e in events:
        msg = e.get('message', '')
        if 'Vulnerability injected' in msg or 'VULNERABILITY INJECTED' in msg:
            injections.append({'ts': parse_ts(e.get('timestamp')),
                               'victim': e.get('player') or e.get('victim', '')})

    # Detect mode for caption
    config = next((e['config'] for e in events if isinstance(e.get('config'), dict)), {})

    return {
        'session_name': os.path.basename(session_dir),
        'game_start': game_start, 'defense_start': defense_start,
        'battle_start': battle_start, 'game_end': game_end,
        'captures': captures, 'injections': injections,
        'player_ids': player_ids, 'config': config,
    }


def render_video(data, output_path, duration_sec=30, fps=30):
    """Write the session animation to output_path. Returns success bool."""
    start = data['game_start']; end = data['game_end']
    if not (start and end):
        print(f'  Cannot render: missing start/end timestamps', file=sys.stderr)
        return False

    total_real_seconds = max(1.0, (end - start).total_seconds())
    total_frames = int(duration_sec * fps)
    real_per_frame = total_real_seconds / total_frames

    players = data['player_ids']
    if not players:
        print('  Cannot render: no players found', file=sys.stderr)
        return False

    # Sort players to match scoreboard layout (alphabetical, custom-bot first if present)
    players = sorted(players, key=lambda p: (0 if p == 'custom-bot' else 1, p))

    # Pre-compute event times in real-seconds-from-start
    cap_seconds = [(c, max(0, (c['ts'] - start).total_seconds())) for c in data['captures'] if c['ts']]
    inj_seconds = [(i, max(0, (i['ts'] - start).total_seconds())) for i in data['injections'] if i['ts']]

    # Build the figure layout: title strip, scoreboard, timeline, event log
    fig = plt.figure(figsize=(12, 7), dpi=120)
    fig.patch.set_facecolor('#FAFBFC')
    gs = fig.add_gridspec(3, 2, height_ratios=[0.6, 3, 1.3], width_ratios=[3, 1.5],
                          hspace=0.45, wspace=0.25, left=0.06, right=0.97, top=0.93, bottom=0.07)
    ax_title = fig.add_subplot(gs[0, :]); ax_title.axis('off')
    ax_score = fig.add_subplot(gs[1, 0])
    ax_log = fig.add_subplot(gs[1, 1]); ax_log.axis('off')
    ax_time = fig.add_subplot(gs[2, :])

    # Title
    pretty_date = re.sub(r'^session-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2}).*',
                         r'\2/\3/\1 \4:\5:\6 UTC', data['session_name'])
    title_txt = ax_title.text(0.5, 0.7, 'CTF Game Session', ha='center', va='center',
                              fontsize=20, fontweight='bold')
    ax_title.text(0.5, 0.18, pretty_date, ha='center', va='center', fontsize=11, color='#666')

    # Scoreboard initial layout
    ax_score.set_title('Live Scoreboard', fontsize=13, fontweight='bold', loc='left')
    ax_score.set_xlim(0, 1)
    ax_score.set_ylim(-0.5, len(players) - 0.5)
    ax_score.invert_yaxis()
    ax_score.spines['top'].set_visible(False)
    ax_score.spines['right'].set_visible(False)
    ax_score.spines['left'].set_visible(False)
    ax_score.set_yticks([])
    ax_score.set_xticks([])
    bar_artists = []
    name_artists = []
    score_artists = []
    cap_artists = []
    for i, p in enumerate(players):
        c = color_for(p)
        # Filled bar (scaled later)
        bar = ax_score.barh(i, 0, color=c, height=0.55, edgecolor='#333', linewidth=0.6)[0]
        bar_artists.append(bar)
        name_artists.append(ax_score.text(0.01, i - 0.32, friendly(p), fontsize=10,
                                          fontweight='bold', va='center'))
        score_artists.append(ax_score.text(0.99, i, '0 pts', fontsize=10, ha='right', va='center',
                                            color='#333'))
        cap_artists.append(ax_score.text(0.01, i + 0.32, '', fontsize=8, va='center', color='#666'))

    # Timeline
    ax_time.set_xlim(0, total_real_seconds); ax_time.set_ylim(0, 1)
    ax_time.set_yticks([]); ax_time.spines['top'].set_visible(False)
    ax_time.spines['right'].set_visible(False); ax_time.spines['left'].set_visible(False)
    ax_time.set_title('Timeline', fontsize=12, fontweight='bold', loc='left')
    # Phase backgrounds
    if data['defense_start'] and data['battle_start']:
        d0 = (data['defense_start'] - start).total_seconds()
        d1 = (data['battle_start'] - start).total_seconds()
        ax_time.axvspan(d0, d1, color='#E0F2FE', alpha=0.6)
        ax_time.text((d0 + d1) / 2, 0.85, 'Defense', ha='center', fontsize=9, color='#0369A1')
    if data['battle_start']:
        b0 = (data['battle_start'] - start).total_seconds()
        ax_time.axvspan(b0, total_real_seconds, color='#FEF3C7', alpha=0.5)
        ax_time.text((b0 + total_real_seconds) / 2, 0.85, 'Battle', ha='center', fontsize=9, color='#92400E')
    # Time axis labels (only at end)
    ax_time.text(0, -0.18, '0:00', fontsize=9, ha='left', color='#666')
    end_label = f'{int(total_real_seconds)//60}:{int(total_real_seconds)%60:02d}'
    ax_time.text(total_real_seconds, -0.18, end_label, fontsize=9, ha='right', color='#666')
    # Cursor (vertical line that sweeps right)
    cursor_line = ax_time.axvline(0, color='#DC2626', linewidth=2.2, zorder=10)
    cursor_text = ax_time.text(0, 1.05, '0:00', ha='center', fontsize=9, color='#DC2626',
                               fontweight='bold')

    # Event log on the right
    ax_log.set_title('Recent Events', fontsize=13, fontweight='bold', loc='left')
    ax_log.set_xlim(0, 1); ax_log.set_ylim(0, 1)
    log_text = ax_log.text(0.02, 0.97, '', fontsize=9, va='top', family='monospace', color='#333')

    # Pre-place markers (initially invisible) to avoid creating artists per frame
    marker_artists = []
    for cap, sec in cap_seconds:
        c = color_for(cap['attacker'])
        if cap['is_bonus']:
            m = patches.Rectangle((sec - 6, 0.32), 12, 0.36, facecolor=c, edgecolor='white',
                                  linewidth=1.2, zorder=8)
            tr = matplotlib.transforms.Affine2D().rotate_deg_around(sec, 0.5, 45) + ax_time.transData
            m.set_transform(tr)
        else:
            m = patches.Circle((sec, 0.5), radius=6, facecolor=c, edgecolor='white',
                               linewidth=1.2, zorder=8, transform=ax_time.transData)
        m.set_visible(False)
        ax_time.add_patch(m)
        marker_artists.append((m, sec))
    inj_artists = []
    for inj, sec in inj_seconds:
        m = patches.Polygon([[sec, 0.18], [sec - 4, 0.06], [sec + 4, 0.06]], closed=True,
                            facecolor='#EF4444', zorder=7)
        m.set_visible(False)
        ax_time.add_patch(m)
        inj_artists.append((m, sec))

    # Score state — incremented when capture events fire.
    # Scoring rules: first capture +100, subsequent +25, bonuses use cap['points'].
    scores = {p: 0 for p in players}
    caps_so_far = {p: 0 for p in players}
    losses_so_far = {p: 0 for p in players}
    flag_taken = set()  # victim ids whose flag has been first-captured (for survival bonus tracking)

    # Pre-compute what events have happened by each frame so update_frame is cheap
    # and so the scores at any frame are deterministic.
    events_for_frame = []  # list of (frame_idx, type, payload)
    for cap, sec in cap_seconds:
        f = min(total_frames - 1, int(sec / real_per_frame))
        events_for_frame.append((f, 'cap', cap))
    for inj, sec in inj_seconds:
        f = min(total_frames - 1, int(sec / real_per_frame))
        events_for_frame.append((f, 'inj', inj))
    events_for_frame.sort(key=lambda x: x[0])

    # Recent log entries (last few)
    log_lines = []
    event_pointer = [0]  # mutable so closure can update

    def update(frame):
        # Apply any events that occur at or before this frame
        while event_pointer[0] < len(events_for_frame) and events_for_frame[event_pointer[0]][0] <= frame:
            f_idx, kind, payload = events_for_frame[event_pointer[0]]
            event_pointer[0] += 1
            if kind == 'cap':
                cap = payload
                pts = cap.get('points', 0)
                scores[cap['attacker']] = scores.get(cap['attacker'], 0) + pts
                caps_so_far[cap['attacker']] = caps_so_far.get(cap['attacker'], 0) + 1
                if not cap['is_bonus']:
                    losses_so_far[cap['victim']] = losses_so_far.get(cap['victim'], 0) + 1
                # Apply -25 to victim only on first non-bonus capture
                if not cap['is_bonus'] and cap['is_first']:
                    scores[cap['victim']] = scores.get(cap['victim'], 0) - 25
                # Reveal the marker
                for m, sec in marker_artists:
                    if abs(sec - (cap['ts'] - start).total_seconds()) < 0.01:
                        m.set_visible(True)
                # Log line
                tag = 'BONUS' if cap['is_bonus'] else 'FLAG'
                t_str = f"{int((cap['ts'] - start).total_seconds()) // 60}:" \
                        f"{int((cap['ts'] - start).total_seconds()) % 60:02d}"
                log_lines.append(f"{t_str} {tag} {friendly(cap['attacker'])[:14]} → "
                                 f"{friendly(cap['victim'])[:14]}")
            else:
                inj = payload
                for m, sec in inj_artists:
                    if abs(sec - (inj['ts'] - start).total_seconds()) < 0.01:
                        m.set_visible(True)
                t_str = f"{int((inj['ts'] - start).total_seconds()) // 60}:" \
                        f"{int((inj['ts'] - start).total_seconds()) % 60:02d}"
                log_lines.append(f"{t_str} VULN injected on {friendly(inj['victim'])[:14]}")

        # Update the cursor
        cur_real_sec = frame * real_per_frame
        cursor_line.set_xdata([cur_real_sec, cur_real_sec])
        cursor_text.set_x(cur_real_sec)
        cursor_text.set_text(f"{int(cur_real_sec) // 60}:{int(cur_real_sec) % 60:02d}")

        # Scoreboard rescaling — find max so bars fit
        max_score = max(max(scores.values(), default=0), 100)
        for i, p in enumerate(players):
            s = scores.get(p, 0)
            display_w = max(0, s) / max_score if max_score > 0 else 0
            bar_artists[i].set_width(display_w)
            score_artists[i].set_text(f"{s} pts")
            cap_artists[i].set_text(
                f"caps {caps_so_far.get(p,0)}  flag: {'lost' if losses_so_far.get(p,0) else 'safe'}"
            )

        # Recent events log (last 6)
        log_text.set_text('\n'.join(log_lines[-6:]) if log_lines else '(no events yet)')
        return []  # no blit

    # Render with ffmpeg writer
    if not animation.writers.is_available('ffmpeg'):
        print('  ffmpeg writer not available', file=sys.stderr)
        return False
    writer = animation.writers['ffmpeg'](fps=fps, bitrate=2400, codec='h264')
    anim = animation.FuncAnimation(fig, update, frames=total_frames, interval=1000/fps, blit=False)
    anim.save(output_path, writer=writer, dpi=120)
    plt.close(fig)
    return True


def main():
    parser = argparse.ArgumentParser(description='Render a CTF session as an MP4.')
    parser.add_argument('session', nargs='?', help='Path to a session directory')
    parser.add_argument('--all', action='store_true', help='Render every session')
    parser.add_argument('--recent', type=int, default=0,
                        help='With --all, render only the last N sessions')
    parser.add_argument('--output', help='Output file (single-session mode) or directory (--all)')
    parser.add_argument('--duration', type=float, default=30,
                        help='Video length in seconds (default 30)')
    parser.add_argument('--fps', type=int, default=30, help='Frames per second (default 30)')
    args = parser.parse_args()

    out_dir = args.output or DEFAULT_OUTPUT_DIR
    if args.all or not args.output:
        os.makedirs(out_dir, exist_ok=True)

    if args.all:
        sessions = sorted(glob.glob(os.path.join(LOGS_DIR, 'session-*')))
        if args.recent > 0:
            sessions = sessions[-args.recent:]
        print(f'Rendering {len(sessions)} sessions to {out_dir}/')
        rendered = 0
        for sd in sessions:
            data = load_session(sd)
            if not data:
                continue
            out_path = os.path.join(out_dir, data['session_name'] + '.mp4')
            print(f'  {data["session_name"]}...', end=' ', flush=True)
            ok = render_video(data, out_path, duration_sec=args.duration, fps=args.fps)
            print('OK' if ok else 'FAILED')
            if ok:
                rendered += 1
        print(f'Rendered {rendered} videos.')
        return

    if not args.session:
        parser.print_help()
        sys.exit(1)
    data = load_session(args.session)
    if not data:
        print(f'Could not load session from {args.session}', file=sys.stderr)
        sys.exit(1)
    out_path = args.output if args.output and args.output.endswith('.mp4') \
               else os.path.join(out_dir, data['session_name'] + '.mp4')
    if not args.output:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if render_video(data, out_path, duration_sec=args.duration, fps=args.fps):
        print(f'Generated: {out_path}')
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
