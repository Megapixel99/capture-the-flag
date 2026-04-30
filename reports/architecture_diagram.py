#!/usr/bin/env python3.11
"""Render a clean architecture diagram of the AI CTF project.

Outputs a 16:9 PNG (and PDF) suitable for a presentation slide.

Usage:
  python3.11 reports/architecture_diagram.py
  python3.11 reports/architecture_diagram.py --output ~/Desktop/ctf_arch
"""

import argparse
import os

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

# ---------- palette ----------
BG          = '#FAFBFC'
INK         = '#1F2937'
INK_SOFT    = '#4B5563'
INK_FAINT   = '#9CA3AF'
PANEL_FILL  = '#FFFFFF'
PANEL_EDGE  = '#D1D5DB'
ACCENT      = '#0F766E'   # teal — overall flow accent
ARROW       = '#374151'

# Agent palette (matches video viewer)
COLORS = {
    'GPT-OSS 120B':    '#4A90D9',
    'Gemini 3 Flash':  '#50C878',
    'GLM-5.1':         '#9B59B6',
    'Nemotron 3 Super':'#F5A623',
    'RNJ-1 8B':        '#E85D4A',
    'Custom Bot (3B)': '#14B8A6',
}

# Section accent fills (very faint backgrounds)
TINT_AGENTS  = '#EEF2FF'
TINT_ENGINE  = '#FEF3C7'
TINT_TARGETS = '#FEE2E2'
TINT_OUTPUTS = '#DCFCE7'
TINT_TRAIN   = '#E0F2FE'


def panel(ax, x, y, w, h, fill=PANEL_FILL, edge=PANEL_EDGE, lw=1.0, radius=0.018):
    box = FancyBboxPatch((x, y), w, h,
                         boxstyle=f'round,pad=0.002,rounding_size={radius}',
                         facecolor=fill, edgecolor=edge, linewidth=lw, zorder=2)
    ax.add_patch(box)
    return box


def chip(ax, x, y, w, h, label, color, text_color='white', fontsize=9, bold=True):
    """A solid colored pill — used for AI agents and pipeline stages."""
    box = FancyBboxPatch((x, y), w, h,
                         boxstyle='round,pad=0.002,rounding_size=0.012',
                         facecolor=color, edgecolor='none', zorder=4)
    ax.add_patch(box)
    ax.text(x + w/2, y + h/2, label, ha='center', va='center',
            color=text_color, fontsize=fontsize,
            fontweight='bold' if bold else 'normal', zorder=5)


def label(ax, x, y, text, fontsize=11, weight='normal', color=INK,
          ha='left', va='baseline'):
    ax.text(x, y, text, fontsize=fontsize, fontweight=weight, color=color,
            ha=ha, va=va, zorder=6)


def arrow(ax, x1, y1, x2, y2, color=ARROW, lw=1.6, style='-|>',
          connection='arc3', text=None, text_offset=(0, 0.012)):
    a = FancyArrowPatch((x1, y1), (x2, y2),
                        arrowstyle=style, mutation_scale=12,
                        color=color, linewidth=lw,
                        connectionstyle=connection, zorder=3)
    ax.add_patch(a)
    if text:
        mx, my = (x1 + x2)/2 + text_offset[0], (y1 + y2)/2 + text_offset[1]
        ax.text(mx, my, text, fontsize=8.5, color=INK_SOFT,
                ha='center', va='center',
                bbox=dict(boxstyle='round,pad=0.18', facecolor=BG,
                          edgecolor='none'), zorder=6)


def render(output_base, fig_w=16, fig_h=9, include_custom_bot=True):
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=180)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_aspect('auto'); ax.axis('off')

    # ---------- title ----------
    label(ax, 0.5, 0.965, 'AI Capture-the-Flag — System Architecture',
          fontsize=18, weight='bold', ha='center', va='top')
    subtitle = ('Multi-LLM attack/defend tournament with a locally-trained custom bot'
                if include_custom_bot
                else 'Multi-LLM attack/defend tournament — 5 cloud models')
    label(ax, 0.5, 0.927, subtitle,
          fontsize=11, color=INK_SOFT, ha='center', va='top')

    # ============================================================
    #  Left column: main pipeline (top-down)
    #  Right column: custom-bot training pipeline (only when included)
    # ============================================================
    if include_custom_bot:
        LEFT_X, LEFT_W = 0.04, 0.62
    else:
        # Use the full width when the training pipeline isn't shown
        LEFT_X, LEFT_W = 0.06, 0.88

    # ---------- (1) AI Agents panel ----------
    A_Y, A_H = 0.74, 0.12
    panel(ax, LEFT_X, A_Y, LEFT_W, A_H, fill=TINT_AGENTS, edge=PANEL_EDGE)
    label(ax, LEFT_X + 0.012, A_Y + A_H - 0.022, '1.  AI AGENT POOL',
          fontsize=11, weight='bold', color=INK)
    pool_subtitle = ('— 5 cloud LLMs + 1 locally-trained custom bot'
                     if include_custom_bot
                     else '— 5 cloud LLMs (GPT-OSS, Gemini, GLM, Nemotron, RNJ)')
    label(ax, LEFT_X + 0.13, A_Y + A_H - 0.022, pool_subtitle,
          fontsize=9.5, color=INK_SOFT)

    agent_names = list(COLORS.keys())
    if not include_custom_bot:
        agent_names = [n for n in agent_names if 'Custom Bot' not in n]
    # Per-chip width sized to label length so long names ("Nemotron 3 Super")
    # don't crowd the chip edges.
    chip_h, gap = 0.038, 0.008
    pad = 0.020          # horizontal padding inside the chip
    char_w = 0.0070      # approximate rendered width per character at fontsize 8.5
    chip_widths = [max(0.078, len(n) * char_w + pad) for n in agent_names]
    row_w = sum(chip_widths) + gap * (len(agent_names) - 1)
    cx = LEFT_X + (LEFT_W - row_w) / 2
    cy = A_Y + 0.018
    for name, cw in zip(agent_names, chip_widths):
        chip(ax, cx, cy, cw, chip_h, name, COLORS[name], fontsize=8.5)
        cx += cw + gap

    # ---------- (2) Game Engine ----------
    E_Y, E_H = 0.55, 0.13
    panel(ax, LEFT_X, E_Y, LEFT_W, E_H, fill=TINT_ENGINE, edge=PANEL_EDGE)
    label(ax, LEFT_X + 0.012, E_Y + E_H - 0.022,
          '2.  GAME ENGINE  (Node.js)',
          fontsize=11, weight='bold', color=INK)
    bullets = [
        '• Live / interactive matches — every agent attacks every other in real time',
        '• Defense phase (30 sec): each agent hardens its own container',
        '• Battle phase (5 min): agents probe opponents, capture flags, score points',
        '• Periodic vulnerability checks audit which boxes are still hardened',
    ]
    for k, t in enumerate(bullets):
        label(ax, LEFT_X + 0.022, E_Y + E_H - 0.045 - 0.022*k, t,
              fontsize=9.3, color=INK_SOFT)

    # ---------- (3) Privileged Containers ----------
    T_Y, T_H = 0.36, 0.13
    panel(ax, LEFT_X, T_Y, LEFT_W, T_H, fill=TINT_TARGETS, edge=PANEL_EDGE)
    label(ax, LEFT_X + 0.012, T_Y + T_H - 0.022,
          '3.  VULNERABLE TARGETS  (Docker, one per agent)',
          fontsize=11, weight='bold', color=INK)
    label(ax, LEFT_X + 0.012, T_Y + T_H - 0.044,
          '8 plantable vulnerabilities  ·  privileged containers  ·  isolated network',
          fontsize=9.3, color=INK_SOFT)
    n_boxes = len(agent_names)
    box_w, box_h, box_gap = 0.080, 0.052, 0.011
    row_w = n_boxes * box_w + (n_boxes - 1) * box_gap
    bx = LEFT_X + (LEFT_W - row_w) / 2
    by = T_Y + 0.018
    for i, name in enumerate(agent_names):
        chip(ax, bx, by, box_w, box_h, f'box-{i+1}',
             '#FFFFFF', text_color=INK, fontsize=8, bold=False)
        # Colored stripe so each container is visually paired with its owner
        stripe = patches.Rectangle((bx, by), box_w, 0.006,
                                   facecolor=COLORS[name], edgecolor='none', zorder=5)
        ax.add_patch(stripe)
        bx += box_w + box_gap

    # ---------- (4) Artifacts ----------
    O_Y, O_H = 0.16, 0.14
    panel(ax, LEFT_X, O_Y, LEFT_W, O_H, fill=TINT_OUTPUTS, edge=PANEL_EDGE)
    label(ax, LEFT_X + 0.012, O_Y + O_H - 0.022,
          '4.  ARTIFACTS & ANALYSIS',
          fontsize=11, weight='bold', color=INK)
    label(ax, LEFT_X + 0.012, O_Y + O_H - 0.044,
          'logs/session-<timestamp>/  →  every event captured for offline analysis',
          fontsize=9.3, color=INK_SOFT)
    artifact_chips = [
        ('Session logs (JSON)',  '#6B7280'),
        ('HTML session viewer',  '#7C3AED'),
        ('MP4 video replays',    '#DC2626'),
    ]
    cw, ch, cg = 0.158, 0.040, 0.012
    row_w = len(artifact_chips) * cw + (len(artifact_chips) - 1) * cg
    cx = LEFT_X + (LEFT_W - row_w) / 2
    cy = O_Y + 0.018
    for text, col in artifact_chips:
        chip(ax, cx, cy, cw, ch, text, col, fontsize=8.5)
        cx += cw + cg

    # ---------- arrows between left-column panels ----------
    midx = LEFT_X + LEFT_W / 2
    arrow(ax, midx, A_Y, midx, E_Y + E_H, text='HTTPS / Ollama API')
    arrow(ax, midx, E_Y, midx, T_Y + T_H, text='docker exec — commands & shell I/O')
    arrow(ax, midx, T_Y, midx, O_Y + O_H, text='structured event stream')

    # ============================================================
    #  Right column: custom bot training pipeline (only when included)
    # ============================================================
    if not include_custom_bot:
        # Footer caption + early return — cloud-only diagram is done
        label(ax, 0.5, 0.045,
              'Every match is recorded as a structured event log → '
              'reports, viewers, and video replays for analysis',
              fontsize=10, color=INK_SOFT, ha='center', va='center')
        png_path = output_base + '.png'
        pdf_path = output_base + '.pdf'
        fig.savefig(png_path, dpi=180, bbox_inches='tight', facecolor=BG)
        fig.savefig(pdf_path, bbox_inches='tight', facecolor=BG)
        plt.close(fig)
        print(f'Wrote {png_path}')
        print(f'Wrote {pdf_path}')
        return

    R_X, R_W = 0.70, 0.26
    R_Y, R_H = 0.16, 0.70
    panel(ax, R_X, R_Y, R_W, R_H, fill=TINT_TRAIN, edge=PANEL_EDGE)
    label(ax, R_X + 0.012, R_Y + R_H - 0.025,
          'CUSTOM BOT — TRAINING LOOP',
          fontsize=11, weight='bold', color=INK)
    label(ax, R_X + 0.012, R_Y + R_H - 0.047,
          '3B Qwen2.5 trained on tournament replays',
          fontsize=9.3, color=INK_SOFT)

    # Pipeline stages — vertical stack
    stages = [
        ('Game replays\n(106+ sessions)',          '#6366F1'),
        ('Extract & weight\ntraining pairs',       '#8B5CF6'),
        ('LoRA fine-tune\n(Apple MLX)',            '#A855F7'),
        ('Fuse adapter\ninto base model',          '#EC4899'),
        ('Quantize → Q4_K_M',                      '#F43F5E'),
        ('Serve via Ollama\n(ctf-custom-q4)',      '#14B8A6'),
    ]
    sx, sw = R_X + 0.024, R_W - 0.048
    sh = 0.073
    sg = 0.013
    sy = R_Y + R_H - 0.085 - sh
    for text, col in stages:
        chip(ax, sx, sy, sw, sh, text, col, fontsize=9, bold=True)
        # arrow into the next stage (skip after last)
        if sy - sg - sh / 2 > R_Y:
            arrow(ax, sx + sw / 2, sy,
                  sx + sw / 2, sy - sg, lw=1.4)
        sy -= sh + sg

    # Feedback arrow: served bot back into the game engine
    # (curve from training panel down-left into the engine panel)
    feedback_start_x = R_X + 0.005
    feedback_start_y = R_Y + 0.06
    feedback_end_x = LEFT_X + LEFT_W
    feedback_end_y = E_Y + E_H / 2
    arrow(ax,
          feedback_start_x, feedback_start_y,
          feedback_end_x, feedback_end_y,
          color=ACCENT, lw=1.8, style='-|>',
          connection='arc3,rad=-0.25',
          text='deploy as “Custom Bot”',
          text_offset=(-0.06, -0.04))

    # And replays feeding training: dashed arrow from artifacts up to top of pipeline
    replay_arrow = FancyArrowPatch(
        (LEFT_X + LEFT_W, O_Y + O_H * 0.7),
        (R_X, R_Y + R_H - 0.10),
        arrowstyle='-|>', mutation_scale=12,
        color=ACCENT, linewidth=1.6, linestyle=(0, (4, 3)),
        connectionstyle='arc3,rad=0.25', zorder=3)
    ax.add_patch(replay_arrow)
    ax.text(0.685, 0.205, 'replays\nfeed training',
            fontsize=8.5, color=ACCENT, ha='center', va='center', style='italic',
            bbox=dict(boxstyle='round,pad=0.2', facecolor=BG, edgecolor='none'),
            zorder=6)

    # ---------- footer caption ----------
    label(ax, 0.5, 0.045,
          'Closed loop: every game generates training data → next bot version → '
          'plays the next round of games',
          fontsize=10, color=INK_SOFT, ha='center', va='center', weight='normal')

    # Save PNG + PDF
    png_path = output_base + '.png'
    pdf_path = output_base + '.pdf'
    fig.savefig(png_path, dpi=180, bbox_inches='tight', facecolor=BG)
    fig.savefig(pdf_path, bbox_inches='tight', facecolor=BG)
    plt.close(fig)
    print(f'Wrote {png_path}')
    print(f'Wrote {pdf_path}')


def main():
    p = argparse.ArgumentParser(description='Render the CTF architecture diagram.')
    p.add_argument('--output',
                   help='Output base path (without extension). '
                        'Both .png and .pdf are written. '
                        'Default: reports/architecture_diagram[_cloud_only].')
    p.add_argument('--no-custom-bot', action='store_true',
                   help='Render the cloud-only variant (5 LLMs, no training pipeline).')
    args = p.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    if args.output:
        out = args.output
    elif args.no_custom_bot:
        out = os.path.join(here, 'architecture_diagram_cloud_only')
    else:
        out = os.path.join(here, 'architecture_diagram')
    render(out, include_custom_bot=not args.no_custom_bot)


if __name__ == '__main__':
    main()
