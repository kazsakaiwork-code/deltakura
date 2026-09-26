"""Deltakura data graphics: the 40-day strip and the small charts.

Standard library only. Every graphic is an inline SVG built at build time
from the published numbers; there is no chart library and no script.

Most SVGs here have NO viewBox. Horizontal positions are percentages and
vertical positions are pixels, so a chart stretches to any column width while
its text stays at the real CSS font size. That is what keeps the strip and the
charts readable at 360 px without a horizontal scroller. (Rects, lines and
text accept percentage coordinates; paths do not, so none is used there.)

The CSP forbids inline style, so colour, size and motion all come from CSS
classes defined in theme.py. Every chart has role="img" with <title>/<desc>,
and every figure also carries its numbers as text next to it.
"""

from __future__ import annotations

import html
import math

from icons import icon


def _e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _t(lang: str, ja: str, en: str) -> str:
    return ja if lang == "ja" else en


def _f(x: float) -> str:
    """Compact number for an SVG attribute."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def wcls(value: float, vmax: float) -> str:
    """Width class w0..w100 (theme.py defines them) for an HTML bar."""
    if vmax <= 0:
        return "w0"
    n = int(round(100 * value / vmax))
    if value > 0:
        n = max(n, 1)
    return f"w{min(100, max(0, n))}"


# --------------------------------------------------------------------------
# The signature: the 40-day strip
# --------------------------------------------------------------------------


def strip_figure(lang, days, daily, total, *, uid="strip", window=40, fade=5,
                 oldest_upstream=None, checked_on=None):
    """Two aligned rows of day cells: the official source and the storehouse.

    Top row: the publisher keeps only the last `window` publication days.
    Cells older than that are drawn as dashed outlines (消えた日). The oldest
    `fade` cells still inside the window are drawn fading: they are the next
    to go. Bottom row: every day we have kept, height = that day's count,
    ending in the 保管済 stamp with the running total.

    `oldest_upstream` (an ISO date an editor read off the publisher's listing,
    on `checked_on`) overrides the window arithmetic: kept days before it are
    drawn as gone. Without it, "gone" is derived from the archive alone.
    """
    n = len(days)
    if n == 0:
        return ""
    vals = [daily[d] for d in days]
    vmax = max(vals) or 1
    H = 46  # cell zone height in px
    slot = 100.0 / n
    gap = slot * 0.24
    w = slot - gap
    start = max(0, n - window)  # first index still on the official site
    if oldest_upstream:
        start = next((i for i, d in enumerate(days) if d >= oldest_upstream), n)
    gone = start

    def cell(i, v, cls, title=None):
        h = max(3.0, H * v / vmax)
        x = i * slot + gap / 2
        inner = f"<title>{_e(title)}</title>" if title else ""
        return (
            f'<rect class="{cls}" x="{_f(x)}%" y="{_f(H - h)}" width="{_f(w)}%" '
            f'height="{_f(h)}" rx="1">{inner}</rect>'
        )

    src = []
    for i, v in enumerate(vals):
        if i < start:
            cls = "c-gone"
        elif i < start + fade:
            cls = f"c-next f{i - start + 1}"
        else:
            cls = "c-src"
        src.append(cell(i, v, cls))

    # bracket under the official row: the window that still exists upstream
    bx0 = start * slot + gap / 2
    by = H + 9
    bracket = (
        f'<line class="st-br" x1="{_f(bx0)}%" y1="{by}" x2="{_f(100 - gap / 2)}%" y2="{by}"/>'
        f'<line class="st-br" x1="{_f(bx0)}%" y1="{by - 5}" x2="{_f(bx0)}%" y2="{by + 5}"/>'
        f'<line class="st-br" x1="{_f(100 - gap / 2)}%" y1="{by - 5}" x2="{_f(100 - gap / 2)}%" y2="{by + 5}"/>'
    )
    mid = (bx0 + 100) / 2
    blabel = _t(lang, f"公式に残るのは直近{window}日", f"only the last {window} days exist upstream")
    bracket += (
        f'<text class="st-lab st-bl" x="{_f(mid)}%" y="{by + 4}" text-anchor="middle">{_e(blabel)}</text>'
    )

    kura = [
        cell(
            i,
            v,
            "c-kura",
            _t(lang, f"{d}: {v:,}件", f"{d}: {v:,} records"),
        )
        for i, (d, v) in enumerate(zip(days, vals))
    ]
    dates = (
        f'<text class="st-date" x="0" y="{H + 17}" text-anchor="start">{_e(days[0])}</text>'
        f'<text class="st-date" x="100%" y="{H + 17}" text-anchor="end">{_e(days[-1])}</text>'
    )

    src_title = _t(
        lang,
        f"公式サイト（国税庁）: 直近{window}日分の日次差分だけが残っています",
        f"Official source (National Tax Agency): only the last {window} daily files exist",
    )
    src_desc = (
        _t(
            lang,
            f"{gone}日分はすでに公式から消えています。薄い{fade}日が次に消えます。",
            f"{gone} days are already gone upstream. The {fade} faded days are next.",
        )
        if gone
        else _t(
            lang,
            f"いちばん古い{fade}日を薄く表示しています。新しい日が増えるたびに、古い日から消えます。",
            f"The oldest {fade} days are drawn faded: each new day pushes the oldest one out.",
        )
    )
    kura_title = _t(
        lang,
        f"Deltakura 蔵: {days[0]}〜{days[-1]} の{n}日分、{total:,}件を保管",
        f"Deltakura storehouse: {n} days kept, {days[0]} to {days[-1]}, {total:,} records",
    )
    kura_desc = _t(
        lang,
        "1本が1日。高さはその日の件数です。",
        "One cell per day; height is that day's record count.",
    )

    legend = [
        ("sw-src", _t(lang, "公式に残っている日", "still on the official site")),
        ("sw-next", _t(lang, "次に消える日", "next to disappear")),
    ]
    if gone:
        legend.append(("sw-gone", _t(lang, f"公式から消えた日（{gone}日）", f"gone upstream ({gone} days)")))
    legend.append(("sw-kura", _t(lang, "蔵に保管した日", "kept in the storehouse")))
    legend_html = "".join(
        f'<li><span class="sw {c}" aria-hidden="true"></span>{_e(label)}</li>' for c, label in legend
    )

    caption = _t(
        lang,
        f"公式サイトは直近{window}日分だけを残し、{window + 1}日目から消えます。蔵には全日が残ります。",
        f"The official site keeps only the last {window} days; from day {window + 1} a file is gone. "
        "The storehouse keeps every day.",
    )
    if oldest_upstream:
        caption += _t(
            lang,
            f"公式サイトの一覧は {oldest_upstream} 分から" + (f"（{checked_on} 確認）。" if checked_on else "。"),
            f" The official listing starts at {oldest_upstream}" + (f" (checked {checked_on})." if checked_on else "."),
        )

    return f"""<figure class="strip" aria-labelledby="{uid}-cap">
<div class="st-grid">
<div class="st-lab-row st-src-lab">{icon("gov")}<span><b>{_e(_t(lang, "公式サイト", "Official site"))}</b><small>{_e(_t(lang, "国税庁", "National Tax Agency"))}</small></span></div>
<div class="st-plot st-src"><svg width="100%" height="{H + 22}" role="img" aria-labelledby="{uid}-t1 {uid}-d1"><title id="{uid}-t1">{_e(src_title)}</title><desc id="{uid}-d1">{_e(src_desc)}</desc>{"".join(src)}{bracket}</svg></div>
<div class="st-end"></div>
<div class="st-lab-row st-kura-lab">{icon("kura")}<span><b>{_e(_t(lang, "Deltakura 蔵", "Deltakura"))}</b><small>{_e(_t(lang, "全日を保管", "every day kept"))}</small></span></div>
<div class="st-plot st-kura"><svg width="100%" height="{H + 22}" role="img" aria-labelledby="{uid}-t2 {uid}-d2"><title id="{uid}-t2">{_e(kura_title)}</title><desc id="{uid}-d2">{_e(kura_desc)}</desc>{"".join(kura)}{dates}</svg></div>
<div class="st-end"><div class="stamp" role="img" aria-label="{_e(_t(lang, f"保管済 {total:,}件", f"Kept: {total:,} records"))}"><span class="stamp-k">{_e(_t(lang, "保管済", "KEPT"))}</span><span class="stamp-n">{total:,}<small>{_e(_t(lang, "件", " rec."))}</small></span></div></div>
</div>
<figcaption id="{uid}-cap"><span class="st-cap">{_e(caption)}</span><ul class="st-legend">{legend_html}</ul></figcaption>
</figure>"""


# --------------------------------------------------------------------------
# Small multiples for the product cards
# --------------------------------------------------------------------------


def heat_thumb(lang, grid, cls_of):
    """A tiny heatmap: rows x cols of class names (h0..h6). No text inside."""
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    if not rows or not cols:
        return ""
    rects = []
    for ri, row in enumerate(grid):
        for ci, v in enumerate(row):
            rects.append(
                f'<rect class="{cls_of(v)}" x="{ci * 10 + 0.4}" y="{ri * 10 + 0.8}" width="9.2" height="8.4"/>'
            )
    title = _t(
        lang,
        "府省 × 年度の落札件数の濃淡（縮小図）",
        "Award counts by ministry and fiscal year, as shades (thumbnail)",
    )
    return (
        f'<svg class="mini hm" viewBox="0 0 {cols * 10} {rows * 10}" preserveAspectRatio="none" '
        f'role="img" aria-label="{_e(title)}"><title>{_e(title)}</title>{"".join(rects)}</svg>'
    )


def spark_bars(lang, days, daily):
    """40 thin bars: the daily registry-diff counts, for the card."""
    n = len(days)
    if not n:
        return ""
    vals = [daily[d] for d in days]
    vmax = max(vals) or 1
    rects = "".join(
        f'<rect x="{i * 4 + 0.5}" y="{_f(40 - 40 * v / vmax)}" width="3" height="{_f(40 * v / vmax)}"/>'
        for i, v in enumerate(vals)
    )
    title = _t(
        lang,
        f"{days[0]}〜{days[-1]} の日ごとの件数（{n}日分）",
        f"Records per day, {days[0]} to {days[-1]} ({n} days)",
    )
    return (
        f'<svg class="mini sp" viewBox="0 0 {n * 4} 40" preserveAspectRatio="none" role="img" '
        f'aria-label="{_e(title)}"><title>{_e(title)}</title>{rects}</svg>'
    )


# --------------------------------------------------------------------------
# Bet A detail: box plot on a log scale
# --------------------------------------------------------------------------

_DECADE_JA = ["1円", "10円", "100円", "1,000円", "1万", "10万", "100万", "1,000万",
              "1億", "10億", "100億", "1,000億", "1兆", "10兆"]
_DECADE_EN = ["¥1", "¥10", "¥100", "¥1k", "¥10k", "¥100k", "¥1M", "¥10M",
              "¥100M", "¥1B", "¥10B", "¥100B", "¥1T", "¥10T"]


def box_plot(lang, vmin, q1, med, q3, vmax, *, estimated=False, uid="bx"):
    """Horizontal box plot of award prices, log10 scale, median labelled.

    `uid` keeps the <title>/<desc> ids unique when a page carries several.
    """
    vals = [max(1, int(v or 1)) for v in (vmin, q1, med, q3, vmax)]
    lo = math.floor(math.log10(vals[0]))
    hi = math.ceil(math.log10(vals[4]))
    if hi <= lo:
        hi = lo + 1

    def x(v):
        return 3 + 94 * (math.log10(v) - lo) / (hi - lo)

    xmin, xq1, xmed, xq3, xmax = (x(v) for v in vals)
    cy = 40
    parts = [
        f'<line class="bx-wh" x1="{_f(xmin)}%" y1="{cy}" x2="{_f(xq1)}%" y2="{cy}"/>',
        f'<line class="bx-wh" x1="{_f(xq3)}%" y1="{cy}" x2="{_f(xmax)}%" y2="{cy}"/>',
        f'<line class="bx-wh" x1="{_f(xmin)}%" y1="{cy - 7}" x2="{_f(xmin)}%" y2="{cy + 7}"/>',
        f'<line class="bx-wh" x1="{_f(xmax)}%" y1="{cy - 7}" x2="{_f(xmax)}%" y2="{cy + 7}"/>',
        f'<rect class="bx-box" x="{_f(xq1)}%" y="{cy - 13}" width="{_f(max(0.4, xq3 - xq1))}%" height="26" rx="2"/>',
        f'<line class="bx-med" x1="{_f(xmed)}%" y1="{cy - 16}" x2="{_f(xmed)}%" y2="{cy + 16}"/>',
    ]
    anchor = "middle"
    if xmed < 18:
        anchor = "start"
    elif xmed > 82:
        anchor = "end"
    mlabel = _t(lang, "中央値 ", "median ") + "¥{:,}".format(vals[2]) + ("†" if estimated else "")
    parts.append(
        f'<text class="bx-ml" x="{_f(xmed)}%" y="{cy - 21}" text-anchor="{anchor}">{_e(mlabel)}</text>'
    )
    # decade ticks, thinned so that no more than 6 carry a label
    span = hi - lo
    step = max(1, math.ceil(span / 6))
    names = _DECADE_JA if lang == "ja" else _DECADE_EN
    ty = cy + 26
    for k in range(lo, hi + 1):
        xx = x(10 ** k)
        parts.append(f'<line class="bx-tk" x1="{_f(xx)}%" y1="{ty}" x2="{_f(xx)}%" y2="{ty + 4}"/>')
        if (k - lo) % step == 0 or k == hi:
            if k == hi and (k - lo) % step != 0:
                continue
            a = "start" if k == lo else ("end" if k == hi else "middle")
            name = names[k] if 0 <= k < len(names) else f"1e{k}"
            parts.append(f'<text class="bx-tl" x="{_f(xx)}%" y="{ty + 17}" text-anchor="{a}">{_e(name)}</text>')
    parts.insert(0, f'<line class="bx-ax" x1="3%" y1="{ty}" x2="97%" y2="{ty}"/>')

    title = _t(lang, "落札価格の分布（箱ひげ図・対数目盛）", "Award price distribution (box plot, log scale)")
    desc = _t(
        lang,
        f"最小 ¥{vals[0]:,}、第1四分位 ¥{vals[1]:,}、中央値 ¥{vals[2]:,}、第3四分位 ¥{vals[3]:,}、最大 ¥{vals[4]:,}",
        f"min ¥{vals[0]:,}, Q1 ¥{vals[1]:,}, median ¥{vals[2]:,}, Q3 ¥{vals[3]:,}, max ¥{vals[4]:,}",
    )
    return (
        f'<svg class="bx" width="100%" height="{ty + 24}" role="img" aria-labelledby="{uid}-t {uid}-d">'
        f'<title id="{uid}-t">{_e(title)}</title><desc id="{uid}-d">{_e(desc)}</desc>'
        + "".join(parts)
        + "</svg>"
    )


# --------------------------------------------------------------------------
# Bet C: records per publication day
# --------------------------------------------------------------------------


def _month_ticks(days, slot, height, label_pct=15.0):
    """x-axis labels: the first and last day, plus the first day of each month.

    A month label that would overlap a neighbour is dropped (the first and last
    day always stay: they are the period). `label_pct` is a label's width as a
    share of the plot at a 360 px screen, where "07-24" is about 15% wide.
    """
    n = len(days)
    ticks = []  # (index, anchor, x%)
    seen = set()
    for i, d in enumerate(days):
        if d[:7] in seen and i != n - 1:
            continue
        seen.add(d[:7])
        if i == n - 1:
            ticks.append((i, "end", 100.0))
        elif i == 0:
            ticks.append((i, "start", 0.0))
        else:
            ticks.append((i, "middle", i * slot + slot / 2))

    def extent(anchor, x):
        if anchor == "start":
            return x, x + label_pct
        if anchor == "end":
            return x - label_pct, x
        return x - label_pct / 2, x + label_pct / 2

    last = ticks[-1] if n > 1 else None
    kept = []
    for t in ticks:
        lo, hi = extent(t[1], t[2])
        if t is not last and t[0] != 0:
            if kept and lo < extent(kept[-1][1], kept[-1][2])[1]:
                continue
            if last and hi > extent(last[1], last[2])[0]:
                continue
        kept.append(t)
    return [
        f'<text class="dc-xl" x="{_f(x)}%" y="{height - 6}" text-anchor="{a}">{_e(days[i][5:])}</text>'
        for i, a, x in kept
    ]


def daily_chart(lang, days, values, height=180):
    n = len(days)
    if not n:
        return ""
    top_pad, bot_pad = 18, 24
    plot_h = height - top_pad - bot_pad
    vmax = max(values)
    step = 10 ** (len(str(int(vmax))) - 1)
    top = int((vmax // step + 1) * step)
    slot = 100.0 / n
    bw = slot * 0.7
    base = top_pad + plot_h

    grid = []
    unit = _t(lang, "件", " records")
    for frac in (0.5, 1.0):
        y = base - plot_h * frac
        grid.append(f'<line class="dc-grid" x1="0" y1="{_f(y)}" x2="100%" y2="{_f(y)}"/>')
    # one scale label, above the top gridline where no bar can reach it
    grid.append(
        f'<text class="dc-yl" x="0" y="{_f(top_pad - 5)}">{top:,}{_e(unit)}'
        f'<tspan class="dc-sub">{_e(_t(lang, "（点線は半分）", " (dotted line = half)"))}</tspan></text>'
    )
    grid.append(f'<line class="dc-base" x1="0" y1="{base}" x2="100%" y2="{base}"/>')

    bars = []
    for i, (d, v) in enumerate(zip(days, values)):
        h = plot_h * v / top
        x = i * slot + (slot - bw) / 2
        label = _t(lang, f"{d}: {v:,}件", f"{d}: {v:,} records")
        bars.append(
            f'<rect class="dc-bar" x="{_f(x)}%" y="{_f(base - h)}" width="{_f(bw)}%" height="{_f(h)}" rx="1">'
            f"<title>{_e(label)}</title></rect>"
        )

    xl = _month_ticks(days, slot, height)

    title = _t(
        lang,
        f"{days[0]}〜{days[-1]} の公表日ごとの差分件数",
        f"Registry diff records per publication day, {days[0]} to {days[-1]}",
    )
    return (
        f'<svg class="dc" width="100%" height="{height}" role="img" aria-label="{_e(title)}">'
        f"<title>{_e(title)}</title>" + "".join(bars) + "".join(grid) + "".join(xl) + "</svg>"
    )


def hbars(rows, *, total=None, fmt=None):
    """HTML horizontal bars: [(label_html, value)] -> <ul>. Bar widths are classes.

    `fmt` formats the value text (default: thousands separators).
    """
    if not rows:
        return ""
    vmax = max(v for _, v in rows) or 1
    items = []
    for label, v in rows:
        share = ""
        if total:
            pc = 100 * v / total
            share = "<small>{}</small>".format("&lt;0.1%" if 0 < pc < 0.05 else f"{pc:.1f}%")
        items.append(
            f'<li><span class="hb-k">{label}</span>'
            f'<span class="hb-bar" aria-hidden="true"><span class="hb-fill {wcls(v, vmax)}"></span></span>'
            f'<span class="hb-v">{_e(fmt(v)) if fmt else f"{v:,}"}{share}</span></li>'
        )
    return f'<ul class="hbars">{"".join(items)}</ul>'


# --------------------------------------------------------------------------
# Articles: one value per fiscal year
# --------------------------------------------------------------------------


def year_bars(lang, rows, *, unit_ja="件", unit_en=" awards", uid="yb", height=200):
    """Vertical bars, one per fiscal year: rows = [(fiscal_year, value)].

    Same construction as daily_chart (percentage x, pixel y, no viewBox), so it
    fits any column width. Each bar carries its value as <title>, and the
    caller prints the numbers as text as well.
    """
    n = len(rows)
    if not n:
        return ""
    top_pad, bot_pad = 18, 24
    plot_h = height - top_pad - bot_pad
    vmax = max(v for _, v in rows) or 1
    step = 10 ** (len(str(int(vmax))) - 1)
    top = int((vmax // step + 1) * step)
    slot = 100.0 / n
    bw = slot * 0.62
    base = top_pad + plot_h
    unit = _t(lang, unit_ja, unit_en)
    parts = []
    for frac in (0.5, 1.0):
        y = base - plot_h * frac
        parts.append(f'<line class="dc-grid" x1="0" y1="{_f(y)}" x2="100%" y2="{_f(y)}"/>')
    parts.append(
        f'<text class="dc-yl" x="0" y="{_f(top_pad - 5)}">{top:,}{_e(unit)}'
        f'<tspan class="dc-sub">{_e(_t(lang, "（点線は半分）", " (dotted line = half)"))}</tspan></text>'
    )
    parts.append(f'<line class="dc-base" x1="0" y1="{base}" x2="100%" y2="{base}"/>')
    bars, labels = [], []
    every = 1 if n <= 8 else 2
    for i, (fy, v) in enumerate(rows):
        h = plot_h * v / top
        x = i * slot + (slot - bw) / 2
        label = _t(lang, f"{fy}年度: {v:,}{unit_ja}", f"FY{fy}: {v:,}{unit_en}")
        bars.append(
            f'<rect class="dc-bar" x="{_f(x)}%" y="{_f(base - h)}" width="{_f(bw)}%" height="{_f(h)}" rx="1">'
            f"<title>{_e(label)}</title></rect>"
        )
        if i % every == 0 or i == n - 1:
            labels.append(
                f'<text class="dc-xl" x="{_f(i * slot + slot / 2)}%" y="{height - 6}" text-anchor="middle">'
                f"{_e(str(fy)[2:] if n > 8 else fy)}</text>"
            )
    first, last = rows[0][0], rows[-1][0]
    title = _t(lang, f"{first}〜{last}年度の年度別の件数", f"Per fiscal year, FY{first} to FY{last}")
    desc = "; ".join(_t(lang, f"{fy}年度 {v:,}", f"FY{fy} {v:,}") for fy, v in rows)
    return (
        f'<svg class="dc" width="100%" height="{height}" role="img" aria-labelledby="{uid}-t {uid}-d">'
        f'<title id="{uid}-t">{_e(title)}</title><desc id="{uid}-d">{_e(desc)}</desc>'
        + "".join(bars) + "".join(parts) + "".join(labels) + "</svg>"
    )


# --------------------------------------------------------------------------
# Articles: records per publication day, stacked by change type
# --------------------------------------------------------------------------


def stacked_daily(lang, days, series, *, uid="sd", height=220, mean=None):
    """Stacked bars, one per publication day.

    series: [(class_suffix, label, {day: value})], bottom to top. Classes
    sd-<suffix> colour the segments (theme.py); the legend and its totals are
    returned as HTML so the numbers are also text.
    """
    n = len(days)
    if not n or not series:
        return "", ""
    totals = [sum(s[2].get(d, 0) for s in series) for d in days]
    vmax = max(totals) or 1
    step = 10 ** (len(str(int(vmax))) - 1)
    top = int((vmax // step + 1) * step)
    top_pad, bot_pad = 18, 24
    plot_h = height - top_pad - bot_pad
    base = top_pad + plot_h
    slot = 100.0 / n
    bw = slot * 0.7
    unit = _t(lang, "件", " records")

    parts = []
    for i, d in enumerate(days):
        y = float(base)
        x = i * slot + (slot - bw) / 2
        for cls, label, values in series:
            v = values.get(d, 0)
            if not v:
                continue
            h = plot_h * v / top
            y -= h
            parts.append(
                f'<rect class="sd-{cls}" x="{_f(x)}%" y="{_f(y)}" width="{_f(bw)}%" height="{_f(h)}">'
                f"<title>{_e(f'{d} {label}: {v:,}')}</title></rect>"
            )
    grid = []
    for frac in (0.5, 1.0):
        gy = base - plot_h * frac
        grid.append(f'<line class="dc-grid" x1="0" y1="{_f(gy)}" x2="100%" y2="{_f(gy)}"/>')
    grid.append(
        f'<text class="dc-yl" x="0" y="{_f(top_pad - 5)}">{top:,}{_e(unit)}'
        f'<tspan class="dc-sub">{_e(_t(lang, "（点線は半分）", " (dotted line = half)"))}</tspan></text>'
    )
    grid.append(f'<line class="dc-base" x1="0" y1="{base}" x2="100%" y2="{base}"/>')
    if mean:
        my = base - plot_h * mean / top
        grid.append(f'<line class="sd-mean" x1="0" y1="{_f(my)}" x2="100%" y2="{_f(my)}"/>')
        grid.append(
            f'<text class="sd-ml" x="100%" y="{_f(my - 5)}" text-anchor="end">'
            f'{_e(_t(lang, f"平均 {mean:,.0f}件", f"mean {mean:,.0f}"))}</text>'
        )
    xl = _month_ticks(days, slot, height)

    title = _t(lang, f"{days[0]}〜{days[-1]} の公表日ごとの件数（処理区分別）",
               f"Records per publication day by change type, {days[0]} to {days[-1]}")
    desc = "; ".join(f"{label}: {sum(values.values()):,}" for _, label, values in series)
    svg = (
        f'<svg class="dc" width="100%" height="{height}" role="img" aria-labelledby="{uid}-t {uid}-d">'
        f'<title id="{uid}-t">{_e(title)}</title><desc id="{uid}-d">{_e(desc)}</desc>'
        + "".join(parts) + "".join(grid) + "".join(xl) + "</svg>"
    )
    grand = sum(totals) or 1
    legend = '<ul class="st-legend sd-legend">' + "".join(
        f'<li><span class="sw sd-{cls}" aria-hidden="true"></span>{_e(label)} '
        f'<span class="num">{sum(values.values()):,}（{100 * sum(values.values()) / grand:.1f}%）</span></li>'
        for cls, label, values in reversed(series)
    ) + "</ul>"
    return svg, legend
