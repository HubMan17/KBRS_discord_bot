#!/usr/bin/env python3
# Render dynamic Tailwind card to PNG using Playwright + Jinja2
import argparse, json, base64, mimetypes
from pathlib import Path
from jinja2 import Template
from playwright.sync_api import sync_playwright

def file_to_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if not mime: mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"

def compute_progress_pct(value: int, maxv: int) -> float:
    maxv = max(1, int(maxv))
    return round(min(100.0, max(0.0, (value / maxv) * 100.0)), 4)

def render_dynamic_card(template_path: Path, out_path: Path, **ctx):
    ctx.setdefault("username", "User")
    ctx.setdefault("initials", "U")
    ctx.setdefault("avatar", None)
    ctx.setdefault("level", 1)
    ctx.setdefault("value", 0)
    ctx.setdefault("max", 100)
    ctx.setdefault("rank", 1)
    ctx.setdefault("top", 1)
    
    ctx.setdefault("label_lvl",  "Lvl")
    ctx.setdefault("label_rank", "Rank")
    ctx.setdefault("label_top",  "Top")
    ctx.setdefault("label_next", "Next")

    ctx["progress_pct"] = compute_progress_pct(ctx["value"], ctx["max"])

    if ctx.get("avatar"):
        p = Path(str(ctx["avatar"]))
        if p.exists():
            ctx["avatar"] = file_to_data_url(p)

    html = Template(Path(template_path).read_text(encoding="utf-8")).render(**ctx)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 500, "height": 188, "deviceScaleFactor": 2})
        page.set_content(html, wait_until="load")
        page.wait_for_timeout(300)
        page.locator("body").screenshot(path=str(out_path), omit_background=True)
        browser.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="card_dynamic.html")
    ap.add_argument("--out", default="card.png")
    ap.add_argument("--data", default=None)
    ap.add_argument("--username", default=None)
    ap.add_argument("--initials", default=None)
    ap.add_argument("--avatar", default=None)
    ap.add_argument("--level", type=int, default=None)
    ap.add_argument("--value", type=int, default=None)
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--rank", type=int, default=None)
    ap.add_argument("--top", type=int, default=None)
    args = ap.parse_args()

    ctx = {}
    if args.data:
        ctx.update(json.loads(Path(args.data).read_text(encoding="utf-8")))
    for k in ("username","initials","avatar","level","value","max","rank","top"):
        v = getattr(args, k)
        if v is not None:
            ctx[k] = v

    render_dynamic_card(Path(args.template), Path(args.out), **ctx)
    print("Saved:", args.out)
