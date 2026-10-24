/**
 * Hand-written copies of the contract shapes the screens use.
 *
 * TEMPORARY: platform generates TS types from contracts/schemas into
 * web/src/generated/. When that lands, import from there and delete this file.
 */

export type AllergenId = string;
export type EntityId = string;

export type Ticket = {
  ticket_id: string;
  number: number;
  revision: number;
  created_t_ms: number;
  source: "typed" | "voice";
  note: string;
  pass_threshold: number;
  forbidden: AllergenId[];
};

export type Severity = "preference" | "intolerance" | "allergy" | "anaphylactic";

export type ParsedConstraint = {
  allergen_unit_id: AllergenId;
  severity: Severity;
  trace_sensitive: boolean;
  confidence: { allergen_unit_id: number; severity: number; trace_sensitive: number };
};

export type ParsedException = {
  allergen_unit_id: AllergenId;
  ingredient: string;
  confidence: number;
};

export type QuestionOption = { label: string; value: unknown };

export type Question = {
  question_id: string;
  target: { kind: "constraint" | "exception" | "new_constraint"; index?: number; field: string };
  prompt: string;
  options: QuestionOption[];
  answer?: unknown;
};

export type ParseResult = {
  draft_id: string;
  note: string;
  source: "typed" | "voice";
  parser: { name: string; version: string };
  constraints: ParsedConstraint[];
  exceptions: ParsedException[];
  questions: Question[];
  question_threshold: number;
  sendable: boolean;
};

export type AllergenProfile = {
  probabilities: Record<AllergenId, number>;
  unknown: boolean;
  source: "laya" | "manual" | "shift_start_clean" | "propagated" | "wash_reset" | "unknown_history";
};

export type BinProfile = AllergenProfile & { entity_id: EntityId };

export type Entity = {
  entity_id: EntityId;
  kind: "bin" | "tool" | "hand" | "plate";
  label: string;
  origin: "shift_start" | "clean_stack" | "unknown";
  first_seen_t_ms: number;
};

export type EntityState = {
  state_id: string;
  entity_id: EntityId;
  version: number;
  t_ms: number;
  zone: string | null;
  box: [number, number, number, number] | null;
  probabilities: Record<AllergenId, number>;
  unknown: boolean;
  caused_by: string | null;
};

export type PlateBinding = { plate_id: EntityId; ticket_id: string; t_ms: number };

export type Interaction = {
  event_id: string;
  t_start_ms: number;
  t_end_ms: number;
  a_id: EntityId;
  b_id: EntityId;
  action: string;
  probability: number;
  direction: "a_to_b" | "b_to_a" | "both";
  classifier: { name: string; version: string };
};

// A reason code is typed as a plain string on purpose: a code this screen does
// not know still has to render (as a hold), never vanish.
export type Reason = { code: string; allergen_unit_id?: AllergenId; detail?: string };

export type ChainStep = {
  step: number;
  event_id: string;
  from_id: EntityId;
  to_id: EntityId;
  t_ms: number;
  action: string;
  allergen_unit_id: AllergenId;
  step_confidence: number;
};

export type Verdict = {
  verdict_id: string;
  ticket_id: string;
  ticket_revision: number;
  plate_id: EntityId;
  decided_t_ms: number;
  trigger: "plate_at_pass" | "ticket_amended";
  decision: "pass" | "hold";
  confidence: number;
  pass_threshold: number;
  reasons: Reason[];
  flagged: { allergen_unit_id: AllergenId; probability: number }[];
  chain: ChainStep[];
  versions: Record<string, string>;
};

export type Scenario = {
  scenario_id: string;
  description: string;
  bins?: { entity_id: EntityId; label: string; allergen_profile: AllergenProfile }[];
  tickets: Ticket[];
  amendments?: Ticket[];
  late_entities?: { entity_id: EntityId; first_seen_t_ms: number; origin: Entity["origin"] }[];
  plate_bindings: PlateBinding[];
  events?: Interaction[];
  plates_at_pass: { plate_id: EntityId; ticket_id: string; t_ms: number }[];
  expected: {
    plate_id: EntityId;
    ticket_revision: number;
    decision: "pass" | "hold";
    allergens_reached: AllergenId[];
    reason_codes?: string[];
    chain_event_ids?: string[];
  }[];
};
