#!/usr/bin/env python3
import sys, time
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
from browser import Browser, load_json

settings = load_json("settings.json")

with Browser(settings) as b:
    print("pages:", len(b.ctx.pages))
    for pi, pg in enumerate(b.ctx.pages):
        print(f"page[{pi}] url={pg.url[:90]}")
        for fi, fr in enumerate(pg.frames[:8]):
            try:
                n = fr.locator("tr.merchant-table__row").count()
            except Exception:
                n = -1
            print(f"  frame[{fi}] {fr.url[:70]} rows={n}")
