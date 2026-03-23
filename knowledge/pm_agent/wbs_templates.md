# PM Agent — WBS Templates

## Standard Software Delivery WBS

```json
{
  "phase_1_discovery": {
    "name": "Discovery & Requirements",
    "weeks": "1-2",
    "deliverables": ["Stakeholder interviews", "Current state analysis", "Requirements brief"],
    "owner": "BA Agent + PM"
  },
  "phase_2_design": {
    "name": "Design & Architecture",
    "weeks": "3-4",
    "deliverables": ["Technical architecture doc", "UI/UX wireframes", "Data model"],
    "owner": "CTO + Design Lead"
  },
  "phase_3_build": {
    "name": "Build & Integration",
    "weeks": "5-9",
    "deliverables": ["Working software (sprints)", "API integrations", "Unit tests"],
    "owner": "Engineering Team + Vendor"
  },
  "phase_4_qa": {
    "name": "QA & Testing",
    "weeks": "10-11",
    "deliverables": ["QA audit report", "UAT sign-off", "Performance test results"],
    "owner": "QA Agent + PMO"
  },
  "phase_5_delivery": {
    "name": "Delivery & Handoff",
    "weeks": "12",
    "deliverables": ["Production deployment", "Handoff documentation", "Training session"],
    "owner": "PM + CTO"
  }
}
```

## AI/ML Project WBS Additions
When the project includes AI or ML components, add to Build phase:
- Model training pipeline
- Data validation and quality gates
- Model performance baseline (F1/accuracy thresholds agreed with client)
- Explainability documentation

## 3D / Spatial Computing Additions (e.g., AURA MVP)
When the project includes silhouette or mesh work, add:
- 3D asset pipeline setup
- Mesh validation and QA pass
- Performance profiling (frame rate, load time targets)
- Cross-device compatibility testing (mobile + desktop)
