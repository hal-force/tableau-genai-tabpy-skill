#!/usr/bin/env python3
"""Build the row-level Payload calculation (one record of grounding text).

  python3 payload.py --fields "Report ID=Report Id" "Week Ending=Week Ending Date" \
      "Site" "Title=Report Title" "Report Content=Full Text" [--max-chars "Full Text=6000"]
  python3 payload.py --from-profile profile.json [--table Reports]

FIELD spec is "Label=Field Caption" or just "Field Caption". Types come from
--from-profile (or --type "Caption=date"); non-strings are wrapped in STR().
Prints the caption-based formula (what you'd paste in Tableau). The patcher
imports build_formula() and passes internal names instead.

All fields must come from ONE logical table: a row-level calc that mixes
tables in a relationship model will fail or duplicate rows.
"""
from __future__ import annotations

import argparse
import json
import sys

START, END = " Start of Record ", " End of Record "


def tableau_literal(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def field_expr(ref: str, ftype: str, max_chars: int | None) -> str:
    ftype = (ftype or "string").lower()
    inner = ref if ftype in ("string", "text") else "STR(%s)" % ref
    if max_chars:
        inner = "LEFT(%s, %d)" % (inner, max_chars)
    return 'IFNULL(%s, "N/A")' % inner


def build_formula(fields: list[dict], ref_key: str = "caption") -> str:
    """fields: [{"label", "caption", "internal", "type", "max_chars"}]."""
    parts = [tableau_literal(START)]
    for f in fields:
        ref = f[ref_key] if f.get(ref_key) else f["caption"]
        if not ref.startswith("["):
            ref = "[" + ref + "]"
        parts.append(tableau_literal(" %s: " % f["label"]) + " + " + field_expr(ref, f.get("type"), f.get("max_chars")))
    parts.append(tableau_literal(END))
    return " +\n".join(parts)


def parse_specs(specs, types, maxc):
    out = []
    for s in specs:
        label, _, cap = s.partition("=")
        cap = cap or label
        out.append({"label": label.strip(), "caption": cap.strip(), "type": types.get(cap.strip(), "string"),
                    "max_chars": maxc.get(cap.strip())})
    return out


def kv(items):
    d = {}
    for it in items or []:
        k, _, v = it.partition("=")
        d[k.strip()] = v.strip()
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fields", nargs="*")
    ap.add_argument("--type", nargs="*", default=[], help='"Caption=date|number|string"')
    ap.add_argument("--max-chars", nargs="*", default=[], help='"Caption=N" truncates long text')
    ap.add_argument("--from-profile")
    ap.add_argument("--table")
    a = ap.parse_args(argv)
    maxc = {k: int(v) for k, v in kv(a.max_chars).items()}
    if a.from_profile:
        prof = json.load(open(a.from_profile))
        recs = prof["payload_recommendations"]
        rec = next((r for r in recs if not a.table or r["table"] == a.table), None)
        if not rec:
            raise SystemExit("no recommendation for table %r" % a.table)
        fields = [{"label": f["label"], "caption": f["field"], "type": f["type"],
                   "max_chars": maxc.get(f["field"])} for f in rec["fields"]]
    elif a.fields:
        fields = parse_specs(a.fields, kv(a.type), maxc)
    else:
        raise SystemExit("--fields or --from-profile required")
    print(build_formula(fields))
    return 0


if __name__ == "__main__":
    sys.exit(main())
