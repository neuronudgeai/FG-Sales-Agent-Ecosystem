# Output Format Rules — All Agents

## Required JSON envelope
Every agent response MUST be valid JSON with these top-level keys:

```json
{
  "recommendation": "One-sentence summary of what this output recommends or delivers",
  "confidence_score": 0.85,
  "reasoning": ["reason 1", "reason 2"],
  "assumptions": ["assumption 1"],
  ...agent-specific keys...
}
```

## confidence_score Guidelines

| Score | When to use |
|-------|-------------|
| 0.90–1.00 | All inputs present, no ambiguity, matches frozen facts |
| 0.75–0.89 | Minor gaps in input data, minor assumptions made |
| 0.60–0.74 | Significant missing inputs; output needs SME review |
| < 0.60 | Do not auto-approve; always flag for review |

## reasoning and assumptions
- `reasoning` — list of factual statements that support the recommendation
- `assumptions` — things assumed true that could change the output if wrong
- Both must be non-empty lists; minimum 2 items each

## No hallucination rules
- Never claim a project is complete unless the QA gate has passed
- Never mention hiring decisions
- Never state Chevron has approved anything
- Always use AURA MVP timeline as "3 months" (not 4, not 6)
- Never invent cost figures not derived from provided budget data
