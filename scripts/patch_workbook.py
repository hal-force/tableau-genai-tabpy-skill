#!/usr/bin/env python3
"""Write a new .twb/.twbx with the GenAI calc, Payload, and Prompts parameter.

  python3 patch_workbook.py WORKBOOK.twb[x] \
      --prompts prompts.json \
      --payload "Report ID=Report Id" "Week Ending=Week Ending Date" "Site" \
                "Title=Report Title" "Report Content=Full Text" \
      [--payload-max-chars "Full Text=8000"] \
      [--models "Claude Sonnet 4=claude-sonnet-4-20250514" "GPT-5=gpt-5"] \
      [--out-dir DIR] [--suffix "GenAI - Gateway"] \
      -- --provider gateway --mode keystore --url https://... --tls ~/.tableau-genai/ca-bundle.pem

Everything after "--" is passed to genai_calc.py (provider, mode, URL ...).

What it changes (the original file is never modified):
  * Generative AI Response -> new SCRIPT_STR body for the provider, args
    (Prompts, Grounding, Select LLM, Grounding Token Count, Prompt)
  * Payload               -> rebuilt from --payload (one logical table)
  * Prompts               -> NEW string list parameter: value = full
    engineered prompt, Display As = short description; added to the
    dashboard next to the Prompt box and to every dependency block
  * Select LLM            -> members replaced when --models is given
  * Grounding             -> saved value reset to "" (--keep-grounding to skip)
  * Prompt                -> default set to --prompt-default (default "")
Every duplicate definition (datasource + worksheet/dashboard dependency
copies) is updated. If the workbook has no GenAI scaffold, the parameters
and calcs are created in --datasource and build steps for the button
sheet + parameter action are printed.

Validation before writing: Python body compiles; every [ref] resolves;
keystore mode leaves no credential literal anywhere in the workbook; the
original calc's embedded key (if any) is gone; output re-parses; zone ids
are unique per layout.
"""
from __future__ import annotations

import argparse
import atexit
import copy
import html
import json
import os
import random
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import genai_calc  # noqa: E402
import payload as payload_mod  # noqa: E402
from profile_data import credential_values, has_plaintext_key  # noqa: E402

USER_NS = "http://www.tableausoftware.com/xml/user"
ET.register_namespace("user", USER_NS)

CAP_RESPONSE, CAP_PAYLOAD = "Generative AI Response", "Payload"
CAP_GROUNDING, CAP_TOKENS = "Grounding", "Grounding Token Count"
CAP_PROMPT, CAP_PROMPTS, CAP_MODEL = "Prompt", "Prompts", "Select LLM"
TOKEN_FORMULA = ('// Approximate token count for [Grounding]\n'
                 '// Heuristic: ~4 characters per token for English text.\n'
                 '// Returns 0 when Grounding is empty, which trips the guard in the TabPy script.\n'
                 'INT(LEN(IFNULL(%s, "")) / 4)')


def _open_private(path, mode: int) -> int:
    """Open for writing and force `mode` even when the file already exists
    (os.open's mode only applies on creation)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    os.fchmod(fd, mode)
    return fd


def lit(s: str) -> str:
    """Tableau string literal as stored in param value/formula attributes."""
    return '"' + (s or "").replace('"', '""') + '"'


def new_calc_name() -> str:
    return "[Calculation_%d]" % random.randint(10 ** 17, 10 ** 18 - 1)


class Patcher:
    def __init__(self, root: ET.Element, log: list):
        self.root = root
        self.log = log
        self.parent = {c: p for p in root.iter() for c in p}

    # ------------------------------------------------------------ lookup
    def datasources(self):
        dss = self.root.find("datasources")
        return [d for d in (dss if dss is not None else []) if d.tag == "datasource"]

    def params_ds(self, create=False):
        for d in self.datasources():
            if d.get("name") == "Parameters":
                return d
        if not create:
            return None
        dss = self.root.find("datasources")
        d = ET.Element("datasource", {"hasconnection": "false", "inline": "true",
                                      "name": "Parameters", "version": "18.1"})
        ET.SubElement(d, "aliases", {"enabled": "yes"})
        dss.insert(0, d)
        self.parent[d] = dss
        self.log.append("created Parameters datasource")
        return d

    def param_by_caption(self, caption):
        pd = self.params_ds()
        if pd is None:
            return None
        for c in pd.findall("column"):
            if (c.get("caption") or c.get("name").strip("[]")).lower() == caption.lower():
                return c
        return None

    def find_scaffold(self, ds_caption=None, response_caption=None):
        """Return (main datasource, {role: column element})."""
        found = {}
        target = None
        want = (response_caption or CAP_RESPONSE).lower()
        scripts = []   # (datasource, column) for every SCRIPT_* calc
        for d in self.datasources():
            if d.get("name") == "Parameters":
                continue
            if ds_caption and ds_caption not in (d.get("caption"), d.get("name")):
                continue
            for c in d.findall("column"):
                calc = c.find("calculation")
                if calc is not None and "SCRIPT_" in calc.get("formula", ""):
                    scripts.append((d, c))
        named = [x for x in scripts if (x[1].get("caption") or "").lower() == want]
        if named:
            target, found["response"] = named[0]
        elif response_caption:
            raise SystemExit("no SCRIPT_ calculation captioned %r (found: %s)" % (
                response_caption, [x[1].get("caption") or x[1].get("name") for x in scripts] or "none"))
        elif len(scripts) == 1:
            target, found["response"] = scripts[0]
        elif scripts:
            raise SystemExit("several SCRIPT_ calculations and none captioned %r: %s. Pass "
                             "--response CAPTION to choose." % (CAP_RESPONSE,
                             [x[1].get("caption") or x[1].get("name") for x in scripts]))
        if target is None:
            cands = [d for d in self.datasources() if d.get("name") != "Parameters" and
                     (not ds_caption or ds_caption in (d.get("caption"), d.get("name")))]
            if not cands:
                raise SystemExit("no data source found%s" % (" named %r" % ds_caption if ds_caption else ""))
            target = cands[0]
        for c in target.findall("column"):
            cap = (c.get("caption") or "").lower()
            calc = c.find("calculation")
            f = calc.get("formula", "") if calc is not None else ""
            if cap == CAP_PAYLOAD.lower():
                found.setdefault("payload", c)
            if cap == CAP_TOKENS.lower() or (calc is not None and "LEN(" in f.upper() and "/ 4" in f.replace("/4", "/ 4")):
                found.setdefault("tokens", c)
        # payload via the parameter action is authoritative
        for act in self.root.iter("edit-parameter-action"):
            ps = {p.get("name"): p.get("value") for p in act.iter("param")}
            m = re.match(r"\[(.+?)\]\.\[[a-z]+:(.+?):[a-z]+\]$", ps.get("source-field") or "")
            if m and m.group(1) == target.get("name"):
                for c in target.findall("column"):
                    if c.get("name") == "[" + m.group(2) + "]":
                        found["payload"] = c
                t = re.match(r"\[Parameters\]\.(\[.+\])$", ps.get("target-parameter") or "")
                if t:
                    pd = self.params_ds()
                    for c in (pd.findall("column") if pd is not None else []):
                        if c.get("name") == t.group(1):
                            found["grounding"] = c
        for role, cap in (("grounding", CAP_GROUNDING), ("prompt", CAP_PROMPT),
                          ("model", CAP_MODEL), ("prompts", CAP_PROMPTS)):
            if role not in found:
                c = self.param_by_caption(cap)
                if c is not None:
                    found[role] = c
        # renamed parameters: fall back to the Response's own SCRIPT_STR arguments
        # (4-arg legacy: Prompt, Grounding, Select LLM, Token Count;
        #  5-arg: Prompts, Grounding, Select LLM, Token Count, Prompt)
        if "response" in found:
            f = found["response"].find("calculation").get("formula", "")
            refs = refs_in(f)
            roles = {4: ("prompt", "grounding", "model", "tokens"),
                     5: ("prompts", "grounding", "model", "tokens", "prompt")}.get(len(refs), ())
            pd = self.params_ds()
            pcols = {c.get("name"): c for c in (pd.findall("column") if pd is not None else [])}
            fcols = {c.get("name"): c for c in target.findall("column")}
            for role, ref in zip(roles, refs):
                if role in found:
                    continue
                m = re.match(r"\[Parameters\]\.(\[.+\])$", ref)
                c = pcols.get(m.group(1)) if m else (fcols.get(ref) if role == "tokens" else None)
                if c is not None:
                    found[role] = c
                    self.log.append("found %s via the Response's arguments: %s" % (role, ref))
        return target, found

    def copies(self, internal_name: str, scope=None):
        """Every <column name=internal_name> in datasources and dependency blocks."""
        for c in (scope or self.root).iter("column"):
            if c.get("name") == internal_name:
                p = self.parent.get(c)
                if p is not None and p.tag in ("datasource", "datasource-dependencies"):
                    yield c

    def dep_blocks(self, ds_name):
        for dep in self.root.iter("datasource-dependencies"):
            if dep.get("datasource") == ds_name:
                yield dep

    # ------------------------------------------------------------ editing
    def set_formula_everywhere(self, internal, formula):
        n = 0
        for c in self.copies(internal):
            calc = c.find("calculation")
            if calc is None:
                calc = ET.SubElement(c, "calculation", {"class": "tableau"})
            calc.set("formula", formula)
            n += 1
        return n

    def add_column(self, ds, attrs, formula=None, table_calc=False):
        col = ET.Element("column", attrs)
        if formula is not None:
            calc = ET.SubElement(col, "calculation", {"class": "tableau", "formula": formula})
            if table_calc:
                ET.SubElement(calc, "table-calc", {"ordering-type": "Rows"})
        cols = ds.findall("column")
        idx = list(ds).index(cols[-1]) + 1 if cols else len([e for e in ds if e.tag in ("connection", "aliases")])
        ds.insert(idx, col)
        self.parent[col] = ds
        for sub in col.iter():
            for ch in sub:
                self.parent[ch] = sub
        return col

    def ensure_dep(self, dep, src_col, with_members=False):
        name = src_col.get("name")
        for c in dep.findall("column"):
            if c.get("name") == name:
                dep.remove(c)
        c = copy.deepcopy(src_col)
        if not with_members:
            for m in c.findall("members"):
                c.remove(m)
        cols = dep.findall("column")
        idx = list(dep).index(cols[-1]) + 1 if cols else 0
        dep.insert(idx, c)
        self.parent[c] = dep
        for sub in c.iter():
            for ch in sub:
                self.parent[ch] = sub

    def holder_of(self, el):
        """worksheet or dashboard element that owns a dependency block."""
        p = self.parent.get(el)
        while p is not None and p.tag not in ("worksheet", "dashboard"):
            p = self.parent.get(p)
        return p

    def params_dep_in(self, holder, create=True):
        for dep in holder.iter("datasource-dependencies"):
            if dep.get("datasource") == "Parameters":
                return dep
        if not create:
            return None
        anchor = next(holder.iter("datasource-dependencies"), None)
        if anchor is None:
            return None
        parent = self.parent[anchor]
        dep = ET.Element("datasource-dependencies", {"datasource": "Parameters"})
        parent.insert(list(parent).index(anchor), dep)
        self.parent[dep] = parent
        # make sure the view lists the Parameters datasource
        dss = parent.find("datasources")
        if dss is not None and not any(d.get("name") == "Parameters" for d in dss):
            ET.SubElement(dss, "datasource", {"name": "Parameters"})
        return dep

    # ------------------------------------------------------------ params
    def set_list_param(self, col, members, default_value):
        """members: [(value, alias or None)]"""
        alias_of = dict(members)
        col.set("datatype", "string")
        col.set("param-domain-type", "list")
        col.set("role", "measure")
        col.set("type", "nominal")
        col.set("value", lit(default_value))
        if alias_of.get(default_value):
            col.set("alias", alias_of[default_value])
        elif "alias" in col.attrib:
            del col.attrib["alias"]
        for tag in ("calculation", "aliases", "members", "range"):
            for e in col.findall(tag):
                col.remove(e)
        ET.SubElement(col, "calculation", {"class": "tableau", "formula": lit(default_value)})
        al = [(v, a) for v, a in members if a]
        if al:
            ae = ET.SubElement(col, "aliases")
            for v, a in al:
                ET.SubElement(ae, "alias", {"key": lit(v), "value": a})
        me = ET.SubElement(col, "members")
        for v, a in members:
            attrs = {"value": lit(v)}
            if a:
                attrs = {"alias": a, "value": lit(v)}
            ET.SubElement(me, "member", attrs)
        for sub in col.iter():
            for ch in sub:
                self.parent[ch] = sub

    def set_any_param_value(self, internal, value):
        for c in self.copies(internal):
            c.set("value", lit(value))
            calc = c.find("calculation")
            if calc is not None:
                calc.set("formula", lit(value))

    def next_param_name(self):
        used = {c.get("name") for c in self.root.iter("column")}
        n = 1
        while "[Parameter %d]" % n in used:
            n += 1
        return "[Parameter %d]" % n

    # ------------------------------------------------------------ zones
    def add_param_zone(self, prompt_param_ref, model_param_ref, new_param_ref):
        """Returns (layouts given a new Prompts control, dashboards that already had one)."""
        added = present = 0
        for db in self.root.iter("dashboard"):
            zones = [z for z in db.iter("zone")]
            if not any(z.get("param") == prompt_param_ref for z in zones):
                continue
            if any(z.get("param") == new_param_ref for z in zones):
                present += 1
                continue
            new_id = str(max(int(z.get("id", "0")) for z in zones) + 1)
            for pz in [z for z in zones if z.get("param") == prompt_param_ref and z.get("type-v2") == "paramctrl"]:
                parent = self.parent[pz]
                mz = next((z for z in parent if z.get("param") == model_param_ref), None)
                pw = int(pz.get("w", "0"))
                w = pw // 3
                nz = copy.deepcopy(mz if mz is not None else pz)
                nz.set("id", new_id)
                nz.set("param", new_param_ref)
                nz.set("mode", "compact")
                nz.set("x", pz.get("x"))
                nz.set("y", pz.get("y"))
                nz.set("h", pz.get("h"))
                nz.set("w", str(w))
                pz.set("x", str(int(pz.get("x")) + w))
                pz.set("w", str(pw - w))
                parent.insert(list(parent).index(pz), nz)
                self.parent[nz] = parent
                added += 1
        return added, present


def resolve_payload_fields(ds, specs, maxc, types):
    """specs: ['Label=Caption'|'Caption'] -> field dicts with internal names + table."""
    by_cap, table_of, dtype_of = {}, {}, {}
    for m in ds.iter("metadata-record"):
        if m.get("class") == "column" and m.findtext("local-name"):
            ln = m.findtext("local-name")
            table_of[ln] = (m.findtext("parent-name") or "").strip("[]")
            dtype_of[ln] = m.findtext("local-type")
            by_cap.setdefault(ln.strip("[]").lower(), ln)
    for c in ds.findall("column"):
        if c.find("calculation") is None or c.get("caption"):
            by_cap[(c.get("caption") or c.get("name").strip("[]")).lower()] = c.get("name")
            if c.get("datatype"):
                dtype_of.setdefault(c.get("name"), c.get("datatype"))
    out, tables = [], set()
    names = set()
    for s in specs:
        label, _, cap = s.partition("=")
        names |= {label.strip(), (cap or label).strip()}
    for opt, d in (("--payload-max-chars", maxc), ("--payload-type", types)):
        unknown = [k for k in d if k not in names]
        if unknown:
            raise SystemExit("%s names %s, which is not a --payload label or caption (%s)"
                             % (opt, unknown, sorted(names)))
    for s in specs:
        label, _, cap = s.partition("=")
        label = label.strip()
        cap = (cap or label).strip()
        internal = by_cap.get(cap.lower()) or by_cap.get(cap.lower().replace(" ", "_"))
        if not internal:
            raise SystemExit("payload field %r not found in data source %r" % (cap, ds.get("caption")))
        dt = types.get(label) or types.get(cap) or dtype_of.get(internal) or "string"
        out.append({"label": label, "caption": "[" + cap + "]", "internal": internal,
                    "type": "string" if dt in ("string", "text") else dt,
                    "max_chars": maxc.get(label) or maxc.get(cap), "table": table_of.get(internal)})
        if table_of.get(internal):
            tables.add(table_of[internal])
    if len(tables) > 1:
        raise SystemExit("payload fields span logical tables %s; a row-level Payload must use one "
                         "table (pick fields from the table whose rows are your records)" % sorted(tables))
    return out, (tables.pop() if tables else None)


def refs_in(formula: str):
    """[ref] tokens outside string literals and // comments."""
    out, i, n = [], 0, len(formula)
    while i < n:
        ch = formula[i]
        if ch == '"' or ch == "'":
            q = ch
            i += 1
            while i < n:
                if formula[i] == q:
                    if i + 1 < n and formula[i + 1] == q:
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif formula.startswith("//", i):
            j = formula.find("\n", i)
            i = n if j < 0 else j
        elif ch == "[":
            j = formula.find("]", i)
            tok = formula[i:j + 1]
            if formula.startswith(".[", j + 1):
                k = formula.find("]", j + 2)
                tok = formula[i:k + 1]
                j = k
            out.append(tok)
            i = j + 1
        else:
            i += 1
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    calc_argv = []
    if "--" in argv:
        k = argv.index("--")
        argv, calc_argv = argv[:k], argv[k + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workbook")
    ap.add_argument("--prompts", help="prompts.json from compose_prompts.py")
    ap.add_argument("--payload", nargs="*", help='"Label=Field Caption" ... (omit to keep current Payload)')
    ap.add_argument("--payload-max-chars", nargs="*", default=[], metavar="FIELD=N",
                    help='truncate a Payload field with LEFT(); FIELD = its label or caption, '
                         'e.g. "Report Content=4000"')
    ap.add_argument("--payload-type", nargs="*", default=[], metavar="FIELD=TYPE",
                    help="override a Payload field's datatype (label or caption)")
    ap.add_argument("--models", nargs="*", help='"Display As=model-id" ... replaces Select LLM members')
    ap.add_argument("--prompt-default", default="", help='default text of the Prompt box (default "")')
    ap.add_argument("--keep-prompt", action="store_true", help="leave the Prompt box default as is")
    ap.add_argument("--keep-grounding", action="store_true")
    ap.add_argument("--datasource", help="caption of the data source to use (default: the one with the calc)")
    ap.add_argument("--response", metavar="CAPTION",
                    help='caption of the existing SCRIPT_ calc to replace (default "%s"; needed only '
                         'when a workbook has several SCRIPT_ calcs and none has that caption)' % CAP_RESPONSE)
    ap.add_argument("--out-dir")
    ap.add_argument("--suffix")
    ap.add_argument("--report", help="write a JSON change report here")
    a = ap.parse_args(argv)
    if not calc_argv:
        raise SystemExit('pass genai_calc options after "--", e.g. -- --provider asksage --mode keystore')
    ca = genai_calc.build_parser().parse_args(calc_argv)
    if not ca.provider:
        raise SystemExit("--provider is required after --")
    prompts = json.load(open(a.prompts)) if a.prompts else None

    src = Path(os.path.expanduser(a.workbook))
    tmp = None
    if src.suffix.lower() == ".twbx":
        tmp = Path(tempfile.mkdtemp(prefix="genai_patch_"))
        atexit.register(shutil.rmtree, tmp, True)   # also on SystemExit / validation failure
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
            names = z.namelist()
        top = [n for n in names if n.lower().endswith(".twb") and "/" not in n and "\\" not in n
               and not n.startswith("..")]
        if not top:
            raise SystemExit("no top-level .twb inside %s" % src)
        twb_path = (tmp / top[0]).resolve()
        if twb_path.parent != tmp.resolve():
            raise SystemExit("refusing suspicious .twbx member name %r" % top[0])
    else:
        twb_path = src
    raw = twb_path.read_text(encoding="utf-8")
    prologue = raw[:raw.find("<workbook")]
    prologue = re.sub(r"^<\?xml[^>]*\?>\s*", "", prologue)
    root = ET.fromstring(raw, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    # live credential literals anywhere in the source (calcs, parameter values and
    # members, dependency copies): none may survive into the output. Never printed.
    old_keys = [k for k in credential_values(html.unescape(raw)) if len(k) >= 8]

    log = []
    P = Patcher(root, log)
    ds, sc = P.find_scaffold(a.datasource, a.response)
    ds_name = ds.get("name")
    pds = P.params_ds(create=True)
    created_scaffold = "response" not in sc

    # ---- parameters -----------------------------------------------------
    def make_param(caption, value=""):
        name = P.next_param_name()
        col = P.add_column(pds, {"caption": caption, "datatype": "string", "name": name,
                                 "param-domain-type": "any", "role": "measure", "type": "nominal",
                                 "value": lit(value)}, formula=lit(value))
        log.append("created parameter %s %s" % (caption, name))
        return col

    if "grounding" not in sc:
        sc["grounding"] = make_param(CAP_GROUNDING)
    if "prompt" not in sc:
        sc["prompt"] = make_param(CAP_PROMPT)
    if "model" not in sc:
        if not a.models:
            raise SystemExit('no "%s" parameter in this workbook: pass --models "Display As=model-id" ... '
                             "(scripts/list_models.py lists the ids your key can use)" % CAP_MODEL)
        sc["model"] = make_param(CAP_MODEL)

    if a.models:
        mem = []
        for m in a.models:
            alias, _, val = m.partition("=")
            mem.append((val.strip() or alias.strip(), alias.strip() if val.strip() else None))
        for c in P.copies(sc["model"].get("name")):
            in_params = P.parent[c] is pds
            P.set_list_param(c, mem, mem[0][0])
            if not in_params and P.holder_of(c) is not None and P.holder_of(c).tag == "worksheet":
                for e in c.findall("members"):
                    c.remove(e)
        log.append("Select LLM members -> %s" % [x[1] or x[0] for x in mem])

    prompts_ref = None
    if prompts:
        members = [(m["value"], m["alias"]) for m in prompts["members"]]
        default_val = next(m["value"] for m in prompts["members"] if m["alias"] == prompts["default_alias"])
        col = sc.get("prompts")
        if col is None:
            col = make_param(prompts.get("parameter_caption", CAP_PROMPTS))
            sc["prompts"] = col
        for c in list(P.copies(col.get("name"))):
            P.set_list_param(c, members, default_val)
            # worksheet dependency copies carry aliases only (see reference/twb_patching.md)
            if P.parent[c] is not pds and P.holder_of(c) is not None and P.holder_of(c).tag == "worksheet":
                for e in c.findall("members"):
                    c.remove(e)
        log.append("Prompts parameter %s: %d members, default %r" % (col.get("name"), len(members),
                                                                       prompts["default_alias"]))
        prompts_ref = "[Parameters].%s" % col.get("name")

    if prompts_ref is None and sc.get("prompts") is not None:
        prompts_ref = "[Parameters].%s" % sc["prompts"].get("name")
    ca.no_prompts_param = prompts_ref is None

    if not a.keep_grounding:
        P.set_any_param_value(sc["grounding"].get("name"), "")
        log.append("Grounding saved value reset to empty")
    if not a.keep_prompt:
        P.set_any_param_value(sc["prompt"].get("name"), a.prompt_default)
        log.append("Prompt default -> %r" % a.prompt_default)

    g_ref = "[Parameters].%s" % sc["grounding"].get("name")
    p_ref = "[Parameters].%s" % sc["prompt"].get("name")
    m_ref = "[Parameters].%s" % sc["model"].get("name")

    # ---- calcs ------------------------------------------------------------
    if "tokens" not in sc:
        sc["tokens"] = P.add_column(ds, {"caption": CAP_TOKENS, "datatype": "integer", "name": new_calc_name(),
                                         "role": "measure", "type": "quantitative"},
                                    formula=TOKEN_FORMULA % g_ref)
        log.append("created calc %s" % CAP_TOKENS)
    t_ref = sc["tokens"].get("name")

    if a.payload:
        maxc = {k: int(v) for k, v in payload_mod.kv(a.payload_max_chars).items()}
        fields, table = resolve_payload_fields(ds, a.payload, maxc, payload_mod.kv(a.payload_type))
        pf = payload_mod.build_formula(fields, ref_key="internal")
        if "payload" not in sc:
            sc["payload"] = P.add_column(ds, {"caption": CAP_PAYLOAD, "datatype": "string", "name": new_calc_name(),
                                              "role": "dimension", "type": "nominal"}, formula=pf)
            log.append("created calc Payload")
        n = P.set_formula_everywhere(sc["payload"].get("name"), pf)
        log.append("Payload (%d copies) from table %s: %s" % (n, table, [f["label"] for f in fields]))
        # base columns must be listed wherever Payload is
        base = {c.get("name"): c for c in ds.findall("column")}
        for dep in list(P.dep_blocks(ds_name)):
            if any(c.get("name") == sc["payload"].get("name") for c in dep.findall("column")):
                have = {c.get("name") for c in dep.findall("column")}
                for f in fields:
                    if f["internal"] not in have:
                        src_col = base.get(f["internal"])
                        attrs = ({k: src_col.get(k) for k in ("caption", "datatype", "name", "role", "type") if src_col.get(k)}
                                 if src_col is not None else
                                 {"datatype": f["type"] if f["type"] != "string" else "string",
                                  "name": f["internal"], "role": "dimension", "type": "nominal"})
                        e = ET.Element("column", attrs)
                        cols = dep.findall("column")
                        dep.insert(list(dep).index(cols[-1]) + 1 if cols else 0, e)
                        P.parent[e] = dep
                        log.append("added %s to a Payload dependency block" % f["internal"])
    elif "payload" not in sc:
        raise SystemExit("no Payload calc in the workbook: pass --payload fields")

    ca.fields = {"prompts": prompts_ref or "", "prompt": p_ref, "grounding": g_ref,
                 "model": m_ref, "token_count": t_ref}
    demo_values = {}
    if ca.mode == "demo" and ca.demo_key_from_keystore:
        import keystore
        p = genai_calc.PROVIDERS[ca.provider]
        for nm in genai_calc.secret_names(ca.provider, ca.secret_name) + p.get("optional_secrets", []):
            v, _ = keystore.get_secret(nm, ca.keystore_service)
            if v:
                demo_values[nm] = v
        if not demo_values:
            raise SystemExit("--demo-key-from-keystore: nothing stored; run set_secret.py first")
    body = genai_calc.build_body(ca.provider, ca.mode, ca, demo_values)
    genai_calc.check_compiles(body)
    formula = genai_calc.to_tableau_formula(body, genai_calc.arg_fields(ca))
    if "response" not in sc:
        sc["response"] = P.add_column(ds, {"caption": CAP_RESPONSE, "datatype": "string", "name": new_calc_name(),
                                           "role": "measure", "type": "nominal"}, formula=formula, table_calc=True)
        log.append("created calc Generative AI Response")
    n = P.set_formula_everywhere(sc["response"].get("name"), formula)
    log.append("Generative AI Response (%d copies) -> %s %s" % (n, ca.provider, ca.mode))

    # ---- dependency blocks for the Prompts parameter ----------------------
    if prompts_ref:
        pcol = sc["prompts"]
        holders = set()
        for c in P.copies(sc["response"].get("name")):
            h = P.holder_of(c)
            if h is not None:
                holders.add(h)
        for db in root.iter("dashboard"):
            if any(z.get("param") == p_ref for z in db.iter("zone")):
                holders.add(db)
        for h in holders:
            dep = P.params_dep_in(h)
            if dep is not None:
                P.ensure_dep(dep, pcol, with_members=(h.tag == "dashboard"))
        log.append("Prompts listed in %d sheet/dashboard dependency blocks" % len(holders))
        z, present = P.add_param_zone(p_ref, m_ref, prompts_ref)
        if z:
            log.append("Prompts control added to %d dashboard layout(s)" % z)
        elif present:
            log.append("Prompts control already on %d dashboard(s)" % present)
        else:
            log.append("no dashboard Prompt control found - add the Prompts parameter control manually")

    # ---- validation -------------------------------------------------------
    known = {c.get("name") for c in ds.iter("column")} | {m.findtext("local-name") for m in ds.iter("metadata-record")}
    known_p = {"[Parameters].%s" % c.get("name") for c in pds.findall("column")}
    problems = []
    for nm in (sc["response"].get("name"), sc["payload"].get("name"), t_ref):
        f = next(P.copies(nm)).find("calculation").get("formula")
        for r in refs_in(f):
            if r.startswith("[Parameters]."):
                if r not in known_p:
                    problems.append("%s references missing parameter %s" % (nm, r))
            elif r not in known:
                problems.append("%s references missing field %s" % (nm, r))
    serialized = ET.tostring(root, encoding="unicode")
    plain = html.unescape(serialized)
    if ca.mode == "keystore" and has_plaintext_key(plain):
        problems.append("a credential-looking literal remains in the workbook (keystore mode); "
                        "run profile_data.py on the output to see where (values are redacted)")
    if any(k in plain for k in old_keys if k not in demo_values.values()):
        problems.append("the source workbook's embedded credential is still present somewhere")
    for db in root.iter("dashboard"):
        for layout in [db.find("zones")] + list(db.iter("devicelayout")):
            if layout is None:
                continue
            ids = [z.get("id") for z in layout.iter("zone")]
            if len(ids) != len(set(ids)):
                problems.append("duplicate zone ids in dashboard %r" % db.get("name"))
    if problems:
        for p_ in problems:
            print("VALIDATION FAILED: " + p_, file=sys.stderr)
        return 2

    # ---- write ------------------------------------------------------------
    label = genai_calc.PROVIDERS[ca.provider]["label"].split(" (")[0]
    suffix = a.suffix or "GenAI - %s%s" % (label, " DEMO" if ca.mode == "demo" else "")
    out_dir = Path(os.path.expanduser(a.out_dir)) if a.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / ("%s (%s)%s" % (src.stem, suffix, src.suffix))
    text = "<?xml version='1.0' encoding='utf-8' ?>\n" + prologue.lstrip("\n") + serialized + "\n"
    ET.fromstring(text.encode("utf-8"))  # re-parse check
    secret_inside = bool(demo_values)
    mode = 0o600 if secret_inside else 0o644
    if tmp:
        twb_path.write_text(text, encoding="utf-8")
        root_dir = tmp.resolve()
        fd = _open_private(out_path, mode)
        with os.fdopen(fd, "wb") as raw_fh, zipfile.ZipFile(raw_fh, "w", zipfile.ZIP_DEFLATED) as z:
            # re-zip only what extractall() actually wrote under tmp: a crafted member
            # name ('/abs/path', '../x') must never pull a file from elsewhere on disk
            for pth in sorted(root_dir.rglob("*")):
                if pth.is_file() and not pth.is_symlink():
                    z.write(pth, pth.relative_to(root_dir).as_posix())
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        fd = _open_private(out_path, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)

    report = {"output": str(out_path), "provider": ca.provider, "mode": ca.mode,
              "url": genai_calc.resolve_url(ca.provider, ca), "tls": ca.tls,
              "embedded_secret": secret_inside, "created_scaffold": created_scaffold,
              "changes": log, "size_bytes": out_path.stat().st_size,
              "original_size_bytes": src.stat().st_size}
    if created_scaffold:
        report["next_steps"] = [
            "Create a sheet 'AI Response': drag Generative AI Response to Text (it is a table calc; "
            "compute along nothing / Table).",
            "Create a sheet 'Analyze' with a calc \"Analyze Using Agentic AI\" on Rows and Payload on Detail.",
            "Dashboard > Actions > Change Parameter: source = Analyze sheet, target = Grounding, "
            "field = Payload, aggregation = Concatenate (select marks to load grounding).",
            "Show the Prompts, Prompt and Select LLM parameter controls on the dashboard."]
    if a.report:
        json.dump(report, open(a.report, "w"), indent=2)
    print(json.dumps(report, indent=2))
    if secret_inside:
        print("\nWARNING: %s embeds a live credential (chmod 600). Demo use only: do not "
              "publish, share, or commit it." % out_path, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
