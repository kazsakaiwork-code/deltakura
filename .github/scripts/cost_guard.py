#!/usr/bin/env python3
"""Cost guard: fail the build on anything that could create a bill.

Runs in CI as a step of the `security-scan` job. Standard library only, no
network, Python 3.10+.

    python .github/scripts/cost_guard.py              # this repository
    python .github/scripts/cost_guard.py --root DIR   # any other checkout

The project runs on free tiers only (Cloudflare Workers Free with KV and D1,
Firebase Spark, GitHub Free). A paid feature must arrive through an explicit,
per-item approval, never through a config edit that happens to pass review.
Some of these accounts have a payment method on file, so "the deploy failed,
let me enable the product" is exactly how an unapproved charge would start.
The guard makes that edit fail in CI instead.

What fails the guard
  wrangler config   (wrangler.toml / wrangler.json / wrangler.jsonc, any directory)
      - a paid or subscription-only binding or feature, in any environment:
        r2_buckets, queues, durable_objects, hyperdrive, ai, browser, vectorize,
        containers, dispatch_namespaces, pipelines, images, tail_consumers,
        logpush, limits, triggers (unattended cron), usage_model
      - custom domains: workers_dev = false, route, routes, custom_domain
        (all of them need a registered domain, which is a purchase)
      - the words plan / plans / billing anywhere outside a comment
      - any table or key that is not on the free-tier allowlist below
  package.json scripts
      - wrangler r2 / queues / hyperdrive / vectorize / ai / pipelines /
        containers / dispatch-namespace
      - wrangler deploy / publish / versions deploy|upload / pages deploy
        without --dry-run in the same command
  dependencies (package.json, package-lock.json, requirements*.txt)
      - a paid-API SDK: Stripe, Paddle, Polar, Lemon Squeezy, PayPal, OpenAI,
        Anthropic, Google Gemini, Cohere, Mistral, Replicate, Apify

Loosening any of this is a reviewed change to this file, made only after the
paid feature itself has been approved.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", "node_modules", ".wrangler", "dist", "__pycache__", ".venv", "venv"}

# ------------------------------------------------------------- wrangler config

# Cloudflare features that are paid, need a paid subscription, or run unattended.
PAID_FEATURES = {
    "r2_buckets": "R2 needs a paid subscription on the account",
    "queues": "Queues is a Workers Paid feature",
    "durable_objects": "Durable Objects bill per request and per GB-second",
    "hyperdrive": "Hyperdrive is a paid database connector",
    "ai": "Workers AI bills per neuron beyond the free allocation",
    "browser": "Browser Rendering bills per browser-hour",
    "vectorize": "Vectorize bills per stored and queried dimension",
    "containers": "Containers is a Workers Paid feature",
    "dispatch_namespaces": "Workers for Platforms is a paid product",
    "pipelines": "Pipelines is a paid product",
    "images": "Cloudflare Images is a paid product",
    "tail_consumers": "Tail Workers are a Workers Paid feature",
    "logpush": "Logpush is a Workers Paid feature",
    "limits": "CPU-time limits above the free cap are a Workers Paid setting",
    "triggers": "cron triggers run unattended and have not been approved",
    "usage_model": "usage_model selects a billing model",
    "unsafe": "unsafe bindings bypass the allowlist",
}
CUSTOM_DOMAIN_KEYS = {
    "route": "a route needs a zone, i.e. a registered domain",
    "routes": "a route needs a zone, i.e. a registered domain",
    "custom_domain": "a custom domain needs a registered domain",
}
BILLING_WORDS = re.compile(r"(?i)\b(plan|plans|billing)\b")

# Everything the free-tier Worker is allowed to declare. `env.<name>.` is
# stripped before the check, so each environment gets the same allowlist.
ALLOWED_TABLES = {"vars", "kv_namespaces", "d1_databases", "observability", "observability.logs", "build"}
ALLOWED_KEYS = {
    "name", "main", "compatibility_date", "compatibility_flags", "workers_dev", "preview_urls",
    "binding", "id", "preview_id",
    "database_name", "database_id", "preview_database_id", "migrations_dir", "migrations_table",
    "enabled", "head_sampling_rate", "invocation_logs",
    "command", "cwd", "watch_dir",
}

_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*$")
_KEYVAL = re.compile(r"^\s*([A-Za-z0-9_\-.\"' ]+?)\s*=\s*(.*)$")
_INLINE_KEY = re.compile(r"[{,]\s*([A-Za-z0-9_\-]+)\s*=")


def strip_comment(line: str) -> str:
    """Drop a TOML comment, leaving any '#' inside a quoted string alone."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def split_key(dotted: str) -> list[str]:
    return [part.strip().strip("\"'") for part in dotted.split(".") if part.strip()]


def strip_env(parts: list[str]) -> list[str]:
    if len(parts) >= 2 and parts[0] == "env":
        return parts[2:]
    return parts


def key_findings(parts: list[str], where: str) -> list[str]:
    out = []
    for part in parts:
        if part in PAID_FEATURES:
            out.append(f"{where} '{part}': {PAID_FEATURES[part]}")
        elif part in CUSTOM_DOMAIN_KEYS:
            out.append(f"{where} '{part}': {CUSTOM_DOMAIN_KEYS[part]}")
    return out


def wrangler_toml_findings(text: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    table: list[str] = []  # current table header, as written (env prefix included)
    table_refused = False  # keys under a refused table are already covered by its finding
    for n, raw in enumerate(text.splitlines(), 1):
        line = strip_comment(raw).rstrip()
        if not line.strip():
            continue
        if BILLING_WORDS.search(line):
            findings.append((n, "plan/billing keyword outside a comment"))

        header = _HEADER.match(line)
        if header:
            table = split_key(header.group(1))
            bad = key_findings(table, "table")
            findings += [(n, b) for b in bad]
            path = strip_env(table)
            table_refused = bool(bad) or bool(path and ".".join(path) not in ALLOWED_TABLES)
            if not bad and table_refused:
                findings.append((n, f"table [{'.'.join(table)}] is not on the free-tier allowlist"))
            continue

        kv = _KEYVAL.match(line)
        if not kv:
            continue
        key_parts = split_key(kv.group(1))
        value = kv.group(2).strip()
        bad = key_findings(key_parts + _INLINE_KEY.findall(value), "key")
        findings += [(n, b) for b in bad]
        if key_parts and key_parts[-1] == "workers_dev" and value.lower().startswith("false"):
            findings.append((n, "workers_dev = false: serving only from a custom domain needs a registered domain"))
        if bad or table_refused:
            continue
        # Allowlist: the full key path, minus any env.<name> prefix, must be an
        # allowed key directly under the root or under an allowed table. Keys
        # under [vars] are free-form (they are plain strings, not bindings).
        path = strip_env(table + key_parts)
        if not path or path[0] == "vars":
            continue
        parent, leaf = ".".join(path[:-1]), path[-1]
        if leaf not in ALLOWED_KEYS or (parent and parent not in ALLOWED_TABLES):
            findings.append((n, f"key '{'.'.join(path)}' is not on the free-tier allowlist"))
    return findings


def _json_walk(node, path: list[str], out: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            here = path + [str(k)]
            out += key_findings([str(k)], "key")
            if BILLING_WORDS.search(str(k)):
                out.append(f"plan/billing keyword in key '{'.'.join(here)}'")
            if k == "workers_dev" and v is False:
                out.append("workers_dev = false: serving only from a custom domain needs a registered domain")
            _json_walk(v, here, out)
    elif isinstance(node, list):
        for item in node:
            _json_walk(item, path, out)
    elif isinstance(node, str) and BILLING_WORDS.search(node):
        out.append(f"plan/billing keyword in value at '{'.'.join(path)}'")


def wrangler_json_findings(text: str) -> list[tuple[int, str]]:
    # jsonc: drop whole-line and trailing // comments, and trailing commas.
    cleaned = "\n".join(re.sub(r"^\s*//.*$|\s+//[^\"]*$", "", ln) for ln in text.splitlines())
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    try:
        data = json.loads(cleaned)
    except ValueError:
        return [(1, "wrangler JSON config could not be parsed, so it cannot be checked")]
    out: list[str] = []
    _json_walk(data, [], out)
    return [(1, o) for o in out]


# ------------------------------------------------------------ package scripts

WRANGLER_PAID_CMDS = re.compile(
    r"\bwrangler\s+(r2|queues|hyperdrive|vectorize|ai|pipelines|containers|dispatch-namespace)\b")
WRANGLER_DEPLOY = re.compile(r"\bwrangler\s+(deploy|publish|versions\s+(deploy|upload)|pages\s+deploy)\b")


def script_findings(name: str, command: str) -> list[str]:
    out = []
    for segment in re.split(r"&&|\|\||;|\|", command):
        m = WRANGLER_PAID_CMDS.search(segment)
        if m:
            out.append(f"script '{name}' runs 'wrangler {m.group(1)}', a paid Cloudflare product")
        if WRANGLER_DEPLOY.search(segment) and "--dry-run" not in segment:
            out.append(f"script '{name}' deploys without --dry-run; a real deploy needs a per-item approval")
    return out


# --------------------------------------------------------------- dependencies

PAID_NPM = re.compile(
    r"^(stripe|@stripe/.+|paddle.*|@paddle/.+|@polar-sh/.+|@lemonsqueezy/.+|@paypal/.+|paypal-.+"
    r"|openai|@openai/.+|anthropic|@anthropic-ai/.+|@google/genai|@google/generative-ai"
    r"|cohere-ai|@mistralai/.+|replicate|apify|apify-client)$", re.IGNORECASE)
PAID_PYPI = re.compile(
    r"^(stripe|paddle.*|polar-sdk|lemonsqueezy.*|paypal.*|openai|anthropic|google-genai"
    r"|google-generativeai|cohere|mistralai|replicate|apify|apify-client)$", re.IGNORECASE)
DEP_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies", "bundleDependencies")


def package_json_findings(text: str) -> list[str]:
    try:
        pkg = json.loads(text)
    except ValueError:
        return ["package.json could not be parsed, so it cannot be checked"]
    out = []
    for name, command in (pkg.get("scripts") or {}).items():
        out += script_findings(name, str(command))
    for section in DEP_SECTIONS:
        deps = pkg.get(section) or {}
        for dep in deps if isinstance(deps, (dict, list)) else []:
            if PAID_NPM.match(dep):
                out.append(f"{section} includes '{dep}', a paid-API SDK")
    return out


def lockfile_findings(text: str) -> list[str]:
    try:
        lock = json.loads(text)
    except ValueError:
        return ["package-lock.json could not be parsed, so it cannot be checked"]
    out = []
    for key in (lock.get("packages") or {}):
        name = key.rsplit("node_modules/", 1)[-1] if "node_modules/" in key else ""
        if name and PAID_NPM.match(name):
            out.append(f"lockfile resolves '{name}', a paid-API SDK (possibly transitive)")
    return sorted(set(out))


def requirements_findings(text: str) -> list[tuple[int, str]]:
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[\s\[<>=!~;@]", line, maxsplit=1)[0]
        if PAID_PYPI.match(name):
            out.append((n, f"requirement '{name}' is a paid-API SDK"))
    return out


# ----------------------------------------------------------------------- scan

def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            yield Path(dirpath) / f


def scan(root: Path) -> tuple[list[str], int]:
    report: list[str] = []
    checked = 0
    for path in walk(root):
        rel = path.relative_to(root).as_posix()
        name = path.name
        if name == "wrangler.toml":
            findings = wrangler_toml_findings(path.read_text(encoding="utf-8"))
        elif name in {"wrangler.json", "wrangler.jsonc"}:
            findings = wrangler_json_findings(path.read_text(encoding="utf-8"))
        elif name == "package.json":
            findings = [(0, f) for f in package_json_findings(path.read_text(encoding="utf-8"))]
        elif name == "package-lock.json":
            findings = [(0, f) for f in lockfile_findings(path.read_text(encoding="utf-8"))]
        elif re.fullmatch(r"requirements.*\.txt", name):
            findings = requirements_findings(path.read_text(encoding="utf-8"))
        else:
            continue
        checked += 1
        for line, why in findings:
            report.append(f"{rel}:{line}: {why}" if line else f"{rel}: {why}")
    return report, checked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", type=Path, default=ROOT, help="directory to scan (default: this repository)")
    args = ap.parse_args(argv)
    root = args.root.resolve()

    report, checked = scan(root)
    print(f"cost-guard: {checked} config/manifest file(s) checked")
    if checked == 0:
        print("cost-guard: FAIL - nothing to check; wrong --root?")
        return 2
    if report:
        print(f"cost-guard: FAIL - {len(report)} finding(s). Free tiers only until a paid feature is approved.")
        for item in report:
            print(f"  {item}")
        return 1
    print("cost-guard: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
