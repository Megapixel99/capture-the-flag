#!/usr/bin/env python3
"""Render markdown reports to PDF using matplotlib text rendering."""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

OUTPUT_DIR = os.path.dirname(__file__)

def md_to_pdf(md_path, pdf_path, title):
    """Convert markdown to a multi-page PDF with text rendering."""
    with open(md_path) as f:
        text = f.read()

    # Split into pages at section headers (##)
    lines = text.split('\n')
    pages = []
    current = []
    for line in lines:
        if line.startswith('## ') and len(current) > 5:
            pages.append('\n'.join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        pages.append('\n'.join(current))

    with PdfPages(pdf_path) as pdf:
        for i, page_text in enumerate(pages):
            fig = plt.figure(figsize=(8.5, 11))
            # Clean up markdown formatting for plain text rendering
            clean = page_text
            clean = clean.replace('**', '')
            clean = clean.replace('`', '')
            clean = clean.replace('*See:', '  See:')
            clean = clean.replace('---', '─' * 60)

            fig.text(0.05, 0.95, clean,
                     transform=fig.transFigure,
                     fontsize=7.5,
                     verticalalignment='top',
                     fontfamily='monospace',
                     wrap=True)

            plt.axis('off')
            pdf.savefig(fig)
            plt.close(fig)

    print(f"Saved {pdf_path} ({len(pages)} pages)")

md_to_pdf(
    os.path.join(OUTPUT_DIR, 'report_technical.md'),
    os.path.join(OUTPUT_DIR, 'report_technical.pdf'),
    'AI CTF Competition — Technical Report'
)

md_to_pdf(
    os.path.join(OUTPUT_DIR, 'report_general.md'),
    os.path.join(OUTPUT_DIR, 'report_general.pdf'),
    'Can AI Models Hack Computers?'
)
