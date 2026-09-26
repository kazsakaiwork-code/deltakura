"""Deltakura articles: front matter and a small Markdown subset, standard library only.

An article is one UTF-8 Markdown file under ``site/content/articles/``:

    ---
    slug: ministry-award-counts-fy2013-fy2026
    title: 省庁別の落札件数の推移 FY2013-FY2026
    date: 2026-10-05
    updated: 2026-10-06          # optional; dateModified, defaults to date
    description: 一文の要約。一覧、検索結果、フィードに出ます。
    description_en: One-line English summary.   # optional
    lang: ja                     # ja or en
    product: bet_a               # optional: bet_a (default) or bet_c, picks the notify button
    draft: false                 # optional; true keeps the file out of the build
    sources:
      - 出典：調達ポータル（https://www.p-portal.go.jp/）
      - 出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/download/sabun/）
    ---

    本文（Markdown）

Supported Markdown, and nothing else (anything unsupported fails the build
instead of rendering oddly):

  * headings ``#`` .. ``####`` (``#`` and ``##`` both become <h2>: the page
    title is the only <h1>);
  * paragraphs; a line ending in two spaces or a backslash is a line break;
  * unordered (``-``, ``*``, ``+``) and ordered (``1.``) lists, nested by
    indentation;
  * GitHub-style pipe tables, with ``:---:`` / ``---:`` alignment; a column
    whose cells are all numbers is right-aligned in the data face;
  * links ``[text](https://...)`` or site-relative ``(/ja/...)``, autolinks
    ``<https://...>`` and bare https URLs; ``**strong**``, ``*em*``, `` `code` ``;
  * ``> `` quotes, ``---`` rules, fenced code blocks;
  * ``{{chart:<id>}}`` or ``{{chart:<id>:<arg>...}}`` alone on a line: a
    chart built at build time from the published data (see build.py,
    ``article_charts``);
  * ``<!-- ... -->`` comments are notes for editors: removed before
    rendering, placeholders inside them included.

Raw HTML is never passed through: every character of the source is escaped,
so an article cannot add a script, a style attribute or a form.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


class ArticleError(ValueError):
    """A problem in an article file. The build fails with the file and line."""


REQUIRED = ("slug", "title", "date", "description", "lang", "sources")
KNOWN = set(REQUIRED) | {"updated", "description_en", "product", "draft", "tags"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


# --------------------------------------------------------------------------
# Front matter: a YAML subset (scalars, lists of scalars, lists of mappings)
# --------------------------------------------------------------------------


def _scalar(raw: str):
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("", "null", "~"):
        return None
    return v


def _strip_comment(line: str) -> str:
    # A " #" outside quotes starts a comment. Values here rarely quote a "#".
    out, quote = [], ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and (i == 0 or line[i - 1] in " :-["):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] == " "):
            break
        out.append(ch)
    return "".join(out).rstrip()


def parse_front_matter(text: str, where: str = "article") -> Tuple[Dict, str, int]:
    """Return (meta, body, body_first_line_number)."""
    text = text.lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ArticleError(f"{where}: must start with a '---' front-matter block")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
    except StopIteration:
        raise ArticleError(f"{where}: front matter is not closed with '---'") from None

    meta: Dict = {}
    key: Optional[str] = None
    for n in range(1, end):
        raw = _strip_comment(lines[n].rstrip("\r"))
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        if indent == 0:
            m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", body)
            if not m:
                raise ArticleError(f"{where}:{n + 1}: expected 'key: value'")
            key, value = m.group(1), m.group(2)
            meta[key] = _scalar(value) if value.strip() else []
            continue
        if key is None:
            raise ArticleError(f"{where}:{n + 1}: indented line outside a key")
        if body.startswith("- ") or body == "-":
            item = body[1:].strip()
            m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.+)$", item)
            if m and not re.match(r"^https?:", item):
                meta[key].append({m.group(1): _scalar(m.group(2))})
            else:
                meta[key].append(_scalar(item))
        else:
            # continuation of a mapping item: "    url: https://..."
            m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.+)$", body)
            if not m or not isinstance(meta.get(key), list) or not meta[key] or not isinstance(meta[key][-1], dict):
                raise ArticleError(f"{where}:{n + 1}: cannot read this front-matter line")
            meta[key][-1][m.group(1)] = _scalar(m.group(2))
    return meta, "\n".join(lines[end + 1:]), end + 2


def _source_text(item) -> str:
    if isinstance(item, dict):
        name = item.get("name") or item.get("title") or item.get("label") or ""
        url = item.get("url") or ""
        note = item.get("note") or item.get("license") or ""
        parts = [str(x) for x in (name, f"（{url}）" if url and name else url, f" {note}" if note else "") if x]
        return "".join(parts).strip()
    return str(item).strip()


def validate(meta: Dict, where: str) -> Dict:
    missing = [k for k in REQUIRED if meta.get(k) in (None, "", [])]
    if missing:
        raise ArticleError(f"{where}: missing front matter: {', '.join(missing)}")
    unknown = sorted(set(meta) - KNOWN)
    if unknown:
        raise ArticleError(f"{where}: unknown front-matter key(s): {', '.join(unknown)}")
    slug = str(meta["slug"])
    if not SLUG_RE.match(slug) or len(slug) > 80:
        raise ArticleError(f"{where}: slug must be lower-case a-z, 0-9 and hyphens: {slug!r}")
    lang = str(meta["lang"])
    if lang not in ("ja", "en"):
        raise ArticleError(f"{where}: lang must be ja or en, not {lang!r}")
    dates = {}
    for k in ("date", "updated"):
        v = meta.get(k)
        if v in (None, ""):
            continue
        v = str(v)
        if not DATE_RE.match(v):
            raise ArticleError(f"{where}: {k} must be YYYY-MM-DD, not {v!r}")
        try:
            dates[k] = dt.date.fromisoformat(v)
        except ValueError:
            raise ArticleError(f"{where}: {k} is not a real date: {v!r}") from None
    published = dates["date"]
    updated = dates.get("updated", published)
    if updated < published:
        raise ArticleError(f"{where}: updated ({updated}) is before date ({published})")
    sources = meta["sources"] if isinstance(meta["sources"], list) else [meta["sources"]]
    sources = [s for s in (_source_text(x) for x in sources) if s]
    if not sources:
        raise ArticleError(f"{where}: sources must list at least one source")
    product = str(meta.get("product") or "bet_a")
    if product not in ("bet_a", "bet_c"):
        raise ArticleError(f"{where}: product must be bet_a or bet_c, not {product!r}")
    return {
        "slug": slug,
        "title": str(meta["title"]).strip(),
        "date": published,
        "updated": updated,
        "description": str(meta["description"]).strip(),
        "description_en": str(meta.get("description_en") or "").strip(),
        "lang": lang,
        "product": product,
        "draft": meta.get("draft") is True,
        "sources": sources,
    }


def load_articles(directory: Path) -> List[Dict]:
    """Every non-draft article, newest first. Fails on any malformed file."""
    if not directory.is_dir():
        return []
    out, seen = [], {}
    for path in sorted(directory.glob("*.md")):
        where = path.name
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        meta, body, first_line = parse_front_matter(text, where)
        art = validate(meta, where)
        if art["draft"]:
            continue
        key = (art["lang"], art["slug"])
        if key in seen:
            raise ArticleError(f"{where}: slug {art['slug']!r} ({art['lang']}) is also used by {seen[key]}")
        seen[key] = where
        art.update({"file": where, "body": body, "body_line": first_line})
        out.append(art)
    out.sort(key=lambda a: (a["date"], a["updated"], a["slug"]), reverse=True)
    return out


# --------------------------------------------------------------------------
# Inline Markdown
# --------------------------------------------------------------------------

# A URL ends at whitespace, at Japanese punctuation or brackets, or at a quote.
_URL_CHARS = r"[^\s<>\"'（）()「」『』【】、。，．]+"
_INLINE = re.compile(
    r"(?P<code>`+)(?P<code_body>.+?)(?P=code)"
    r"|!\[(?P<img>[^\]]*)\]\([^)]*\)"
    r"|\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|<(?P<auto>https?://[^>\s]+)>"
    r"|\*\*(?P<strong>.+?)\*\*"
    r"|__(?P<strong2>.+?)__"
    r"|(?<![\w*])\*(?P<em>[^*\s](?:[^*]*[^*\s])?)\*(?![\w*])"
    r"|(?<![\w_])_(?P<em2>[^_\s](?:[^_]*[^_\s])?)_(?![\w_])"
    r"|(?P<bare>https?://" + _URL_CHARS + r")"
)


def safe_href(url: str) -> Optional[str]:
    url = url.strip()
    if re.match(r"^https?://[^\s]+$", url, re.I) or re.match(r"^(/(?!/)|#)[^\s]*$", url):
        return url
    return None


def _link(href: str, text_html: str) -> str:
    ext = href.lower().startswith(("http://", "https://"))
    rel = ' rel="noopener"' if ext else ""
    return f'<a href="{_e(href)}"{rel}>{text_html}</a>'


def inline(text: str, where: str = "", links: bool = True) -> str:
    if "{{" in text and "chart:" in text:
        raise ArticleError(f"{where}: a chart placeholder must be alone on its line: {{{{chart:<id>}}}}")
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        out.append(_e(text[pos:m.start()]))
        pos = m.end()
        g = m.groupdict()
        if g["code"]:
            out.append(f"<code>{_e(g['code_body'].strip())}</code>")
        elif g["img"] is not None:
            raise ArticleError(f"{where}: images are not supported; use {{{{chart:<id>}}}}")
        elif g["ltext"] is not None:
            href = safe_href(g["lurl"])
            label = inline(g["ltext"], where, links=False)
            out.append(_link(href, label) if (href and links) else label)
        elif g["auto"] or g["bare"]:
            url = (g["auto"] or g["bare"]).rstrip(".,;:!?")
            tail = (g["auto"] or g["bare"])[len(url):]
            href = safe_href(url)
            out.append(_link(href, _e(url)) if (href and links) else _e(url))
            out.append(_e(tail))
        elif g["strong"] is not None or g["strong2"] is not None:
            out.append(f"<strong>{inline(g['strong'] or g['strong2'], where, links)}</strong>")
        elif g["em"] is not None or g["em2"] is not None:
            out.append(f"<em>{inline(g['em'] or g['em2'], where, links)}</em>")
    out.append(_e(text[pos:]))
    return "".join(out)


# --------------------------------------------------------------------------
# Block Markdown
# --------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def strip_comments(text: str) -> str:
    """Drop <!-- --> editor notes, keeping line numbers (newlines survive)."""
    if "<!--" in text and "-->" not in text[text.index("<!--"):]:
        raise ArticleError("an HTML comment '<!--' is not closed with '-->'")
    return _COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


CHART_RE = re.compile(r"^\{\{\s*chart:([a-z0-9-]+)((?::[A-Za-z0-9_-]+)*)\s*\}\}$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
HR_RE = re.compile(r"^(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$")
ITEM_RE = re.compile(r"^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$")
TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
NUMERIC_RE = re.compile(r"^[−\-+]?[¥￥$]?\s?[\d,.]+\s?(%|％|件|円|日|倍|万|億|万円|億円|pt|ポイント)?[†*]?$|^[—–-]$")
_CJK = re.compile(r"[　-ヿ㐀-鿿＀-￯]")


def _join_lines(lines: List[str]) -> Tuple[str, List[int]]:
    """Join paragraph lines; returns text and the indices where a hard break goes."""
    text, breaks = "", []
    for i, raw in enumerate(lines):
        hard = raw.endswith("  ") or raw.endswith("\\")
        line = raw.rstrip("\\").strip()
        if text:
            if breaks and breaks[-1] == i - 1:
                text += "\x00"
            elif _CJK.search(text[-1:]) or _CJK.search(line[:1]):
                text += ""
            else:
                text += " "
        text += line
        if hard and i < len(lines) - 1:
            breaks.append(i)
    return text, breaks


def _para_html(lines: List[str], where: str) -> str:
    text, _ = _join_lines(lines)
    return "<br>".join(inline(part, where) for part in text.split("\x00"))


def _split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], "", 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if s[i] == "|":
            cells.append(cur.strip())
            cur = ""
        else:
            cur += s[i]
        i += 1
    cells.append(cur.strip())
    return cells


def _table_html(rows: List[str], where: str) -> str:
    head = _split_row(rows[0])
    seps = _split_row(rows[1])
    if len(seps) != len(head):
        raise ArticleError(f"{where}: table header has {len(head)} cells but the separator has {len(seps)}")
    align = []
    for sep in seps:
        s = sep.strip()
        align.append("n" if s.endswith(":") and not s.startswith(":") else ("c" if s.startswith(":") and s.endswith(":") else ""))
    body = [_split_row(r) for r in rows[2:]]
    for i, row in enumerate(body):
        if len(row) > len(head):
            raise ArticleError(f"{where}: table row {i + 1} has more cells than the header")
        row.extend([""] * (len(head) - len(row)))
    for c in range(len(head)):
        if not align[c] and body and all(NUMERIC_RE.match(r[c].replace("**", "")) for r in body if r[c]) and any(r[c] for r in body):
            align[c] = "n"
    cls = {"n": ' class="n"', "c": ' class="c"', "": ""}
    th = "".join(f'<th scope="col"{cls[a]}>{inline(h, where)}</th>' for h, a in zip(head, align))
    trs = "".join(
        "<tr>" + "".join(f"<td{cls[a]}>{inline(c, where)}</td>" for c, a in zip(r, align)) + "</tr>"
        for r in body
    )
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def _list_html(items: List[Tuple[int, bool, List[str]]], where: str) -> str:
    """items: (indent, ordered, lines). Nests by indentation."""
    out: List[str] = []
    stack: List[Tuple[int, str]] = []  # (indent, tag)
    for indent, ordered, lines in items:
        tag = "ol" if ordered else "ul"
        while stack and indent < stack[-1][0]:
            out.append(f"</li></{stack.pop()[1]}>")
        if not stack or indent > stack[-1][0]:
            out.append(f"<{tag}>")
            stack.append((indent, tag))
        else:
            out.append("</li>")
            if stack[-1][1] != tag:
                out.append(f"</{stack.pop()[1]}><{tag}>")
                stack.append((indent, tag))
        out.append("<li>" + _para_html(lines, where))
    while stack:
        out.append(f"</li></{stack.pop()[1]}>")
    return "".join(out)


def render_markdown(
    text: str,
    chart: Callable[[str, List[str]], str],
    where: str = "article",
    first_line: int = 1,
) -> Tuple[str, List[Tuple[str, str]]]:
    """Markdown subset -> HTML. Returns (html, [(h2 id, h2 text)])."""
    text = strip_comments(text.replace("\r\n", "\n"))
    lines = text.split("\n")
    out: List[str] = []
    toc: List[Tuple[str, str]] = []
    i, n = 0, len(lines)

    def at(k: int) -> str:
        return f"{where}:{first_line + k}"

    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue

        if s.startswith("```") or s.startswith("~~~"):
            fence = s[:3]
            j = i + 1
            while j < n and not lines[j].strip().startswith(fence):
                j += 1
            if j >= n:
                raise ArticleError(f"{at(i)}: code block is not closed")
            code = "\n".join(lines[i + 1:j])
            out.append(f"<pre><code>{_e(code)}</code></pre>")
            i = j + 1
            continue

        if "{{" in s and "chart:" in s:
            m = CHART_RE.match(s)
            if not m:
                raise ArticleError(f"{at(i)}: a chart placeholder must be alone on its line: {{{{chart:<id>}}}}")
            args = [a for a in m.group(2).split(":") if a]
            try:
                out.append(chart(m.group(1), args))
            except ArticleError as err:
                raise ArticleError(f"{at(i)}: {err}") from None
            i += 1
            continue

        m = HEADING_RE.match(s)
        if m and not line.startswith(" "):
            level = len(m.group(1))
            if level > 4:
                raise ArticleError(f"{at(i)}: headings go down to #### only")
            tag = "h2" if level <= 2 else f"h{level}"
            body = inline(m.group(2), at(i))
            if tag == "h2":
                hid = f"s{len(toc) + 1}"
                toc.append((hid, re.sub(r"<[^>]+>", "", body)))
                out.append(f'<h2 id="{hid}">{body}</h2>')
            else:
                out.append(f"<{tag}>{body}</{tag}>")
            i += 1
            continue

        if HR_RE.match(s):
            out.append("<hr>")
            i += 1
            continue

        if s.startswith(">"):
            quote = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner, _ = render_markdown("\n".join(quote), chart, where, first_line + i - len(quote))
            out.append(f"<blockquote>{inner}</blockquote>")
            continue

        if "|" in s and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1].strip()):
            rows = [s]
            j = i + 1
            while j < n and lines[j].strip() and "|" in lines[j]:
                rows.append(lines[j].strip())
                j += 1
            out.append(_table_html(rows, at(i)))
            i = j
            continue

        if ITEM_RE.match(line):
            items: List[Tuple[int, bool, List[str]]] = []
            while i < n:
                cur = lines[i]
                mi = ITEM_RE.match(cur)
                if mi:
                    items.append((len(mi.group(1).expandtabs(4)), not mi.group(2)[0] in "-*+", [mi.group(3)]))
                    i += 1
                    continue
                if cur.strip() and cur.startswith((" ", "\t")) and items:
                    items[-1][2].append(cur.strip())
                    i += 1
                    continue
                if not cur.strip() and i + 1 < n and ITEM_RE.match(lines[i + 1]):
                    i += 1
                    continue
                break
            out.append(_list_html(items, at(i)))
            continue

        para = []
        while i < n:
            cur = lines[i]
            cs = cur.strip()
            if (
                not cs
                or HEADING_RE.match(cs)
                or HR_RE.match(cs)
                or cs.startswith((">", "```", "~~~"))
                or ITEM_RE.match(cur)
                or CHART_RE.match(cs)
                or ("|" in cs and i + 1 < n and TABLE_SEP_RE.match(lines[i + 1].strip()))
            ):
                break
            para.append(cur)
            i += 1
        out.append(f"<p>{_para_html(para, at(i))}</p>")

    return "\n".join(out), toc
