# Team charter: `<team>`

> Copy to `teams/<team>/CHARTER.md`. Owned by the team's Manager; changes go through the Director. Keep it under ~150 lines. Put task-level detail in issues, not here.

## Mission

One or two sentences: what this team makes true about the system.

## Owns

- Paths: `components/<team>/` (and anything else in [OWNERS](../../OWNERS)).
- Pipeline stages from [ARCHITECTURE.md](../ARCHITECTURE.md).

## Does not own

Things people might assume are ours but aren't, and who owns them instead.

## Produces

| Contract or interface | Consumers | Fake we ship |
|---|---|---|

## Consumes

| Contract or interface | Producer | Fake we test against |
|---|---|---|

## Invariants we guard

Which of the nine invariants this team's code can break, and the test (marked `safety`) that guards each.

## Stack and layout

Language, key libraries, how the component is laid out, the test command. Differences from [CONVENTIONS.md](../agents/CONVENTIONS.md) need a team ADR.

## Deliverables by milestone

For each of M0–M4 from [ROADMAP.md](../ROADMAP.md): what this team must deliver for that milestone's exit criteria. Mark items that need hardware.

## Working with other teams

Who to talk to for what, and the known handoffs.

## Risks and open questions

Top risks, with a mitigation for each. Cite `OQ-n` from [OPEN_QUESTIONS.md](../OPEN_QUESTIONS.md).

## Definition of done (team additions)

Anything beyond the org-wide definition in [WORKER.md](../agents/WORKER.md#definition-of-done).

## Staffing

Suggested worker count per milestone, and what kind of work suits parallel Workers vs one careful Worker.
