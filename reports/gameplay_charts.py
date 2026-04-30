#!/usr/bin/env python3.11
"""Generate the small set of gameplay charts the report references.

Reads logs/session-*/game.json files, extracts captures and final scores per
model, and produces the 4 charts cited in the Results section.
"""
import glob
import json
import os
from collections import Counter, defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, '..', 'logs')

# Cloud-only models (the gameplay sample)
CLOUD = ['gpt-oss', 'gemini3', 'glm5', 'nemotron', 'rnj']
ALL_MODELS = CLOUD + ['custom-bot']
PRETTY = {
    'gpt-oss':    'GPT-OSS 120B',
    'gemini3':    'Gemini 3 Flash',
    'glm5':       'GLM-5.1',
    'nemotron':   'Nemotron 3 Super',
    'rnj':        'RNJ-1 8B',
    'custom-bot': 'Custom Bot (3B)',
}
COLORS = {
    'gpt-oss':    '#4A90D9',
    'gemini3':    '#50C878',
    'glm5':       '#9B59B6',
    'nemotron':   '#F5A623',
    'rnj':        '#E85D4A',
    'custom-bot': '#14B8A6',
}
CHART_BG = '#FAFBFC'


def load_session_metrics():
    sessions = sorted(glob.glob(os.path.join(LOGS, 'session-*')))
    # Separate by era: cloud-only sessions (no custom-bot) vs with-custom sessions
    cloud_scores = defaultdict(list); cloud_caps = defaultdict(list); cloud_lost = defaultdict(list)
    all_scores  = defaultdict(list); all_caps  = defaultdict(list); all_lost  = defaultdict(list)
    attack_method_counts = Counter()

    METHOD_BUCKETS = [
        ('Web requests',      ('curl', 'wget', 'http', 'nikto', 'gobuster')),
        ('Login attempts',    ('hydra', 'ssh ', 'sshpass', 'medusa')),
        ('Network scans',     ('nmap', 'masscan', 'rustscan')),
        ('File system probes', ('find ', 'ls ', 'cat ', 'cd ')),
        ('Exploitation',      ('python -c', 'bash -c', 'perl -e', 'msfconsole')),
        ('Credential dump',   ('shadow', 'passwd', 'mysql', 'redis-cli')),
    ]

    for sd in sessions:
        gf = os.path.join(sd, 'game.json')
        if not os.path.exists(gf):
            continue
        try:
            events = json.load(open(gf))
        except Exception:
            continue

        # Find final scoreboard
        final_scores = None
        for e in reversed(events):
            if e.get('type') == 'scoreboard' or 'scores' in e:
                if isinstance(e.get('scores'), dict):
                    final_scores = e['scores']
                    break

        # Captures and losses (per model in this session)
        caps = Counter(); lost = Counter()
        for e in events:
            msg = e.get('message', '')
            if 'CAPTURED' in msg and 'BONUS' not in msg:
                a = e.get('attacker'); v = e.get('victim')
                if a and v and a != v:
                    caps[a] += 1
                    lost[v] += 1
            cmd = e.get('command') or e.get('action') or ''
            if cmd:
                low = cmd.lower()
                for label, kws in METHOD_BUCKETS:
                    if any(kw in low for kw in kws):
                        attack_method_counts[label] += 1
                        break

        # Determine era: was custom-bot in this session?
        players_in_session = set(final_scores.keys()) if final_scores else set()
        all_session_agents = {e.get('agent') for e in events if e.get('agent')}
        has_custom = ('custom-bot' in players_in_session
                      or 'custom-bot' in all_session_agents
                      or any(c == 'custom-bot' for c in caps)
                      or any(l == 'custom-bot' for l in lost))

        for m in ALL_MODELS:
            if m not in players_in_session and not caps.get(m) and not lost.get(m):
                continue
            score_val = None
            if final_scores and m in final_scores:
                s = final_scores[m]
                score_val = s.get('total', s.get('totalScore', s.get('score', 0))) if isinstance(s, dict) else s
            # All-era buckets
            if score_val is not None:
                all_scores[m].append(score_val)
            all_caps[m].append(caps.get(m, 0))
            all_lost[m].append(lost.get(m, 0))
            # Cloud-only-era buckets (only when custom-bot not present and only for cloud players)
            if not has_custom and m in CLOUD:
                if score_val is not None:
                    cloud_scores[m].append(score_val)
                cloud_caps[m].append(caps.get(m, 0))
                cloud_lost[m].append(lost.get(m, 0))

    return {
        'cloud': (cloud_scores, cloud_caps, cloud_lost),
        'all':   (all_scores,   all_caps,   all_lost),
        'methods': attack_method_counts,
    }


def chart_avg_scores(per_model_scores, include_custom=True, suffix=''):
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    fig.patch.set_facecolor(CHART_BG)
    pool = ALL_MODELS if include_custom else CLOUD
    models = [m for m in pool if per_model_scores.get(m)]
    means = [np.mean(per_model_scores[m]) for m in models]
    sds = [np.std(per_model_scores[m], ddof=1) if len(per_model_scores[m]) > 1 else 0
           for m in models]
    bars = ax.bar([PRETTY[m] for m in models], means, yerr=sds, capsize=4,
                  color=[COLORS[m] for m in models], edgecolor='white', linewidth=0.8)
    ax.set_ylabel('Mean total score')
    ax.set_title('Mean total score per AI model (with SD error bars)',
                 loc='left', fontsize=12, fontweight='bold')
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width()/2, m + max(sds)*0.05, f'{m:.0f}',
                ha='center', fontsize=10, fontweight='bold')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    plt.tight_layout()
    out = os.path.join(HERE, f'chart_avg_scores{suffix}.png')
    fig.savefig(out, facecolor=CHART_BG, bbox_inches='tight')
    plt.close(fig)
    return out


def chart_capture_vs_lost(per_caps, per_lost, include_custom=True, suffix=''):
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    fig.patch.set_facecolor(CHART_BG)
    pool = ALL_MODELS if include_custom else CLOUD
    models = [m for m in pool if per_caps.get(m)]
    cap_means = [np.mean(per_caps[m]) for m in models]
    lost_means = [np.mean(per_lost[m]) for m in models]
    x = np.arange(len(models)); w = 0.36
    ax.bar(x - w/2, cap_means, w, label='Flags captured (offense)', color='#10B981')
    ax.bar(x + w/2, lost_means, w, label='Flags lost (defense)', color='#EF4444')
    ax.set_xticks(x); ax.set_xticklabels([PRETTY[m] for m in models])
    ax.set_ylabel('Mean per session')
    ax.set_title('Offense (flags captured) vs defense (flags lost), per session',
                 loc='left', fontsize=12, fontweight='bold')
    ax.legend(frameon=False)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    plt.tight_layout()
    out = os.path.join(HERE, f'chart_capture_vs_lost{suffix}.png')
    fig.savefig(out, facecolor=CHART_BG, bbox_inches='tight')
    plt.close(fig)
    return out


def chart_attack_methods(counts):
    fig, ax = plt.subplots(figsize=(9, 5), dpi=160)
    fig.patch.set_facecolor(CHART_BG)
    items = counts.most_common()
    if not items:
        items = [('(no commands logged)', 0)]
    labels = [k for k, _ in items]; values = [v for _, v in items]
    bars = ax.bar(labels, values, color='#0EA5E9', edgecolor='white', linewidth=0.6)
    ax.set_ylabel('Total command count')
    ax.set_title('Attack-method usage across all gameplay sessions',
                 loc='left', fontsize=12, fontweight='bold')
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width()/2, v + max(values)*0.02, str(v),
                ha='center', fontsize=10)
    plt.setp(ax.get_xticklabels(), rotation=20, ha='right')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    plt.tight_layout()
    out = os.path.join(HERE, 'chart_attack_methods.png')
    fig.savefig(out, facecolor=CHART_BG, bbox_inches='tight')
    plt.close(fig)
    return out


def main():
    data = load_session_metrics()
    cloud_s, cloud_c, cloud_l = data['cloud']
    all_s, all_c, all_l = data['all']
    methods = data['methods']

    print('Per-model session counts:')
    print('  --- cloud-only era ---')
    for m in CLOUD:
        print(f'  {m}: scores n={len(cloud_s[m])}, caps n={len(cloud_c[m])}, lost n={len(cloud_l[m])}')
    print('  --- all-era (cloud + with-custom-bot) ---')
    for m in ALL_MODELS:
        print(f'  {m}: scores n={len(all_s[m])}, caps n={len(all_c[m])}, lost n={len(all_l[m])}')

    outs = []
    # Cloud-only charts (used in early Results section before the custom bot reveal)
    outs.append(chart_avg_scores(cloud_s, include_custom=False, suffix='_cloud'))
    outs.append(chart_capture_vs_lost(cloud_c, cloud_l, include_custom=False, suffix='_cloud'))
    # All-era charts (used in The Custom-Trained AI section)
    outs.append(chart_avg_scores(all_s, include_custom=True, suffix='_all'))
    outs.append(chart_capture_vs_lost(all_c, all_l, include_custom=True, suffix='_all'))
    # Attack methods (single chart, all sessions)
    outs.append(chart_attack_methods(methods))
    print(f'\nWrote:')
    for o in outs:
        print(f'  {o}')


if __name__ == '__main__':
    main()
