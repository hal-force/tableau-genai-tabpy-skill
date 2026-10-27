# Prompt engineering for the Prompts parameter

Each **Prompts** member is a complete, reusable instruction. The calc wraps it
like this before sending:

```
<PROMPT (from the Prompts parameter, if one is selected)>

ANALYST QUESTION: <Prompt box text, only if not empty>

GROUNDING DATA (the only evidence you may use; cite record IDs):
<Payload records, concatenated by Tableau>

ANALYSIS QUALITY SELF-ASSESSMENT (REQUIRED): … COVERAGE / CONFIDENCE /
DATA_GAPS / RECOMMENDED_ACTION block …

REMINDER OF ANALYST TASK: <the question, or "Complete the OBJECTIVE stated
above for the stated AUDIENCE.">
```

So a prompt never needs to repeat "use the data below" — it needs to say
**who is answering, for whom, to do what, under which rules, in what shape**.

## Template (what `compose_prompts.py` renders)

```
PERSONA: You are <role with domain expertise and point of view>.
AUDIENCE: <who reads it, what they know, how much time, what decision>.
OBJECTIVE: <the job, phrased as an outcome>.
CONSTRAINTS:
- <prompt-specific rules>
- <shared rules: grounding only, citations, naming, inference labelling…>
OUTPUT FORMAT: <exact structure: BLUF, table columns, list shape, length>.
```

## Deriving the questions from the workflow

Work backwards from the dashboard's user and the decision it supports.
From the profile (`profile.md`) and one AskUserQuestion round, fill:

| Ask | Typical prompts it yields |
|---|---|
| Who opens this dashboard, and who do *they* report to? | Executive brief (BLUF) for the boss; working analysis for the analyst |
| What do they look for over time / across groups? | Recurring issues & trends |
| What do they have to explain? | Root cause / contributing factors |
| What do they have to track to closure? | Action / commitment tracker |
| Where do they spend limited resources? | Risk prioritization / ranking |
| What's their next meeting? | Questions to ask, talking points |

Aim for 4–7 prompts; more makes the dropdown hard to scan. Keep the label
2–4 words and the summary ≤ ~45 chars so *Display As* stays ≤ 80.

## Shared constraints that matter for grounded analysis

* Use only the grounding data; say "not in the selected records" rather than
  filling gaps.
* Cite each finding with the record id **and** date fields that are in the
  Payload — so put them in the Payload.
* Keep names (sites, units, programs) exactly as written; expand acronyms once.
* Separate stated facts from inference; label inference.
* House rules: classification/handling markings, no PII, no speculation about
  personnel or sensitive matters.

## Spec format

See `examples/dnfsb/prompts_spec.json`:

```json
{
  "domain": "...", "parameter_caption": "Prompts",
  "free_text_label": "Free text (use the Prompt box)",
  "shared_constraints": ["..."],
  "default": "Executive Safety Brief",
  "prompts": [{"label": "...", "summary": "...", "persona": "...",
               "audience": "...", "objective": "...",
               "constraints": ["..."], "output": "..."}]
}
```

`compose_prompts.py` replaces `"` with `'` (Tableau parameter values are
double-quoted literals) and reports estimated tokens per prompt; prompts are
small (~300–500 tokens) next to the grounding.
