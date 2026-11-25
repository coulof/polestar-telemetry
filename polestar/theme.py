"""Shared look for the dashboard: palette, Plotly styling and CSS."""
import html

import streamlit as st

# Polestar orange: RAL 2000 (#ED760E) on light surfaces, brand #FF7500 on dark. Neutrals are the brand sheet's
# Black, White and Greys (the same values as .streamlit/config.toml).
LIGHT = {"series": "#ED760E", "good": "#1baf7a", "ink": "#000000", "muted": "#53565A", "grid": "rgba(83,86,90,0.15)", "low": "#e34948", "high": "#eda100",
         "baseline": "#75787B", "faint": "#C8C9C7", "rest": "#D9D9D6",   # brand Grey 9 / Grey 3 / Grey 1
         "surface": "#FFFFFF"}                        # page background, for rings around marks
# dark low/high re-stepped so the amber and red bands stay apart for normal vision (dataviz validator, ΔE 16)
DARK = {"series": "#FF7500", "good": "#199e70", "ink": "#ECECE7", "muted": "#C8C9C7", "grid": "rgba(200,201,199,0.15)", "low": "#e05a5a", "high": "#b38f00",
        "baseline": "#B1B3B3", "faint": "#53565A", "rest": "#53565A",   # brand Grey 5 / Grey 11
        "surface": "#000000"}

FONT = "Inter, Helvetica Neue, Helvetica, Arial, sans-serif"  # Unica77 is Helvetica-derived

CSS = """
<style>
.block-container { padding-top: 2.2rem; max-width: 1200px; }
/* Type follows the brand sheet: one regular weight, tight tracking, sentence case. Weights are set in config.toml. */
h1, h2, h3 { letter-spacing: -0.02em; }
/* the title carries the brand marking system: a short heavy dash with the headline under it */
[data-testid="stHeading"] h1 { font-size: 2.4rem; letter-spacing: -0.03em; padding-top: 0; }
[data-testid="stHeading"] h1::before { content: ""; display: block; width: 2.5rem; height: 4px; background: currentColor; margin-bottom: .9rem; }
/* section titles share the chart titles' size (theme.style), so the tabs read as one system */
[data-testid="stHeading"] h3 { font-size: 1rem; letter-spacing: -0.01em; }
.pt-big { font-size: 2.4rem; font-weight: 400; line-height: 1.1; letter-spacing: -0.03em; }
[data-testid="stMetricValue"] { font-size: 1.75rem; letter-spacing: -0.02em; }
[data-testid="stMetricLabel"], .pt-sub { opacity: .7; font-size: .8rem !important; }
/* figures that refresh in place keep their width */
[data-testid="stMetricValue"], .pt-big, .pt-chip, .js-plotly-plot text, [data-testid="stCaptionContainer"] { font-variant-numeric: tabular-nums; }
/* status chip pinned to the top-right corner of the hero card */
.st-key-hero { position: relative; }
/* no chart toolbar (download / full screen) in the hero card: it collided with the status chip on phones */
.st-key-hero [data-testid="stElementToolbar"] { display: none; }
.st-key-hero [data-testid="stElementContainer"]:has(.pt-chip) { position: absolute; top: .8rem; right: 0; width: auto; z-index: 2; }
.pt-chip { display: inline-flex; align-items: center; gap: .45rem; padding: .25rem .6rem; font-size: .72rem; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; }
.pt-dot { width: .5rem; height: .5rem; background: currentColor; }
.pt-chip.on { animation: pt-pulse 2.6s ease-in-out infinite; }
@keyframes pt-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .4; } }
@media (prefers-reduced-motion: reduce) { .pt-chip.on { animation: none; } }
@media (max-width: 640px) {
  [data-testid="stHeading"] h1 { font-size: 1.8rem; }
  /* keep nested metric columns two-per-row on phones instead of one tall stack */
  [data-testid="stColumn"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"] {
    min-width: calc(50% - 1rem) !important; flex: 1 1 calc(50% - 1rem) !important;
  }
  /* the same for the top-level metric rows of the other tabs (the hero card's own columns still stack) */
  [data-testid="stHorizontalBlock"]:not(.st-key-hero *):has([data-testid="stMetric"]) > [data-testid="stColumn"] {
    min-width: calc(50% - 1rem) !important; flex: 1 1 calc(50% - 1rem) !important;
  }
}
/* touch screens keep Plotly's toolbar always visible, on top of the chart titles: hide it there */
@media (hover: none) { .js-plotly-plot .modebar-container { display: none !important; } }
/* Sharp, flat look: square corners, hairlines instead of boxes.
   Bordered containers and metrics keep only a top hairline (theme borderColor) and no side padding, so their content
   lines up with the title and the tabs; unbordered ones have no border width, so they stay clean. */
* { border-radius: 0 !important; }
[data-testid="stVerticalBlock"], [data-testid="stVerticalBlockBorderWrapper"], [data-testid="stMetric"] {
  border-left-width: 0 !important; border-right-width: 0 !important; border-bottom-width: 0 !important;
  padding-left: 0 !important; padding-right: 0 !important; background: transparent !important; box-shadow: none !important;
}
[data-testid="stMetric"] > div { padding-left: 0 !important; padding-right: 0 !important; }
[data-testid="stExpander"] details { border-left-width: 0 !important; border-right-width: 0 !important; border-bottom-width: 0 !important; }
/* the first hairline of each tab already separates it from the tab bar: no second rule above it */
[data-testid="stTabs"] [role="tablist"]::after { display: none; }
/* public demo ribbon (POLESTAR_DEMO=1): a slim full-width bar above the title */
.demo-ribbon { display: block; padding: .6rem 1rem !important; margin: .7rem 0 1rem; color: #000 !important; text-decoration: none !important;
  font-size: .72rem; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; line-height: 1.4; }
.demo-ribbon:hover span { text-decoration: underline; }
</style>
"""

def is_dark():
    try:
        return st.context.theme.type == "dark"
    except Exception:
        return False


def chip(text, on, color="green"):
    """HTML for the status chip: tinted and gently pulsing while `on` (green for charging, gold for
    driving), grey otherwise."""
    if on and color == "gold":      # Swedish gold #F6BE00 (secondary brand colour)
        bg, fg = ("rgba(246,190,0,.20)", "#F6BE00") if is_dark() else ("rgba(246,190,0,.24)", "#7A5C00")
    elif on:
        bg, fg = ("rgba(25,158,112,.22)", "#6fdcb2") if is_dark() else ("rgba(27,175,122,.15)", "#0b6b4a")
    else:
        bg, fg = ("rgba(200,201,199,.15)", "#C8C9C7") if is_dark() else ("rgba(83,86,90,.10)", "#53565A")
    return (f'<span class="pt-chip{" on" if on else ""}" style="background:{bg};color:{fg}">'
            f'<span class="pt-dot"></span>{html.escape(str(text))}</span>')


def demo_ribbon(url):
    """HTML for the public demo's ribbon: an accent bar linking to the quick start."""
    return (f'<a class="demo-ribbon" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener" '
            f'style="background:{palette()["series"]}"><span>Live demo on synthetic data. Run it yourself: quick start on GitHub</span></a>')


def palette():
    return DARK if is_dark() else LIGHT


def style(fig, height=300, title=None, **layout):
    """Apply the shared chart look: transparent, thin grid, ink-coloured text, no legend."""
    p = palette()
    fig.update_layout(
        title=dict(text=title, font=dict(size=16, weight=500, color=p["ink"]), x=0) if title else None,
        height=height, margin=dict(l=0, r=0, t=40 if title else 10, b=0), showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=p["muted"]), hoverlabel=dict(font_size=13), **layout,
    )
    fig.update_xaxes(showgrid=False, linecolor=p["grid"], zeroline=False)
    fig.update_yaxes(gridcolor=p["grid"], zeroline=False)
    return fig
