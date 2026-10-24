/**
 * FakeStateSource: replays one golden scenario in the browser.
 *
 * Reads: a contracts/scenarios/*.json file.
 * Emits: the same tables the SpacetimeDB module will hold (state-tables.md).
 * Rules: this is a replay. `is_replay` is true so every screen shows a REPLAY
 * banner. Verdicts come straight from the scenario's `expected` entries; this
 * file never decides one.
 */
import type { Entity, Interaction, Scenario, Verdict } from "../types/TEMPORARY";
import { empty_tables, type FeedStatus, type StateSource, type TableName, type Tables } from "./state_source";

/** How long an amended ticket shows RECHECKING before its new verdict lands. */
const RECHECK_DELAY_MS = 1500;
const TICK_MS = 100;

type Step = { t_ms: number; table: TableName; key: string | null; row: unknown };

export class FakeStateSource implements StateSource {
  label: string;
  is_replay = true;

  private tables: Tables = empty_tables();
  private status: FeedStatus = "connecting";
  private listeners = new Set<() => void>();
  private steps: Step[];
  private next_step = 0;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(scenario: Scenario) {
    this.label = scenario.scenario_id;
    this.steps = build_timeline(scenario);
  }

  rows<T extends TableName>(table: T): Tables[T] {
    return this.tables[table];
  }

  feed_status(): FeedStatus {
    return this.status;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Goes live and replays the scenario in real time from t = 0. */
  start(): void {
    const started_at = Date.now();
    this.status = "live";
    this.advance_to(0);
    this.timer = setInterval(() => this.advance_to(Date.now() - started_at), TICK_MS);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.status = "lost";
    this.notify();
  }

  /** Applies every step up to `t_ms`. Tests call this directly instead of waiting. */
  advance_to(t_ms: number): void {
    let changed = false;
    while (this.next_step < this.steps.length && this.steps[this.next_step].t_ms <= t_ms) {
      apply_step(this.tables, this.steps[this.next_step]);
      this.next_step += 1;
      changed = true;
    }
    if (changed) this.notify();
  }

  /** For tests: mark the feed live without starting the clock. */
  go_live(): void {
    this.status = "live";
    this.notify();
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}

/** Upserts by key (or appends when there is no key), replacing the table's array. */
function apply_step(tables: Tables, step: Step): void {
  const current = tables[step.table] as unknown[];
  const key_field = KEY_FIELDS[step.table];
  const next =
    step.key === null || key_field === null
      ? [...current, step.row]
      : [...current.filter((row) => (row as Record<string, unknown>)[key_field] !== step.key), step.row];
  (tables as Record<TableName, unknown[]>)[step.table] = next;
}

const KEY_FIELDS: Record<TableName, string | null> = {
  ticket: "ticket_id",
  bin_profile: "entity_id",
  entity: "entity_id",
  entity_state: null,
  plate_binding: "plate_id",
  interaction: "event_id",
  verdict: "verdict_id",
};

/** Turns a scenario into time-ordered table writes. */
export function build_timeline(scenario: Scenario): Step[] {
  const steps: Step[] = [];
  const add = (t_ms: number, table: TableName, key: string | null, row: unknown) =>
    steps.push({ t_ms, table, key, row });

  for (const bin of scenario.bins ?? []) {
    add(0, "entity", bin.entity_id, entity_row(bin.entity_id, bin.label, "shift_start", 0));
    add(0, "bin_profile", bin.entity_id, { entity_id: bin.entity_id, ...bin.allergen_profile });
  }

  const late = new Map((scenario.late_entities ?? []).map((late_entity) => [late_entity.entity_id, late_entity]));
  for (const late_entity of late.values()) {
    add(
      late_entity.first_seen_t_ms,
      "entity",
      late_entity.entity_id,
      entity_row(late_entity.entity_id, late_entity.entity_id, late_entity.origin, late_entity.first_seen_t_ms),
    );
  }

  // A ticket row holds the latest revision; an amendment replaces it.
  for (const ticket of [...scenario.tickets, ...(scenario.amendments ?? [])]) {
    add(ticket.created_t_ms, "ticket", ticket.ticket_id, ticket);
  }

  for (const binding of scenario.plate_bindings) {
    add(
      binding.t_ms,
      "entity",
      binding.plate_id,
      entity_row(binding.plate_id, `plate for ${binding.ticket_id}`, "shift_start", binding.t_ms),
    );
    add(binding.t_ms, "plate_binding", binding.plate_id, binding);
  }

  const known = new Set([
    ...(scenario.bins ?? []).map((bin) => bin.entity_id),
    ...late.keys(),
    ...scenario.plate_bindings.map((b) => b.plate_id),
  ]);
  for (const event of scenario.events ?? []) {
    for (const id of [event.a_id, event.b_id]) {
      if (!known.has(id)) {
        known.add(id);
        add(0, "entity", id, entity_row(id, id, "shift_start", 0));
      }
    }
    add(event.t_start_ms, "interaction", event.event_id, event);
  }

  for (const expected of scenario.expected) {
    const verdict = verdict_from_expected(scenario, expected);
    if (verdict) add(verdict.decided_t_ms, "verdict", verdict.verdict_id, verdict);
  }

  return steps.sort((a, b) => a.t_ms - b.t_ms);
}

function entity_row(entity_id: string, label: string, origin: Entity["origin"], t_ms: number): Entity {
  return { entity_id, kind: kind_from_id(entity_id), label, origin, first_seen_t_ms: t_ms };
}

function kind_from_id(entity_id: string): Entity["kind"] {
  if (entity_id.startsWith("bin-")) return "bin";
  if (entity_id.startsWith("hand-")) return "hand";
  if (entity_id.startsWith("plate-")) return "plate";
  return "tool";
}

/**
 * Builds the verdict row a scenario says should appear. A revision-1 verdict
 * lands when the plate reaches the pass; a later revision's verdict lands a
 * little after its amendment, so the screen shows RECHECKING in between.
 */
function verdict_from_expected(scenario: Scenario, expected: Scenario["expected"][number]): Verdict | null {
  const at_pass = scenario.plates_at_pass.find((plate) => plate.plate_id === expected.plate_id);
  if (!at_pass) return null;

  const all_revisions = [...scenario.tickets, ...(scenario.amendments ?? [])];
  const ticket = all_revisions.find(
    (candidate) => candidate.ticket_id === at_pass.ticket_id && candidate.revision === expected.ticket_revision,
  );
  if (!ticket) return null;

  const is_amendment = expected.ticket_revision > 1;
  const decided_t_ms = is_amendment ? ticket.created_t_ms + RECHECK_DELAY_MS : at_pass.t_ms;
  const flagged = expected.allergens_reached.filter((id) => ticket.forbidden.includes(id));

  return {
    verdict_id: `v-${expected.plate_id}-r${expected.ticket_revision}`,
    ticket_id: ticket.ticket_id,
    ticket_revision: ticket.revision,
    plate_id: expected.plate_id,
    decided_t_ms,
    trigger: is_amendment ? "ticket_amended" : "plate_at_pass",
    decision: expected.decision,
    confidence: expected.decision === "pass" ? 1 : 0,
    pass_threshold: ticket.pass_threshold,
    reasons: reasons_for(expected, flagged),
    flagged: flagged.map((allergen_unit_id) => ({ allergen_unit_id, probability: 1 })),
    chain: chain_for(
      scenario.events ?? [],
      expected.chain_event_ids ?? [],
      flagged[0] ?? expected.allergens_reached[0] ?? "",
    ),
    versions: { policy: "fake-replay" },
  };
}

function reasons_for(expected: Scenario["expected"][number], flagged: string[]): Verdict["reasons"] {
  if (expected.decision === "pass") return [];
  const codes = expected.reason_codes ?? [];
  // A hold always carries a reason. If the scenario gives none, say so rather than invent one.
  if (codes.length === 0) return [{ code: "error", detail: "the scenario lists no reason" }];
  return codes.map((code) =>
    code === "forbidden_allergen" && flagged[0] ? { code, allergen_unit_id: flagged[0] } : { code },
  );
}

/** Orders the chain's events into from → to steps. */
function chain_for(events: Interaction[], event_ids: string[], allergen_unit_id: string): Verdict["chain"] {
  let previous_to: string | null = null;
  return event_ids.flatMap((event_id, index) => {
    const event = events.find((candidate) => candidate.event_id === event_id);
    if (!event) return [];
    const forward = event.direction === "a_to_b" || (event.direction === "both" && previous_to !== event.b_id);
    const from_id = forward ? event.a_id : event.b_id;
    const to_id = forward ? event.b_id : event.a_id;
    previous_to = to_id;
    return [
      {
        step: index + 1,
        event_id,
        from_id,
        to_id,
        t_ms: event.t_start_ms,
        action: event.action,
        allergen_unit_id,
        step_confidence: event.probability,
      },
    ];
  });
}
