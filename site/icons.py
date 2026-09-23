"""Deltakura in-house icon set.

One line set on a 24 px grid: 1.5 px stroke, round caps and joins,
``currentColor``, no fills. Standard library only; every icon is an inline
SVG string, so nothing is fetched and the CSP (``img-src 'self' data:``,
no inline style) is untouched: size and colour come from CSS classes.

Rule: an icon labels a real concept that sits next to it in text. None is
decorative, so every icon is ``aria-hidden`` and the adjacent text carries the
meaning.
"""

from __future__ import annotations

# name -> SVG body (paths / circles) on a 0 0 24 24 grid.
ICONS = {
    # 蔵 storehouse: gabled roof, plaster band, door. The brand concept.
    "kura": (
        '<path d="M2.5 9.5 12 4l9.5 5.5"/><path d="M5 9v11h14V9"/>'
        '<path d="M5 13.5h14"/><path d="M10 20v-3.5h4V20"/>'
    ),
    # 帳簿 ledger: side-stitched book with ruled lines.
    "ledger": (
        '<path d="M6.5 3h11a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1h-11z"/>'
        '<path d="M4 6.5h2.5M4 10.5h2.5M4 14.5h2.5M4 18.5h2.5"/>'
        '<path d="M10 8h5.5M10 12h5.5M10 16h3.5"/>'
    ),
    # 札 tender tag: a hanging wooden tag.
    "tag": (
        '<path d="M8 3h8l2 3.5V20a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V6.5z"/>'
        '<circle cx="12" cy="7" r="1.2"/><path d="M9 12h6M9 15.5h6"/>'
    ),
    # calendar with "40": the upstream retention window.
    "cal40": (
        '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 9.5h18"/>'
        '<path d="M8.5 13v3h3M10.5 13v5.5"/>'
        '<path d="M15 13a1.5 1.5 0 0 1 1.5 1.5v2.5a1.5 1.5 0 0 1-3 0v-2.5A1.5 1.5 0 0 1 15 13z"/>'
    ),
    # 判子 stamp.
    "stamp": (
        '<path d="M12 3a2.5 2.5 0 0 1 2.5 2.5c0 1.6-1 2.6-1 4.5H17l1 4H6l1-4h3.5c0-1.9-1-2.9-1-4.5A2.5 2.5 0 0 1 12 3z"/>'
        '<path d="M5 20h14"/>'
    ),
    # nightly: crescent moon.
    "moon": '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
    # 国の公開データ: a public building.
    "gov": (
        '<path d="M3 9 12 4l9 5"/><path d="M5.5 10v8M10 10v8M14 10v8M18.5 10v8"/>'
        '<path d="M3 20.5h18"/>'
    ),
    # a web page (the site as a delivery channel).
    "page": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 12h6M9 15.5h6"/>',
    "rss": (
        '<path d="M5 11a8 8 0 0 1 8 8"/><path d="M5 5a14 14 0 0 1 14 14"/>'
        '<circle cx="6" cy="18" r="1.3"/>'
    ),
    # data file (JSON).
    "braces": (
        '<path d="M8.5 4C6.5 4 6.5 5 6.5 7v2c0 1.5-1 2.2-2.5 3 1.5.8 2.5 1.5 2.5 3v2c0 2 0 3 2 3"/>'
        '<path d="M15.5 4c2 0 2 1 2 3v2c0 1.5 1 2.2 2.5 3-1.5.8-2.5 1.5-2.5 3v2c0 2 0 3-2 3"/>'
    ),
    # MCP / agent connection: a plug.
    "plug": '<path d="M9 3v5M15 3v5"/><path d="M7 8h10v3a5 5 0 0 1-10 0z"/><path d="M12 16v5"/>',
    "shield": '<path d="M12 3l7 3v5.5c0 4.5-3 7.8-7 9.5-4-1.7-7-5-7-9.5V6z"/><path d="m9 12 2 2 4-4"/>',
    "mail-off": (
        '<rect x="3" y="6" width="18" height="12" rx="1.5"/><path d="m3.5 7 8.5 6.5L20.5 7"/>'
        '<path d="M3 3l18 18"/>'
    ),
    "cookie-off": (
        '<path d="M20.5 12A8.5 8.5 0 1 1 12 3.5a3 3 0 0 0 3 3 3 3 0 0 0 3 3 2.5 2.5 0 0 0 2.5 2.5z"/>'
        '<path d="M9 10.5h.01M14.5 15h.01M9.5 15.5h.01"/><path d="M3 3l18 18"/>'
    ),
    "eye-off": (
        '<path d="M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z"/>'
        '<circle cx="12" cy="12" r="2.5"/><path d="M4 4l16 16"/>'
    ),
    # 入力欄なし: an input box, struck through.
    "field-off": '<rect x="3" y="8" width="18" height="8" rx="1.5"/><path d="M6.5 12h4"/><path d="M4 4l16 16"/>',
    "code": '<path d="m8 7-5 5 5 5"/><path d="m16 7 5 5-5 5"/><path d="m14 4-4 16"/>',
    # hiring (the not-yet-live product): person with a briefcase.
    "hiring": (
        '<circle cx="9" cy="7" r="3"/><path d="M3 20v-.5a6 6 0 0 1 8.5-5.5"/>'
        '<rect x="14" y="14" width="7" height="6" rx="1"/><path d="M16 14v-1.5h3V14"/>'
    ),
    # 計測は25時間で消える: a clock.
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
}


def icon(name: str, cls: str = "ic") -> str:
    """Inline SVG for one icon. Decorative-by-adjacency: aria-hidden."""
    body = ICONS[name]
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" width="24" height="24" fill="none" '
        'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true" focusable="false">{body}</svg>'
    )
