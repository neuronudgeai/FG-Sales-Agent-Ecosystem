# BA Agent — Design Session Processing Guide

## How to Process a Transcript

1. **Identify speakers** — note who said what; client statements = requirements,
   internal team statements = constraints or design decisions
2. **Extract verbatim quotes** for ambiguous requirements and flag them
3. **Classify each statement** as: FR, NFR, constraint, assumption, or out-of-scope
4. **Resolve conflicts** — if client says two contradictory things, flag both and
   mark as "NEEDS CLARIFICATION"
5. **Infer implied requirements** — a mention of "admin panel" implies user roles,
   access control, and audit logging

## Red Flags in Transcripts (Flag for human review)

| Phrase heard | What it likely implies |
|---|---|
| "similar to [competitor product]" | Benchmark NFRs needed; get specifics |
| "we'll figure that out later" | Scope risk — document as assumption |
| "shouldn't be too hard" | Complexity underestimate — flag timeline risk |
| "the old system used to..." | Legacy migration requirement likely hidden |
| "everyone on the team needs access" | Role-based access control FR required |
| "real-time" | WebSocket/event-driven NFR; performance target needed |
| "works on mobile" | Responsive design + native performance NFRs |
| "GDPR" / "HIPAA" / "SOC2" | Compliance NFRs — these are high-priority |

## Standard NFR Defaults (add if not stated)
When none are explicitly mentioned, include these as baseline NFRs with a note:
- NFR-PERF: Page load < 3s, API response < 500ms at p95
- NFR-AVAIL: 99.5% uptime (business hours)
- NFR-SEC: HTTPS, authentication required, data encrypted at rest
- NFR-SCALE: Support minimum 100 concurrent users unless stated otherwise

## Output Structure
```json
{
  "functional_requirements": [
    {"id": "FR1", "title": "...", "description": "...", "acceptance_criteria": "..."}
  ],
  "non_functional_requirements": [
    {"id": "NFR1", "category": "performance|security|scalability|availability",
     "description": "...", "acceptance_criteria": "..."}
  ],
  "clarifications_needed": ["item 1", "item 2"],
  "out_of_scope": ["item 1"],
  "traceability_matrix": {}
}
```
