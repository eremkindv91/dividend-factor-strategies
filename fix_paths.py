#!/usr/bin/env python3
"""
Fix hardcoded paths in all 3 notebooks to use relative paths.
Also add setup cell at the beginning of each notebook.
"""
import json
import copy
import os

REPO = os.path.dirname(os.path.abspath(__file__))
NB_DIR = os.path.join(REPO, "notebooks")

# ─── Setup cell to prepend to each notebook ──────────────────────────
SETUP_CELL_CODE = """# ─── Setup: paths & dependencies ─────────────────────────────────────
import os, sys
from pathlib import Path

# Determine project root (works both locally and on Colab/Binder)
if 'google.colab' in sys.modules:
    # Google Colab: clone repo if needed
    REPO_URL = "https://github.com/dremkin/dividend-factor-strategies"
    if not os.path.exists("/content/dividend-factor-strategies"):
        os.system(f"git clone {REPO_URL} /content/dividend-factor-strategies")
    PROJECT_ROOT = Path("/content/dividend-factor-strategies")
else:
    # Local / Binder: notebook is in notebooks/, go up one level
    PROJECT_ROOT = Path(os.getcwd()).parent if Path(os.getcwd()).name == "notebooks" else Path(os.getcwd())

DATA_DIR = PROJECT_ROOT / "data"
print(f"Project root : {PROJECT_ROOT}")
print(f"Data directory: {DATA_DIR}")
assert DATA_DIR.exists(), f"Data directory not found: {DATA_DIR}"
"""

SETUP_CELL_MARKDOWN = """# Прогноз дивидендов и построение факторных стратегий: сопоставление рынков РФ и Японии

**Автор:** Еремкин Дмитрий Игоревич
**Магистерская программа:** «Финансовый аналитик», НИУ ВШЭ, 2025

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/dremkin/dividend-factor-strategies/blob/main/notebooks/{nb_filename})
"""


def make_code_cell(source):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.strip().split("\n")
    }

def make_markdown_cell(source):
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": [line + "\n" for line in source.strip().split("\n")]
    }

def fix_source_lines(lines):
    """Fix lines list (each line is a string)."""
    fixed = []
    for line in lines:
        fixed.append(line)
    return fixed

def join_source(cell):
    """Get source as single string."""
    src = cell.get("source", [])
    if isinstance(src, list):
        return "".join(src)
    return src

def set_source(cell, text):
    """Set source from string, preserving newlines."""
    cell["source"] = text.split("\n")
    # re-add newlines to all but last
    for i in range(len(cell["source"]) - 1):
        cell["source"][i] = cell["source"][i] + "\n"


# ═══════════════════════════════════════════════════════════════════════
# Notebook 1: 01_factor_backtest.ipynb
# ═══════════════════════════════════════════════════════════════════════
def fix_nb1():
    path = os.path.join(NB_DIR, "01_factor_backtest.ipynb")
    with open(path, "r", encoding="utf-8") as f:
        nb = json.load(f)

    # Fix Cell 0 (or 1): replace BASE path
    for cell in nb["cells"]:
        src = join_source(cell)
        if cell["cell_type"] == "code" and "/Users/dmitrijeremkin" in src:
            # Replace hardcoded BASE
            new_src = src.replace(
                "BASE = '/Users/dmitrijeremkin/Desktop/диплом созвон 5'",
                "BASE = str(DATA_DIR)"
            ).replace(
                'BASE = "/Users/dmitrijeremkin/Desktop/диплом созвон 5"',
                "BASE = str(DATA_DIR)"
            )
            # Also handle OUT dir -> results folder
            new_src = new_src.replace(
                "OUT  = os.path.join(BASE, 'output_factors')",
                "OUT  = str(PROJECT_ROOT / 'results' / 'factor_backtest')"
            ).replace(
                "OUT = os.path.join(BASE, 'output_factors')",
                "OUT = str(PROJECT_ROOT / 'results' / 'factor_backtest')"
            )
            set_source(cell, new_src)

    # Add setup cells at beginning
    md_cell = make_markdown_cell(
        SETUP_CELL_MARKDOWN.format(nb_filename="01_factor_backtest.ipynb")
    )
    code_cell = make_code_cell(SETUP_CELL_CODE)
    nb["cells"] = [md_cell, code_cell] + nb["cells"]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
    print(f"  Fixed: {path}")


# ═══════════════════════════════════════════════════════════════════════
# Notebook 2: 02_quality_factor_research.ipynb
# ═══════════════════════════════════════════════════════════════════════
def fix_nb2():
    path = os.path.join(NB_DIR, "02_quality_factor_research.ipynb")
    with open(path, "r", encoding="utf-8") as f:
        nb = json.load(f)

    for cell in nb["cells"]:
        src = join_source(cell)
        if cell["cell_type"] == "code" and "/Users/dmitrijeremkin" in src:
            new_src = src.replace(
                "BASE = '/Users/dmitrijeremkin/Desktop/диплом созвон 5'",
                "BASE = str(DATA_DIR)"
            ).replace(
                'BASE = "/Users/dmitrijeremkin/Desktop/диплом созвон 5"',
                "BASE = str(DATA_DIR)"
            )
            new_src = new_src.replace(
                "OUT  = os.path.join(BASE, 'output')",
                "OUT  = str(PROJECT_ROOT / 'results' / 'quality_research')"
            ).replace(
                "OUT = os.path.join(BASE, 'output')",
                "OUT = str(PROJECT_ROOT / 'results' / 'quality_research')"
            )
            set_source(cell, new_src)

    md_cell = make_markdown_cell(
        SETUP_CELL_MARKDOWN.format(nb_filename="02_quality_factor_research.ipynb")
    )
    code_cell = make_code_cell(SETUP_CELL_CODE)
    nb["cells"] = [md_cell, code_cell] + nb["cells"]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
    print(f"  Fixed: {path}")


# ═══════════════════════════════════════════════════════════════════════
# Notebook 3: 03_ml_dividend_forecast.ipynb
# ═══════════════════════════════════════════════════════════════════════
def fix_nb3():
    path = os.path.join(NB_DIR, "03_ml_dividend_forecast.ipynb")
    with open(path, "r", encoding="utf-8") as f:
        nb = json.load(f)

    for cell in nb["cells"]:
        src = join_source(cell)
        if cell["cell_type"] == "code" and "/Users/dmitrijeremkin" in src:
            new_src = src
            # Fix PATH_RF
            new_src = new_src.replace(
                "PATH_RF = '/Users/dmitrijeremkin/Desktop/диплом созвон 5/рф_ПАНЕЛЬ_enriched_BACKUP2.xlsx'",
                "PATH_RF = str(DATA_DIR / 'рф_ПАНЕЛЬ_enriched_BACKUP2.xlsx')"
            ).replace(
                'PATH_RF = "/Users/dmitrijeremkin/Desktop/диплом созвон 5/рф_ПАНЕЛЬ_enriched_BACKUP2.xlsx"',
                "PATH_RF = str(DATA_DIR / 'рф_ПАНЕЛЬ_enriched_BACKUP2.xlsx')"
            )
            # Also handle enriched (not backup) version
            new_src = new_src.replace(
                "PATH_RF = '/Users/dmitrijeremkin/Desktop/диплом созвон 5/рф_ПАНЕЛЬ_enriched.xlsx'",
                "PATH_RF = str(DATA_DIR / 'рф_ПАНЕЛЬ_enriched.xlsx')"
            ).replace(
                'PATH_RF = "/Users/dmitrijeremkin/Desktop/диплом созвон 5/рф_ПАНЕЛЬ_enriched.xlsx"',
                "PATH_RF = str(DATA_DIR / 'рф_ПАНЕЛЬ_enriched.xlsx')"
            )
            # Fix PATH_JP
            new_src = new_src.replace(
                "PATH_JP = '/Users/dmitrijeremkin/Desktop/диплом созвон 5/JP_PANEL_enriched.xlsx'",
                "PATH_JP = str(DATA_DIR / 'JP_PANEL_enriched.xlsx')"
            ).replace(
                'PATH_JP = "/Users/dmitrijeremkin/Desktop/диплом созвон 5/JP_PANEL_enriched.xlsx"',
                "PATH_JP = str(DATA_DIR / 'JP_PANEL_enriched.xlsx')"
            )
            # Fix OUT_DIR
            new_src = new_src.replace(
                "OUT_DIR = '/Users/dmitrijeremkin/Desktop/диплом созвон 5/OUTPUT_ML_V5'",
                "OUT_DIR = str(PROJECT_ROOT / 'results' / 'ml_forecast_v5')"
            ).replace(
                'OUT_DIR = "/Users/dmitrijeremkin/Desktop/диплом созвон 5/OUTPUT_ML_V5"',
                "OUT_DIR = str(PROJECT_ROOT / 'results' / 'ml_forecast_v5')"
            )
            # Generic catch-all for any remaining paths
            import re
            new_src = re.sub(
                r"""['"]\/Users\/dmitrijeremkin\/Desktop\/диплом созвон 5\/([^'"]+)['"]""",
                lambda m: f"str(DATA_DIR / '{m.group(1)}')",
                new_src
            )
            set_source(cell, new_src)

    md_cell = make_markdown_cell(
        SETUP_CELL_MARKDOWN.format(nb_filename="03_ml_dividend_forecast.ipynb")
    )
    code_cell = make_code_cell(SETUP_CELL_CODE)
    nb["cells"] = [md_cell, code_cell] + nb["cells"]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
    print(f"  Fixed: {path}")


if __name__ == "__main__":
    print("Fixing notebook paths...")
    fix_nb1()
    fix_nb2()
    fix_nb3()
    print("Done! All paths updated to relative.")
