# How patch_workbook.py edits the workbook

## Finding the scaffold

1. The **Response** calc: a column whose formula contains `SCRIPT_`.
2. **Grounding** / **Prompt**: the parameter-action (`edit-parameter-action`)
   target is Grounding; its source field is the Payload aggregation. Prompt,
   Select LLM, Token Count are found by caption, then by the Response's args.
3. **Prompts**: an existing string list parameter captioned `Prompts` is
   reused; otherwise a new one gets the next free `[Parameter N]` name.

Internal names are never changed, so sheets, actions and filters keep working.
If no scaffold exists, Parameters + calcs are created and the script prints
the manual steps left (Response sheet, parameter action, dashboard layout).

## Every copy must change

A column appears in the datasource **and** in each worksheet/dashboard
`<datasource-dependencies>` block. `set_formula_everywhere` and
`set_list_param` update all copies. Worksheet copies of list parameters keep
`<aliases>` but no `<members>`; dashboard copies keep `<members>`.

## String / list parameter XML

```xml
<column caption='Prompts' datatype='string' name='[Parameter 1]'
        param-domain-type='list' role='measure' type='nominal'
        value='"PERSONA: …&#10;AUDIENCE: …"' alias='Executive Safety Brief - …'>
  <calculation class='tableau' formula='"PERSONA: …&#10;…"' />
  <aliases><alias key='"PERSONA: …"' value='Executive Safety Brief - …' /></aliases>
  <members><member alias='Executive Safety Brief - …' value='"PERSONA: …"' /></members>
</column>
```
Values are Tableau string literals (`"` inside → `""`); newlines are `&#10;`.

## Formula

`genai_calc.py` emits `SCRIPT_STR("<python>",\nARG1,\nARG2, …\n)` with caption
references; the patcher rewrites them to internal names
(`[Parameters].[Parameter 1]`). The Response keeps its
`<table-calc ordering-type='Rows'/>`.

## Dashboard control

`add_param_zone` inserts a compact Prompts control next to the existing
Prompt control in **every** layout (main + device layouts, which reuse zone
ids), taking ~1/3 of the Prompt zone's width. New zone id = max + 1.

## Writing

ElementTree drops the `<?xml?>` prologue and comments → the patcher restores
the prologue and `<!-- build … -->` line and registers the `user` namespace.
`.twbx`: the inner `.twb` is replaced and the archive re-zipped with the
other members untouched. Output name: `<stem> (GenAI - <Provider>[ DEMO]).twb[x]`
next to the source (or `--out-dir`). The original is never modified.

## Validation before writing

* every `[…]` reference in changed formulas resolves
* keystore build: no credential-looking literal; source's old key absent
* zone ids unique per layout
* the output re-parses

Saved thumbnails (~0.8 MB on large dashboards) are kept; Tableau regenerates
them on save.
