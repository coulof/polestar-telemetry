"""Capture README screenshots and a short tour video of the dashboard running on synthetic data.

    uv run python demo/make_demo_db.py data/demo.db
    DB_PATH=data/demo.db CONFIG_PATH=demo/config.toml uv run streamlit run app.py --server.port 8599 --server.headless true &
    uv run --with playwright python demo/capture.py http://localhost:8599 docs/img

Needs a Chromium that Playwright can find (`playwright install chromium`, or the system Chrome with CHROME=/path).
Output: now-light/dark.png, now-phone.png, trips, charging, report, battery (light), tour.webm and, when `ffmpeg` is on
the PATH, tour.gif (the tour without the page load, 8 fps, 720 px wide)."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8599"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/img")
OUT.mkdir(parents=True, exist_ok=True)
TABS = ["Now", "Report", "Trips", "Charging", "Battery"]            # screenshots
TOUR = ["Now", "Report", "Trips", "Charging", "Battery", "Insights", "Path"]   # the animated tour
SLOW = {"Path": 7000}                                               # the map tiles load after the tab opens


def open_app(browser, scheme="light", width=1280, height=1000, **kw):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme, device_scale_factor=2, **kw)
    page = ctx.new_page()
    page.goto(URL)
    page.get_by_role("tab", name="Now").wait_for(timeout=60000)
    page.get_by_text("Estimated range").wait_for(timeout=60000)
    page.get_by_text("Added", exact=True).wait_for(timeout=60000)   # the charging metrics load after the gauge
    page.wait_for_timeout(2500)
    return ctx, page


def tab(page, name, wait=2500):
    page.get_by_role("tab", name=name, exact=True).click()
    page.wait_for_timeout(wait)


def main():
    with sync_playwright() as p:
        kw = {"executable_path": os.environ["CHROME"]} if os.environ.get("CHROME") else {}
        browser = p.chromium.launch(**kw)
        for scheme in ("light", "dark"):
            ctx, page = open_app(browser, scheme)
            page.screenshot(path=OUT / f"now-{scheme}.png")
            if scheme == "light":
                for name in TABS[1:]:
                    tab(page, name)
                    page.screenshot(path=OUT / f"{name.lower()}.png", full_page=False)
            ctx.close()
        ctx, page = open_app(browser, "light", 390, 844, is_mobile=True, has_touch=True)
        page.screenshot(path=OUT / "now-phone.png")
        ctx.close()
        t0 = time.monotonic()
        ctx, page = open_app(browser, "light", 1000, 700, record_video_dir=str(OUT / "_video"), record_video_size={"width": 1000, "height": 700})
        skip = time.monotonic() - t0 - 1.0     # the recording starts with the page load: cut it, keeping a second of the loaded app
        for name in TOUR:
            tab(page, name, SLOW.get(name, 2200))
        ctx.close()   # flushes the video
        browser.close()
    vids = list((OUT / "_video").glob("*.webm"))
    if vids:
        vids[0].replace(OUT / "tour.webm")
        (OUT / "_video").rmdir()
        make_gif(OUT / "tour.webm", OUT / "tour.gif", skip)


def make_gif(webm, gif, skip):
    """webm to a looping GIF with a palette made for this video (small file, no dithering noise)."""
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found: tour.webm written, tour.gif skipped")
        return
    vf = "fps=8,scale=720:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(skip, 0):.2f}", "-i", str(webm), "-vf", vf, "-loop", "0", str(gif)], check=True)


if __name__ == "__main__":
    main()
