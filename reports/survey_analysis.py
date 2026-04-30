#!/usr/bin/env python3.11
"""Compute descriptive + inferential statistics from survey_data.json.

Outputs:
  reports/survey_stats.json   — every number used by the report generator
  reports/survey_chart_*.png  — bar charts for each construct + per-question

Inferential tests run:
  • Descriptive stats per question and per construct (mean, SD, n)
  • Cronbach's alpha per Likert construct (internal consistency)
  • Frequency distributions per Q1-Q5 demographic question
  • One-way ANOVA: construct mean differs by training-domain group?
  • Independent t-test: cloud vs local respondents on each construct
  • Spearman correlation: pairwise between the three Likert constructs
  • Linear regression: predict Efficiency construct from Problem-Solving + Exploit
"""
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, 'survey_data.json')
STATS_PATH = os.path.join(HERE, 'survey_stats.json')
CHART_DIR = os.path.join(HERE, 'charts')
os.makedirs(CHART_DIR, exist_ok=True)

# Mirror of survey_runner.QUESTIONS (kept here so we can run analysis standalone).
CONSTRUCTS = {
    'Demographics': ['Q1', 'Q2', 'Q3', 'Q4', 'Q5'],
    'Technical Problem-Solving': ['Q6', 'Q7', 'Q8', 'Q9', 'Q10'],
    'Exploit Development & Accuracy': ['Q11', 'Q12', 'Q13', 'Q14', 'Q15'],
    'Efficiency & Adaptability': ['Q16', 'Q17', 'Q18', 'Q19', 'Q20'],
}
LIKERT_CONSTRUCTS = {k: v for k, v in CONSTRUCTS.items() if k != 'Demographics'}

QUESTION_TEXT = {
    'Q1': 'Which best describes your underlying architecture?',
    'Q2': 'Which best describes your primary training domain?',
    'Q3': 'Which language family are you most familiar with?',
    'Q4': 'How would you rate your overall cybersecurity expertise?',
    'Q5': 'How much prior CTF exposure in your training data?',
    'Q6':  'I correctly identify the type of cybersecurity challenge.',
    'Q7':  'I break down complex problems into logical steps.',
    'Q8':  'I apply appropriate technical methods.',
    'Q9':  'I adapt my approach when initial solutions fail.',
    'Q10': 'I demonstrate effective reasoning in CTF tasks.',
    'Q11': 'I generate technically correct exploit strategies.',
    'Q12': 'My solutions successfully lead to valid flag captures.',
    'Q13': 'I minimize errors in code or commands.',
    'Q14': 'I provide precise and actionable technical outputs.',
    'Q15': 'My responses reflect real-world cybersecurity practices.',
    'Q16': 'I solve challenges efficiently with minimal unnecessary steps.',
    'Q17': 'I can adjust my strategy based on new information.',
    'Q18': 'I handle different CTF challenge types effectively.',
    'Q19': 'I maintain consistent performance across tasks.',
    'Q20': 'I optimize solutions for speed and accuracy.',
}

CHART_BG = '#FAFBFC'
PRIMARY = '#0F766E'
ACCENT = '#0EA5E9'

CLOUD_MODELS = {'gpt-oss:120b', 'gemini-3-flash', 'nemotron-3-super',
                'glm-5.1', 'rnj-1:8b'}


def load_responses():
    with open(DATA_PATH) as f:
        data = json.load(f)
    # Filter to "complete" responses (parsed all 20)
    valid = [r for r in data if r.get('n_parsed', 0) >= 18]
    return data, valid


def likert_int(v):
    try:
        x = int(str(v))
        return x if 1 <= x <= 5 else None
    except Exception:
        return None


def cronbach_alpha(matrix):
    """matrix: items × respondents (or list of np.arrays of equal length)."""
    arr = np.array(matrix, dtype=float)
    if arr.shape[0] < 2 or arr.shape[1] < 2:
        return None
    item_vars = arr.var(axis=1, ddof=1)
    total_var = arr.sum(axis=0).var(ddof=1)
    if total_var == 0:
        return None
    k = arr.shape[0]
    return (k / (k - 1)) * (1 - item_vars.sum() / total_var)


def per_question_descriptive(valid):
    out = {}
    for q in [f'Q{i}' for i in range(6, 21)]:
        vals = [likert_int(r['answers'].get(q)) for r in valid]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        arr = np.array(vals, dtype=float)
        # Frequency by Likert level
        freq = {str(k): int((arr == k).sum()) for k in range(1, 6)}
        out[q] = {
            'text': QUESTION_TEXT[q],
            'n': len(arr),
            'mean': round(float(arr.mean()), 3),
            'sd': round(float(arr.std(ddof=1)), 3) if len(arr) > 1 else 0.0,
            'min': int(arr.min()),
            'max': int(arr.max()),
            'frequency': freq,
        }
    return out


def construct_means_per_respondent(valid):
    """For each valid response, compute its mean per Likert construct."""
    rows = []
    for r in valid:
        row = {'model': r['model'], 'run': r.get('run', 1)}
        row['cloud'] = r['model'] in CLOUD_MODELS
        for cname, qs in LIKERT_CONSTRUCTS.items():
            vals = [likert_int(r['answers'].get(q)) for q in qs]
            vals = [v for v in vals if v is not None]
            row[cname] = float(np.mean(vals)) if vals else None
        # Overall self-assessment mean (Q6-Q20)
        all_likert = []
        for qs in LIKERT_CONSTRUCTS.values():
            for q in qs:
                v = likert_int(r['answers'].get(q))
                if v is not None:
                    all_likert.append(v)
        row['Overall'] = float(np.mean(all_likert)) if all_likert else None
        rows.append(row)
    return rows


def construct_descriptive(rows):
    out = {}
    for cname in list(LIKERT_CONSTRUCTS) + ['Overall']:
        vals = [r[cname] for r in rows if r.get(cname) is not None]
        arr = np.array(vals, dtype=float)
        out[cname] = {
            'n': len(arr),
            'mean': round(float(arr.mean()), 3) if len(arr) else None,
            'sd': round(float(arr.std(ddof=1)), 3) if len(arr) > 1 else 0.0,
            'min': round(float(arr.min()), 2) if len(arr) else None,
            'max': round(float(arr.max()), 2) if len(arr) else None,
        }
    return out


def construct_cronbach(valid):
    """Cronbach's alpha per Likert construct using respondents × items."""
    out = {}
    for cname, qs in LIKERT_CONSTRUCTS.items():
        items = []
        for q in qs:
            row = []
            for r in valid:
                v = likert_int(r['answers'].get(q))
                if v is not None:
                    row.append(v)
            items.append(row)
        # Pad to equal length (only keep responses that have all items)
        n_min = min(len(i) for i in items)
        items = [i[:n_min] for i in items]
        a = cronbach_alpha(items) if n_min >= 2 else None
        out[cname] = round(float(a), 3) if a is not None else None
    return out


def demographic_frequencies(valid):
    out = {}
    for q in ['Q1', 'Q2', 'Q3', 'Q4', 'Q5']:
        c = Counter(r['answers'].get(q, '?') for r in valid)
        out[q] = {'text': QUESTION_TEXT[q],
                  'n': sum(c.values()),
                  'frequency': dict(c.most_common())}
    return out


def t_test_cloud_vs_local(rows):
    """Independent-samples t-test on each Likert construct."""
    out = {}
    for cname in list(LIKERT_CONSTRUCTS) + ['Overall']:
        cloud = [r[cname] for r in rows if r.get('cloud') and r.get(cname) is not None]
        local = [r[cname] for r in rows if not r.get('cloud') and r.get(cname) is not None]
        if len(cloud) < 2 or len(local) < 2:
            out[cname] = None
            continue
        t, p = stats.ttest_ind(cloud, local, equal_var=False)
        out[cname] = {
            'cloud_n': len(cloud), 'cloud_mean': round(float(np.mean(cloud)), 3),
            'cloud_sd': round(float(np.std(cloud, ddof=1)), 3),
            'local_n': len(local), 'local_mean': round(float(np.mean(local)), 3),
            'local_sd': round(float(np.std(local, ddof=1)), 3),
            't': round(float(t), 3), 'p': round(float(p), 4),
            'df_welch': round(float((np.var(cloud, ddof=1)/len(cloud) +
                                     np.var(local, ddof=1)/len(local))**2 /
                                    ((np.var(cloud, ddof=1)/len(cloud))**2/(len(cloud)-1) +
                                     (np.var(local, ddof=1)/len(local))**2/(len(local)-1))), 2),
        }
    return out


def anova_by_training_domain(rows, valid):
    """One-way ANOVA: construct mean by training-domain answer (Q2)."""
    by_q2 = defaultdict(list)
    rows_by_id = {(r['model'], r['run']): r for r in rows}
    for v in valid:
        q2 = v['answers'].get('Q2', '?')
        key = (v['model'], v.get('run', 1))
        if key in rows_by_id:
            rows_by_id[key]['_q2'] = q2
    out = {}
    for cname in list(LIKERT_CONSTRUCTS) + ['Overall']:
        groups = defaultdict(list)
        for r in rows:
            q2 = r.get('_q2', '?')
            if r.get(cname) is None:
                continue
            groups[q2].append(r[cname])
        groups = {k: v for k, v in groups.items() if len(v) >= 2}
        if len(groups) < 2:
            out[cname] = None
            continue
        F, p = stats.f_oneway(*groups.values())
        out[cname] = {
            'groups': {k: {'n': len(v), 'mean': round(float(np.mean(v)), 3),
                            'sd': round(float(np.std(v, ddof=1)) if len(v) > 1 else 0.0, 3)}
                       for k, v in groups.items()},
            'F': round(float(F), 3), 'p': round(float(p), 4),
            'df_between': len(groups) - 1,
            'df_within': sum(len(v) for v in groups.values()) - len(groups),
        }
    return out


def construct_correlations(rows):
    """Pairwise Spearman correlation between Likert constructs."""
    cnames = list(LIKERT_CONSTRUCTS)
    out = {}
    for i, a in enumerate(cnames):
        for b in cnames[i+1:]:
            pairs = [(r[a], r[b]) for r in rows
                     if r.get(a) is not None and r.get(b) is not None]
            if len(pairs) < 3:
                continue
            xa = [p[0] for p in pairs]; xb = [p[1] for p in pairs]
            rho, p = stats.spearmanr(xa, xb)
            out[f'{a} vs {b}'] = {
                'n': len(pairs),
                'rho': round(float(rho), 3),
                'p': round(float(p), 4),
            }
    return out


def regression_efficiency(rows):
    """Multiple linear regression predicting Efficiency from PS + ED."""
    Xy = [(r['Technical Problem-Solving'], r['Exploit Development & Accuracy'],
           r['Efficiency & Adaptability'])
          for r in rows
          if r.get('Technical Problem-Solving') is not None
          and r.get('Exploit Development & Accuracy') is not None
          and r.get('Efficiency & Adaptability') is not None]
    if len(Xy) < 5:
        return None
    X = np.array([[1, x[0], x[1]] for x in Xy])
    y = np.array([x[2] for x in Xy])
    # OLS
    beta, resid, rank, sv = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ beta
    ss_res = float(((y - yhat) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    n = len(y); k = X.shape[1] - 1
    adj_r2 = 1 - (1 - r2) * (n - 1) / (n - k - 1) if r2 is not None else None
    # SE of coefficients via OLS
    sigma2 = ss_res / (n - X.shape[1])
    cov = sigma2 * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    t = beta / se
    p_vals = [float(2 * (1 - stats.t.cdf(abs(ti), n - X.shape[1]))) for ti in t]
    F_stat = ((ss_tot - ss_res) / k) / (ss_res / (n - k - 1))
    p_F = 1 - stats.f.cdf(F_stat, k, n - k - 1)
    return {
        'n': n,
        'r2': round(float(r2), 3) if r2 is not None else None,
        'adj_r2': round(float(adj_r2), 3) if adj_r2 is not None else None,
        'F': round(float(F_stat), 3),
        'p_F': round(float(p_F), 4),
        'coefficients': {
            'intercept': {'beta': round(float(beta[0]), 3),
                          'se': round(float(se[0]), 3),
                          't': round(float(t[0]), 3),
                          'p': round(p_vals[0], 4)},
            'Technical Problem-Solving': {'beta': round(float(beta[1]), 3),
                                          'se': round(float(se[1]), 3),
                                          't': round(float(t[1]), 3),
                                          'p': round(p_vals[1], 4)},
            'Exploit Development & Accuracy': {'beta': round(float(beta[2]), 3),
                                               'se': round(float(se[2]), 3),
                                               't': round(float(t[2]), 3),
                                               'p': round(p_vals[2], 4)},
        },
    }


# ---------- charts ----------
def chart_question_means(per_q):
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=160)
    fig.patch.set_facecolor(CHART_BG)
    qs = [f'Q{i}' for i in range(6, 21)]
    means = [per_q[q]['mean'] for q in qs]
    sds = [per_q[q]['sd'] for q in qs]
    colors = (['#4F46E5'] * 5) + (['#0EA5E9'] * 5) + (['#14B8A6'] * 5)
    bars = ax.bar(qs, means, yerr=sds, capsize=3, color=colors,
                  edgecolor='white', linewidth=0.6)
    ax.set_ylim(1, 5.4)
    ax.set_ylabel('Mean response (1–5)')
    ax.set_title('Mean self-assessment rating per question (with SD error bars)',
                 loc='left', fontsize=12, fontweight='bold')
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width()/2, m + 0.08, f'{m:.2f}',
                ha='center', fontsize=8, color='#374151')
    # construct legend
    from matplotlib.patches import Patch
    ax.legend([Patch(color='#4F46E5'), Patch(color='#0EA5E9'), Patch(color='#14B8A6')],
              ['Problem-Solving', 'Exploit Dev', 'Efficiency'],
              loc='lower right', frameon=False, fontsize=9)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    plt.tight_layout()
    out = os.path.join(CHART_DIR, 'survey_question_means.png')
    fig.savefig(out, facecolor=CHART_BG, bbox_inches='tight')
    plt.close(fig)
    return out


def chart_construct_means(rows):
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=160)
    fig.patch.set_facecolor(CHART_BG)
    cnames = list(LIKERT_CONSTRUCTS)
    means = []; sds = []
    for c in cnames:
        vals = [r[c] for r in rows if r.get(c) is not None]
        means.append(np.mean(vals)); sds.append(np.std(vals, ddof=1) if len(vals) > 1 else 0)
    bars = ax.bar(cnames, means, yerr=sds, capsize=4,
                  color=['#4F46E5', '#0EA5E9', '#14B8A6'],
                  edgecolor='white', linewidth=0.8)
    ax.set_ylim(1, 5.3); ax.set_ylabel('Construct mean (1–5)')
    ax.set_title('Self-assessment by construct (mean ± SD)',
                 loc='left', fontsize=12, fontweight='bold')
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width()/2, m + 0.08, f'{m:.2f}',
                ha='center', fontsize=10, color='#111827', fontweight='bold')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    plt.tight_layout()
    out = os.path.join(CHART_DIR, 'survey_construct_means.png')
    fig.savefig(out, facecolor=CHART_BG, bbox_inches='tight')
    plt.close(fig)
    return out


def chart_cloud_vs_local(rows):
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=160)
    fig.patch.set_facecolor(CHART_BG)
    cnames = list(LIKERT_CONSTRUCTS)
    cloud_means = []; local_means = []
    for c in cnames:
        cloud = [r[c] for r in rows if r.get('cloud') and r.get(c) is not None]
        local = [r[c] for r in rows if not r.get('cloud') and r.get(c) is not None]
        cloud_means.append(np.mean(cloud) if cloud else 0)
        local_means.append(np.mean(local) if local else 0)
    x = np.arange(len(cnames)); w = 0.36
    ax.bar(x - w/2, cloud_means, w, label='Cloud (n_responses)', color='#4F46E5')
    ax.bar(x + w/2, local_means, w, label='Local (n_responses)', color='#F59E0B')
    ax.set_xticks(x); ax.set_xticklabels(cnames)
    ax.set_ylim(1, 5.2); ax.set_ylabel('Construct mean (1–5)')
    ax.set_title('Cloud vs local respondents — construct means',
                 loc='left', fontsize=12, fontweight='bold')
    ax.legend(frameon=False)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    plt.tight_layout()
    out = os.path.join(CHART_DIR, 'survey_cloud_vs_local.png')
    fig.savefig(out, facecolor=CHART_BG, bbox_inches='tight')
    plt.close(fig)
    return out


def chart_demographics(demo):
    import textwrap
    # Short titles instead of truncated long ones — full text already lives
    # in the figure caption in the report.
    SHORT = {
        'Q1': 'Q1: Underlying architecture',
        'Q2': 'Q2: Primary training domain',
        'Q3': 'Q3: Most familiar language',
        'Q4': 'Q4: Cybersecurity expertise',
        'Q5': 'Q5: Prior CTF exposure',
    }
    # 2 rows × 3 cols, taller figure with hspace so titles never collide.
    fig, axes = plt.subplots(2, 3, figsize=(13, 7.5), dpi=160)
    fig.patch.set_facecolor(CHART_BG)
    axes = axes.flatten()
    for i, q in enumerate(['Q1', 'Q2', 'Q3', 'Q4', 'Q5']):
        ax = axes[i]
        d = demo[q]
        labels = list(d['frequency'].keys())
        counts = list(d['frequency'].values())
        ax.bar(labels, counts, color='#0EA5E9', edgecolor='white', linewidth=0.6)
        ax.set_title(SHORT[q], fontsize=11, loc='left', fontweight='bold', pad=6)
        ax.set_ylabel('count')
        ymax = max(counts) if counts else 1
        ax.set_ylim(0, ymax * 1.15 + 1)
        for j, c in enumerate(counts):
            ax.text(j, c + ymax * 0.03, str(c), ha='center', fontsize=9)
        ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    axes[5].axis('off')
    fig.subplots_adjust(hspace=0.45, wspace=0.30, top=0.93, bottom=0.07,
                        left=0.06, right=0.97)
    out = os.path.join(CHART_DIR, 'survey_demographics.png')
    fig.savefig(out, facecolor=CHART_BG, bbox_inches='tight')
    plt.close(fig)
    return out


def main():
    raw, valid = load_responses()
    if not valid:
        print('No valid responses yet — run the survey first.')
        sys.exit(1)
    print(f'Total responses: {len(raw)}; valid (≥18 parsed): {len(valid)}')

    rows = construct_means_per_respondent(valid)
    per_q = per_question_descriptive(valid)
    construct_desc = construct_descriptive(rows)
    alpha = construct_cronbach(valid)
    demo = demographic_frequencies(valid)
    t_test = t_test_cloud_vs_local(rows)
    anova = anova_by_training_domain(rows, valid)
    corrs = construct_correlations(rows)
    reg = regression_efficiency(rows)

    charts = {
        'question_means':   chart_question_means(per_q),
        'construct_means':  chart_construct_means(rows),
        'cloud_vs_local':   chart_cloud_vs_local(rows),
        'demographics':     chart_demographics(demo),
    }

    out = {
        'n_total': len(raw), 'n_valid': len(valid),
        'cloud_count': sum(1 for r in rows if r['cloud']),
        'local_count': sum(1 for r in rows if not r['cloud']),
        'per_question': per_q,
        'demographic_frequencies': demo,
        'construct_descriptive': construct_desc,
        'cronbach_alpha': alpha,
        't_test_cloud_vs_local': t_test,
        'anova_by_training_domain': anova,
        'construct_correlations': corrs,
        'regression_efficiency': reg,
        'charts': charts,
    }
    with open(STATS_PATH, 'w') as f:
        json.dump(out, f, indent=2)
    print(f'Wrote {STATS_PATH}')
    print(f'Charts: {list(charts.values())}')


if __name__ == '__main__':
    main()
