#!/usr/bin/env python3
"""Render prompt-engineering specs into full prompts for the Prompts parameter.

  python3 compose_prompts.py SPEC.json [--out DIR]

SPEC.json
---------
{
  "domain": "DNFSB weekly resident-inspector reports",       # optional context
  "record_noun": "report", "id_label": "Report ID",          # used in shared constraints
  "shared_constraints": ["Use only the GROUNDING DATA ...", ...],   # optional, appended to each
  "free_text_label": "Free text (use the Prompt box)",       # optional; "" to omit
  "prompts": [
    {"label": "Executive Safety Brief",                      # short name
     "summary": "1-page BLUF for leadership",                # brief description (Display As)
     "persona": "...", "audience": "...", "objective": "...",
     "constraints": ["...", "..."],
     "output": "Markdown: BLUF line, then ..."}               # optional output format
  ]
}

Each prompt becomes (the full text is the parameter VALUE):

  PERSONA: ...
  AUDIENCE: ...
  OBJECTIVE: ...
  CONSTRAINTS:
  - ...
  OUTPUT FORMAT: ...

and "<label> - <summary>" is the Display As alias. Double quotes are
converted to single quotes so values are safe as Tableau string literals.
Writes DIR/prompts.json (consumed by patch_workbook.py) and DIR/prompts.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

MAX_ALIAS = 80
MAX_PROMPT_CHARS = 6000


def clean(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace('"', "'").replace("“", "'").replace("”", "'")
    return s.strip()


def render(p: dict, shared: list) -> str:
    for k in ("label", "persona", "audience", "objective"):
        if not clean(p.get(k, "")):
            raise SystemExit("prompt %r is missing %r" % (p.get("label", "?"), k))
    cons = [clean(c) for c in (p.get("constraints") or []) + list(shared or []) if clean(c)]
    lines = ["PERSONA: " + clean(p["persona"]),
             "AUDIENCE: " + clean(p["audience"]),
             "OBJECTIVE: " + clean(p["objective"]),
             "CONSTRAINTS:"] + ["- " + c for c in cons]
    if clean(p.get("output", "")):
        lines.append("OUTPUT FORMAT: " + clean(p["output"]))
    return "\n".join(lines)


def compose(spec: dict) -> dict:
    shared = spec.get("shared_constraints") or []
    members, seen = [], set()
    for p in spec.get("prompts") or []:
        text = render(p, shared)
        alias = clean(p["label"]) + ((" - " + clean(p["summary"])) if clean(p.get("summary", "")) else "")
        if len(alias) > MAX_ALIAS:
            alias = alias[:MAX_ALIAS - 3].rstrip() + "..."
        if alias in seen:
            raise SystemExit("duplicate Display As: %r" % alias)
        seen.add(alias)
        if len(text) > MAX_PROMPT_CHARS:
            print("WARNING: %r is %d chars; long templates eat grounding budget" % (alias, len(text)),
                  file=sys.stderr)
        members.append({"label": clean(p["label"]), "alias": alias, "value": text,
                        "est_tokens": len(text) // 4})
    if not members:
        raise SystemExit("spec has no prompts")
    free = spec.get("free_text_label", "Free text (use the Prompt box)")
    if free:
        members.append({"label": "Free text", "alias": clean(free), "value": "", "est_tokens": 0})
    default = spec.get("default") or members[0]["alias"]
    if default not in [m["alias"] for m in members]:
        raise SystemExit("default %r is not one of the Display As values" % default)
    return {"parameter_caption": spec.get("parameter_caption", "Prompts"),
            "default_alias": default, "domain": spec.get("domain", ""), "members": members}


def to_md(out: dict) -> str:
    L = ["# %s parameter - %s" % (out["parameter_caption"], out["domain"] or "prompt library"), "",
         "Default: **%s**" % out["default_alias"], ""]
    for m in out["members"]:
        L += ["## %s" % m["alias"], "", "~%d tokens" % m["est_tokens"], ""]
        L += ["```text", m["value"] or "(empty - the Prompt box text is sent on its own)", "```", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    with open(a.spec) as fh:
        out = compose(json.load(fh))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "prompts.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    with open(os.path.join(a.out, "prompts.md"), "w") as fh:
        fh.write(to_md(out))
    for m in out["members"]:
        print("%-70s %5d tokens" % (m["alias"], m["est_tokens"]))
    print("wrote %s/prompts.json and prompts.md" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
