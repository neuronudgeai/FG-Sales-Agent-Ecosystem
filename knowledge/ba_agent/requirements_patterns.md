# BA Agent — Requirements Extraction Patterns

## FR / NFR Classification Rules

### Functional Requirements (FR) — "the system SHALL"
Triggered by phrases: "we need", "must support", "should be able to", "allow users to",
"the system will", "integrate with", "process", "generate", "send", "display"

### Non-Functional Requirements (NFR) — quality attributes
Triggered by phrases: "performance", "scalable", "secure", "available", "fast",
"response time", "concurrent users", "uptime", "encrypted", "compliant"

## Numbering Convention
- FR1, FR2, FR3 ... (functional)
- NFR1, NFR2, NFR3 ... (non-functional)
- Each requirement gets a unique ID that never changes — used for traceability

## Acceptance Criteria Format
Each requirement needs at least one acceptance criterion:

```
FR3 — User Authentication
Acceptance: Given a registered user, when they submit valid credentials,
then the system grants access within 2 seconds and logs the event.
```

## Traceability Matrix Template
```json
{
  "FR1": {"requirement": "...", "design_reference": "wireframe-001", "test_case": "TC-001", "status": "OPEN"},
  "NFR1": {"requirement": "...", "design_reference": "arch-doc-001", "test_case": "TC-020", "status": "OPEN"}
}
```

## Common Extraction Mistakes to Avoid
- Don't merge two requirements into one FR (split compound statements)
- Don't skip implied requirements (if they say "login", imply: register, forgot password, logout)
- Don't accept vague NFRs — always convert to measurable: "fast" → "< 2s response time at p95"
- Don't duplicate: one concept = one FR/NFR

## Lessons Learned — Requirements
- **LESSON-BA-001:** Clients often conflate UX preferences with functional requirements.
  Always separate "the system must do X" from "the client prefers Y styling."
- **LESSON-BA-002:** Design sessions often omit security requirements. Always add at
  minimum: authentication, authorization, and data encryption NFRs.
- **LESSON-BA-003:** "Integration with existing systems" is always underestimated.
  Create a dedicated FR for each external system integration.
- **LESSON-BA-004:** Confirm with client that requirements are complete before
  advancing to design. Undiscovered requirements after design cost 5× more to fix.
