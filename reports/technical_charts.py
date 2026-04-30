#!/usr/bin/env python3.11
"""Generate the full set of charts used by the technical CTF report.

Output: reports/technical_charts/*.png
"""
import json
import glob
import os
from collections import Counter, defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, '..', 'logs')
OUT_DIR = os.path.join(HERE, 'technical_charts')
os.makedirs(OUT_DIR, exist_ok=True)

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
ALL_VULNS = ['root_password', 'sudo', 'web_service', 'shadow_perms', 'cron', 'suid_find']
VULN_PRETTY = {
    'root_password': 'Weak root password',
    'sudo':          'Passwordless sudo (ctfservice)',
    'web_service':   'Command-injection web service',
    'shadow_perms':  'World-readable /etc/shadow',
    'cron':          'World-writable cron job',
    'suid_find':     'SUID find binary',
}
BG = '#FAFBFC'


# ---------- data extraction ----------
def load_session_metrics():
    sessions = sorted(glob.glob(os.path.join(LOGS, 'session-*')))
    cloud_scores = defaultdict(list); cloud_caps = defaultdict(list); cloud_lost = defaultdict(list)
    cloud_bonuses = defaultdict(list); cloud_survivors = defaultdict(int); cloud_session_n = 0
    all_scores = defaultdict(list); all_caps = defaultdict(list); all_lost = defaultdict(list)
    all_bonuses = defaultdict(list)
    methods = Counter()
    cmd_count_per_session = defaultdict(list)  # commands issued per model per session
    cmd_count_cloud = defaultdict(list)        # cloud-only era version
    time_to_first_cap = defaultdict(list)      # seconds, all sessions
    time_to_first_cap_cloud = defaultdict(list)  # cloud-only era version
    patch_counts = defaultdict(lambda: defaultdict(lambda: {'patched': 0, 'unpatched': 0}))
    # Per-model command usage by tool category (for exploit heatmap)
    method_by_model = defaultdict(lambda: Counter())

    METHOD_BUCKETS = [
        ('curl/wget',     ('curl', 'wget')),
        ('nmap/masscan',  ('nmap', 'masscan', 'rustscan')),
        ('hydra/ssh',     ('hydra', 'sshpass', 'medusa', 'ssh ')),
        ('nikto/gobuster',('nikto', 'gobuster', 'dirb')),
        ('nc/netcat',     ('netcat', ' nc ', '\\bnc\\b')),
        ('cat/file read', ('cat ', 'less ', 'more ', 'head ', 'tail ')),
        ('find/locate',   ('find ', 'locate ')),
        ('python exploit',('python -c', 'python3 -c', 'python -m')),
        ('bash payload',  ('bash -c', 'sh -c', '/bin/sh', '/bin/bash')),
        ('credential dump', ('shadow', 'passwd', '.bash_history')),
        ('sqlmap',        ('sqlmap',)),
        ('john/hashcat',  ('john', 'hashcat')),
    ]

    for sd in sessions:
        gf = os.path.join(sd, 'game.json')
        if not os.path.exists(gf):
            continue
        try:
            events = json.load(open(gf))
        except Exception:
            continue

        # scoreboard
        final_scores = None
        for e in reversed(events):
            if e.get('type') == 'scoreboard' and isinstance(e.get('scores'), dict):
                final_scores = e['scores']
                break

        # parse all events
        caps = Counter(); lost = Counter(); bonuses = Counter()
        cmd_counts = Counter()
        first_cap_t = {}
        game_start = None
        has_custom = False

        for e in events:
            ts = e.get('timestamp', '')
            msg = e.get('message', '')
            if 'Game started' in msg and not game_start:
                game_start = ts

            # commands
            cmd = e.get('command') or ''
            agent = e.get('agent', '')
            if cmd and agent:
                cmd_counts[agent] += 1
                low = cmd.lower()
                for label, kws in METHOD_BUCKETS:
                    if any(kw in low for kw in kws):
                        methods[label] += 1
                        if agent in ALL_MODELS:
                            method_by_model[agent][label] += 1
                        break

            # captures
            if 'CAPTURED' in msg:
                a = e.get('attacker'); v = e.get('victim')
                if a and v and a != v:
                    if 'BONUS' in msg or e.get('bonus'):
                        bonuses[a] += 1
                    else:
                        caps[a] += 1
                        lost[v] += 1
                        if a not in first_cap_t and game_start and ts:
                            try:
                                from datetime import datetime
                                t0 = datetime.fromisoformat(game_start.replace('Z', '+00:00'))
                                t1 = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                                first_cap_t[a] = (t1 - t0).total_seconds()
                            except Exception:
                                pass

            # vuln_check
            if msg == 'Vulnerability check' and isinstance(e.get('report'), dict):
                for player, vlist in e['report'].items():
                    if player not in ALL_MODELS: continue
                    unpatched = {x.strip() for x in vlist.split(',') if x.strip()}
                    for vv in ALL_VULNS:
                        if vv in unpatched:
                            patch_counts[player][vv]['unpatched'] += 1
                        else:
                            patch_counts[player][vv]['patched'] += 1

        all_session_agents = {e.get('agent') for e in events if e.get('agent')}
        if 'custom-bot' in all_session_agents or (final_scores and 'custom-bot' in final_scores):
            has_custom = True

        for m in ALL_MODELS:
            score_val = None
            if final_scores and m in final_scores:
                s = final_scores[m]
                if isinstance(s, dict):
                    score_val = s.get('total', 0)
            cmd_count_per_session[m].append(cmd_counts.get(m, 0))
            if m in first_cap_t:
                time_to_first_cap[m].append(first_cap_t[m])
            if score_val is None and m not in caps and m not in lost:
                continue
            all_scores[m].append(score_val if score_val is not None else 0)
            all_caps[m].append(caps.get(m, 0))
            all_lost[m].append(lost.get(m, 0))
            all_bonuses[m].append(bonuses.get(m, 0))
            if not has_custom and m in CLOUD:
                cloud_scores[m].append(score_val if score_val is not None else 0)
                cloud_caps[m].append(caps.get(m, 0))
                cloud_lost[m].append(lost.get(m, 0))
                cloud_bonuses[m].append(bonuses.get(m, 0))
                cmd_count_cloud[m].append(cmd_counts.get(m, 0))
                if m in first_cap_t:
                    time_to_first_cap_cloud[m].append(first_cap_t[m])

    return {
        'cloud': {'scores': cloud_scores, 'caps': cloud_caps, 'lost': cloud_lost,
                  'bonuses': cloud_bonuses},
        'all':   {'scores': all_scores, 'caps': all_caps, 'lost': all_lost,
                  'bonuses': all_bonuses},
        'methods': methods,
        'method_by_model': method_by_model,
        'cmd_count': cmd_count_per_session,
        'cmd_count_cloud': cmd_count_cloud,
        'time_to_first_cap': time_to_first_cap,
        'time_to_first_cap_cloud': time_to_first_cap_cloud,
        'patch_counts': patch_counts,
        'n_sessions': len(sessions),
    }


# ---------- chart helpers ----------
def _save(fig, name):
    out = os.path.join(OUT_DIR, name + '.png')
    fig.savefig(out, facecolor=BG, bbox_inches='tight', dpi=160)
    plt.close(fig)
    return out


def _new(figsize=(9, 5)):
    fig, ax = plt.subplots(figsize=figsize, dpi=160)
    fig.patch.set_facecolor(BG)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    return fig, ax


# ---------- charts ----------
def chart_avg_score(data, models, suffix):
    fig, ax = _new()
    means = [np.mean(data['scores'][m]) if data['scores'].get(m) else 0 for m in models]
    sds = [np.std(data['scores'][m], ddof=1) if data['scores'].get(m) and len(data['scores'][m]) > 1 else 0 for m in models]
    bars = ax.bar([PRETTY[m] for m in models], means, yerr=sds, capsize=4,
                  color=[COLORS[m] for m in models], edgecolor='white', linewidth=0.8)
    ax.set_ylabel('Mean total score (points)')
    ax.set_title(f'Mean total score per model ({suffix})', loc='left', fontweight='bold')
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, m + max(sds) * 0.05 + 5, f'{m:.0f}',
                ha='center', fontsize=10, fontweight='bold')
    plt.tight_layout()
    return _save(fig, f'01_avg_score_{suffix}')


def chart_capture_vs_lost(data, models, suffix):
    fig, ax = _new()
    cap = [np.mean(data['caps'][m]) if data['caps'].get(m) else 0 for m in models]
    lost = [np.mean(data['lost'][m]) if data['lost'].get(m) else 0 for m in models]
    x = np.arange(len(models)); w = 0.36
    ax.bar(x - w / 2, cap, w, label='Flags captured (offense)', color='#10B981')
    ax.bar(x + w / 2, lost, w, label='Flags lost (defense)', color='#EF4444')
    ax.set_xticks(x); ax.set_xticklabels([PRETTY[m] for m in models])
    ax.set_ylabel('Mean per session')
    ax.set_title(f'Offense vs defense ({suffix})', loc='left', fontweight='bold')
    ax.legend(frameon=False)
    plt.tight_layout()
    return _save(fig, f'02_capture_vs_lost_{suffix}')


def chart_bonuses(data, models, suffix):
    fig, ax = _new()
    means = [np.mean(data['bonuses'][m]) if data['bonuses'].get(m) else 0 for m in models]
    bars = ax.bar([PRETTY[m] for m in models], means,
                  color=[COLORS[m] for m in models], edgecolor='white', linewidth=0.8)
    ax.set_ylabel('Mean bonus flags per session')
    ax.set_title(f'Bonus flag captures per model ({suffix})', loc='left', fontweight='bold')
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, m + 0.02, f'{m:.2f}',
                ha='center', fontsize=10)
    plt.tight_layout()
    return _save(fig, f'03_bonuses_{suffix}')


def chart_score_distribution(data, models, suffix):
    fig, ax = _new()
    parts = ax.violinplot([data['scores'][m] for m in models if data['scores'].get(m)],
                          showmeans=True, showmedians=False)
    for i, (pc, m) in enumerate(zip(parts['bodies'],
                                     [m for m in models if data['scores'].get(m)])):
        pc.set_facecolor(COLORS[m]); pc.set_alpha(0.6)
    ax.set_xticks(np.arange(1, len([m for m in models if data['scores'].get(m)]) + 1))
    ax.set_xticklabels([PRETTY[m] for m in models if data['scores'].get(m)])
    ax.set_ylabel('Total score (points)')
    ax.set_title(f'Score distribution per model ({suffix})', loc='left', fontweight='bold')
    ax.axhline(0, color='#999', linewidth=0.8, linestyle='--')
    plt.tight_layout()
    return _save(fig, f'04_score_violin_{suffix}')


def chart_patch_heatmap(patch_counts, models, name, title_suffix=''):
    fig, ax = plt.subplots(figsize=(10, max(3.0, 0.6 * len(models) + 1.5)), dpi=160)
    fig.patch.set_facecolor(BG)
    rates = np.array([
        [(patch_counts[m][v]['patched'] /
          max(patch_counts[m][v]['patched'] + patch_counts[m][v]['unpatched'], 1)) * 100
         for v in ALL_VULNS]
        for m in models
    ])
    im = ax.imshow(rates, cmap='RdYlGn', vmin=0, vmax=100, aspect='auto')
    ax.set_xticks(range(len(ALL_VULNS)))
    ax.set_xticklabels([VULN_PRETTY[v] for v in ALL_VULNS], rotation=20, ha='right')
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels([PRETTY[m] for m in models])
    for i in range(len(models)):
        for j in range(len(ALL_VULNS)):
            v = rates[i, j]
            color = 'white' if v < 35 or v > 80 else 'black'
            ax.text(j, i, f'{v:.0f}%', ha='center', va='center', fontsize=10, color=color)
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('% of audits where vulnerability was patched')
    ax.set_title(f'Patch rate heatmap — model × vulnerability{title_suffix}',
                 loc='left', fontweight='bold')
    plt.tight_layout()
    return _save(fig, name)


def chart_custom_bot_versions():
    """Captures/losses across custom-bot evaluation runs.

    v3 and v4 were abandoned during training and never reached the evaluation
    stage, so they are omitted from the chart. The full version history
    (including the abandoned iterations) lives in the report's Table 3.
    """
    fig, ax1 = plt.subplots(figsize=(10.5, 4.5), dpi=160)
    fig.patch.set_facecolor(BG)
    versions = ['v1', 'v2', 'v5', 'v6', 'v7', 'v8']
    captures = [0.41, 0.86, 0.78, 1.86, 1.39, 2.60]
    losses = [1.18, 1.10, 1.02, 0.59, 0.41, 0.70]
    decisions = ['baseline', 'promoted', 'rolled back',
                 'promoted', 'rolled back', 'production']
    x = np.arange(len(versions))
    w = 0.36
    ax1.bar(x - w / 2, captures, w, label='Captures / game', color='#10B981')
    ax1.bar(x + w / 2, losses, w, label='Losses / game', color='#EF4444')
    ax1.set_xticks(x)
    ax1.set_xticklabels([f'{v}\n({d})' for v, d in zip(versions, decisions)],
                        fontsize=9)
    ax1.set_ylabel('Mean per session over 20-game evaluation window')
    ax1.set_title('Custom-bot performance across evaluated versions '
                  '(v3 and v4 abandoned during training)',
                  loc='left', fontweight='bold')
    ax1.legend(frameon=False, loc='upper left')
    ax1.spines['top'].set_visible(False); ax1.spines['right'].set_visible(False)
    for i, c in enumerate(captures):
        ax1.text(i - w / 2, c + 0.04, f'{c:.2f}', ha='center', fontsize=9)
    for i, l in enumerate(losses):
        ax1.text(i + w / 2, l + 0.04, f'{l:.2f}', ha='center', fontsize=9)
    plt.tight_layout()
    return _save(fig, '14_custom_bot_versions')


def chart_exploit_heatmap(method_by_model, models, name, title_suffix=''):
    """Model × attack-method matrix, normalized to row totals (% of each
    model's commands that fall in each tool category)."""
    # Order categories by total volume across these models
    cat_totals = Counter()
    for m in models:
        for k, v in method_by_model.get(m, {}).items():
            cat_totals[k] += v
    cats = [k for k, _ in cat_totals.most_common() if cat_totals[k] > 0]

    fig, ax = plt.subplots(figsize=(11, max(3.5, 0.6 * len(models) + 1.5)), dpi=160)
    fig.patch.set_facecolor(BG)
    matrix = np.zeros((len(models), len(cats)))
    for i, m in enumerate(models):
        row_total = sum(method_by_model.get(m, {}).get(c, 0) for c in cats)
        if row_total == 0:
            continue
        for j, c in enumerate(cats):
            matrix[i, j] = method_by_model[m][c] / row_total * 100
    im = ax.imshow(matrix, cmap='Blues', vmin=0, vmax=max(matrix.max(), 1), aspect='auto')
    ax.set_xticks(range(len(cats)))
    ax.set_xticklabels(cats, rotation=20, ha='right')
    ax.set_yticks(range(len(models)))
    ax.set_yticklabels([PRETTY[m] for m in models])
    for i in range(len(models)):
        for j in range(len(cats)):
            v = matrix[i, j]
            if v > 0.5:
                color = 'white' if v > matrix.max() * 0.55 else '#1F2937'
                ax.text(j, i, f'{v:.0f}%', ha='center', va='center',
                        fontsize=9, color=color)
    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.04)
    cbar.set_label('% of model\'s commands in this category')
    ax.set_title(f'Exploit / tool usage heatmap — model × attack method{title_suffix}',
                 loc='left', fontweight='bold')
    plt.tight_layout()
    return _save(fig, name)


def chart_patch_rate_by_vuln(patch_counts):
    fig, ax = _new()
    overall_rates = []
    for v in ALL_VULNS:
        patched = sum(patch_counts[m][v]['patched'] for m in CLOUD)
        total = sum(patch_counts[m][v]['patched'] + patch_counts[m][v]['unpatched']
                    for m in CLOUD)
        overall_rates.append(patched / total * 100 if total else 0)
    bars = ax.bar([VULN_PRETTY[v] for v in ALL_VULNS], overall_rates,
                  color='#0EA5E9', edgecolor='white', linewidth=0.6)
    ax.set_ylabel('% audits where vulnerability was patched')
    ax.set_title('Overall patch rate by vulnerability type (cloud models)',
                 loc='left', fontweight='bold')
    for b, r in zip(bars, overall_rates):
        ax.text(b.get_x() + b.get_width() / 2, r + 1.5, f'{r:.1f}%',
                ha='center', fontsize=10, fontweight='bold')
    ax.set_ylim(0, 110)
    plt.setp(ax.get_xticklabels(), rotation=20, ha='right')
    plt.tight_layout()
    return _save(fig, '06_patch_by_vuln')


def chart_patch_rate_by_model(patch_counts):
    fig, ax = _new()
    overall_rates = []
    for m in ALL_MODELS:
        patched = sum(patch_counts[m][v]['patched'] for v in ALL_VULNS)
        total = sum(patch_counts[m][v]['patched'] + patch_counts[m][v]['unpatched']
                    for v in ALL_VULNS)
        overall_rates.append(patched / total * 100 if total else 0)
    bars = ax.bar([PRETTY[m] for m in ALL_MODELS], overall_rates,
                  color=[COLORS[m] for m in ALL_MODELS], edgecolor='white', linewidth=0.6)
    ax.set_ylabel('% audits where any tracked vuln was patched')
    ax.set_title('Overall patch rate by model (across all six vulnerabilities)',
                 loc='left', fontweight='bold')
    for b, r in zip(bars, overall_rates):
        ax.text(b.get_x() + b.get_width() / 2, r + 1.0, f'{r:.1f}%',
                ha='center', fontsize=10, fontweight='bold')
    ax.set_ylim(0, max(overall_rates) * 1.18 + 5)
    plt.tight_layout()
    return _save(fig, '07_patch_by_model')


def chart_attack_methods(methods):
    fig, ax = _new()
    items = methods.most_common()
    if not items:
        items = [('(none)', 0)]
    labels = [k for k, _ in items]; values = [v for _, v in items]
    bars = ax.barh(labels, values, color='#0EA5E9', edgecolor='white', linewidth=0.6)
    ax.set_xlabel('Total command count across all sessions')
    ax.set_title('Attack-method usage by tool category (technical labels)',
                 loc='left', fontweight='bold')
    for b, v in zip(bars, values):
        ax.text(v + max(values) * 0.01, b.get_y() + b.get_height() / 2, str(v),
                va='center', fontsize=10)
    plt.tight_layout()
    return _save(fig, '08_attack_methods')


def chart_cmd_count(cmd_count, models, name, title_suffix=''):
    fig, ax = _new()
    means = [np.mean(cmd_count[m]) if cmd_count.get(m) else 0 for m in models]
    bars = ax.bar([PRETTY[m] for m in models], means,
                  color=[COLORS[m] for m in models], edgecolor='white', linewidth=0.8)
    ax.set_ylabel('Mean shell commands per session')
    ax.set_title(f'Command throughput per model{title_suffix}',
                 loc='left', fontweight='bold')
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, m + max(means) * 0.02, f'{m:.0f}',
                ha='center', fontsize=10, fontweight='bold')
    plt.tight_layout()
    return _save(fig, name)


def chart_time_to_first_cap(ttfc, models, name, title_suffix=''):
    fig, ax = _new()
    models_with = [m for m in models if ttfc.get(m)]
    means = [np.median(ttfc[m]) for m in models_with]
    bars = ax.bar([PRETTY[m] for m in models_with], means,
                  color=[COLORS[m] for m in models_with], edgecolor='white')
    ax.set_ylabel('Median seconds to first flag capture')
    ax.set_title(f'Time-to-first-capture (lower is faster){title_suffix}',
                 loc='left', fontweight='bold')
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, m + 4, f'{m:.0f}s',
                ha='center', fontsize=10, fontweight='bold')
    plt.tight_layout()
    return _save(fig, name)


def chart_offense_defense_balance(data, models, name, title_suffix=''):
    """Net offense - net defense per model: (caps + bonuses) - lost."""
    fig, ax = _new()
    nets = []
    for m in models:
        c = sum(data['caps'].get(m, []))
        b = sum(data['bonuses'].get(m, []))
        l = sum(data['lost'].get(m, []))
        nets.append((PRETTY[m], c, b, l, c + b - l))
    nets.sort(key=lambda x: -x[4])
    labels = [n[0] for n in nets]
    cs = [n[1] for n in nets]; bs = [n[2] for n in nets]; ls = [n[3] for n in nets]
    x = np.arange(len(labels)); w = 0.27
    ax.bar(x - w, cs, w, label='Flags captured', color='#10B981')
    ax.bar(x, bs, w, label='Bonus captures', color='#0EA5E9')
    ax.bar(x + w, ls, w, label='Flags lost', color='#EF4444')
    ax.set_xticks(x); ax.set_xticklabels(labels, rotation=15, ha='right')
    ax.set_ylabel('Total events across all sessions')
    ax.set_title(f'Total offense vs defense{title_suffix}',
                 loc='left', fontweight='bold')
    ax.legend(frameon=False)
    plt.tight_layout()
    return _save(fig, name)


# ---------- survey-derived charts ----------
def chart_self_assessment_vs_perf(survey_stats, performance):
    """Mean self-rating (Q6-Q20) vs actual mean captures/session for the 5 cloud + custom."""
    fig, ax = _new(figsize=(8.5, 5.5))
    by_model = survey_stats.get('per_respondent_overall', {})
    pts = []
    for m in ALL_MODELS:
        # Try to find this model in survey responses
        survey_key = {
            'gpt-oss': 'gpt-oss:120b',
            'gemini3': 'gemini-3-flash',
            'glm5': 'glm-5.1',
            'nemotron': 'nemotron-3-super',
            'rnj': 'rnj-1:8b',
            'custom-bot': 'ctf-custom-q4:latest',
        }.get(m)
        if survey_key in by_model and m in performance:
            self_rate = by_model[survey_key]
            cap = np.mean(performance[m]) if performance[m] else 0
            pts.append((m, self_rate, cap))
    for m, sr, cp in pts:
        ax.scatter(sr, cp, s=240, color=COLORS[m], edgecolor='white', linewidth=1.5,
                   zorder=3, label=PRETTY[m])
        ax.annotate(PRETTY[m], (sr, cp), xytext=(8, 8), textcoords='offset points',
                    fontsize=10)
    ax.set_xlabel('Mean self-assessment rating (1–5 Likert, Q6–Q20)')
    ax.set_ylabel('Mean flag captures per session (actual)')
    ax.set_title('Self-assessed capability vs actual offensive performance',
                 loc='left', fontweight='bold')
    ax.grid(True, linestyle='--', alpha=0.4)
    plt.tight_layout()
    return _save(fig, '12_self_vs_actual')


# ---------- main ----------
def main():
    print('Extracting session metrics from logs…')
    data = load_session_metrics()
    print(f'  {data["n_sessions"]} sessions read.')

    outs = []
    # Cloud-only era (106 sessions)
    outs.append(chart_avg_score(data['cloud'], CLOUD, 'cloud'))
    outs.append(chart_capture_vs_lost(data['cloud'], CLOUD, 'cloud'))
    outs.append(chart_bonuses(data['cloud'], CLOUD, 'cloud'))
    outs.append(chart_score_distribution(data['cloud'], CLOUD, 'cloud'))
    # Everywhere (custom bot included) — used in custom-bot section
    outs.append(chart_avg_score(data['all'], ALL_MODELS, 'all'))
    outs.append(chart_capture_vs_lost(data['all'], ALL_MODELS, 'all'))
    outs.append(chart_bonuses(data['all'], ALL_MODELS, 'all'))
    outs.append(chart_score_distribution(data['all'], ALL_MODELS, 'all'))

    # Vulnerability patching — cloud-only and all-era
    outs.append(chart_patch_heatmap(data['patch_counts'], CLOUD,
                                     '05_patch_heatmap_cloud',
                                     ' (cloud models, 106 sessions)'))
    outs.append(chart_patch_heatmap(data['patch_counts'], ALL_MODELS,
                                     '05_patch_heatmap_all',
                                     ' (all six competitors)'))
    outs.append(chart_patch_rate_by_vuln(data['patch_counts']))
    outs.append(chart_patch_rate_by_model(data['patch_counts']))

    # Exploit/tool usage heatmaps — cloud-only and all-era
    outs.append(chart_exploit_heatmap(data['method_by_model'], CLOUD,
                                       '13_exploit_heatmap_cloud',
                                       ' (cloud models)'))
    outs.append(chart_exploit_heatmap(data['method_by_model'], ALL_MODELS,
                                       '13_exploit_heatmap_all',
                                       ' (all six competitors)'))

    # Custom-bot version progression
    outs.append(chart_custom_bot_versions())

    # Tools and timing
    outs.append(chart_attack_methods(data['methods']))
    # Cloud-only versions for early Results section
    outs.append(chart_cmd_count(data['cmd_count_cloud'], CLOUD,
                                 '09_cmd_count_cloud',
                                 ' (cloud models)'))
    outs.append(chart_time_to_first_cap(data['time_to_first_cap_cloud'], CLOUD,
                                         '10_time_to_first_cap_cloud',
                                         ' (cloud models)'))
    outs.append(chart_offense_defense_balance(data['cloud'], CLOUD,
                                               '11_offense_defense_totals_cloud',
                                               ' (cloud models, 106 sessions)'))
    # All-era versions for custom-bot section
    outs.append(chart_cmd_count(data['cmd_count'], ALL_MODELS,
                                 '09_cmd_count_all',
                                 ' (all six competitors)'))
    outs.append(chart_time_to_first_cap(data['time_to_first_cap'], ALL_MODELS,
                                         '10_time_to_first_cap_all',
                                         ' (all six competitors)'))
    outs.append(chart_offense_defense_balance(data['all'], ALL_MODELS,
                                               '11_offense_defense_totals_all',
                                               ' (all six competitors)'))

    # Survey-derived (uses survey_stats.json + per-model captures)
    stats_path = os.path.join(HERE, 'survey_stats.json')
    if os.path.exists(stats_path):
        s = json.load(open(stats_path))
        # Build per-respondent overall mean for the 5 cloud + custom bot
        sd_path = os.path.join(HERE, 'survey_data.json')
        sd = json.load(open(sd_path))
        per_resp_overall = {}
        for r in sd:
            ans = r.get('answers', {})
            vals = []
            for q in [f'Q{i}' for i in range(6, 21)]:
                if q in ans:
                    try:
                        vals.append(int(ans[q]))
                    except (ValueError, TypeError):
                        pass
            if vals:
                per_resp_overall[r['model']] = np.mean(vals)
        s['per_respondent_overall'] = per_resp_overall
        outs.append(chart_self_assessment_vs_perf(s, data['all']['caps']))

    print(f'\nWrote {len(outs)} charts to {OUT_DIR}/:')
    for o in outs:
        print(f'  {os.path.basename(o)}')


if __name__ == '__main__':
    main()
