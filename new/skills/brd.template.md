# Functional specification — {{requirement_id}}

Business requirements and functional specification for Gate 2. Every requirement
has a traceability id reused by tickets, tests, commits and attestations.

## 1. Purpose

{{objective}}

## 2. Summary

{{summary}}

## 3. Users and roles

{{users}}

## 4. What happens today

{{current_state}}

## 5. Success

{{success}}

## 6. In scope

{{in_scope}}

## 7. Out of scope

{{out_of_scope}}

## 8. Functional requirements

{{requirements}}

## 9. Non-functional requirements

{{nfr}}

## 10. User journeys

{{journeys}}

## 11. Page behaviour

Screens the UI/UX agent must cover. A page with no screen is a gap at Gate 3.

**Inventory rules (do not break):**
- This section is the only screen list. Each screen is one flat bullet whose name is in bold, then what the screen shows.
- Do not repeat those bullets under `###` requirement headings in §8 — that duplicates Gate 3 screens.
- One bullet per screen. Same screen used by several requirements stays listed once here; requirements describe behaviour, not a second inventory.

{{page_behaviour}}

## 12. Data model

{{data_model}}

```mermaid
{{er_diagram}}
```

## 13. Security design

{{security}}

## 14. Integrations

{{integrations}}

## 15. Assumptions

{{assumptions}}

## 16. Open questions

{{open_questions}}

## 17. Traceability

Each id in §8 is the source of tickets, tests and attestations. Do not invent
requirements that are not listed there.
