/**
 * What the gate shows for the plate at the pass, for the gate panel on /expo.
 *
 * Reads: zone, entity, entity_state, plate_binding, ticket, verdict; the feed status; vision health.
 * Rules: fail closed, plain-code verdict. The same order as kitchen/gate/service.py's gate_state:
 * stale inputs give FAULT, two plates at the pass give HOLD, no plate or no verdict yet gives
 * IDLE (closed), and only plate_badge()'s PASS opens it. A plate counts at the pass only while
 * vision still tracks it, and one seen in the last 5 s still crowds it (plates_at_pass). This
 * decides nothing new: PASS comes only from plate_badge(), which only reads verdict rows.
 */
import { latest_verdict, plate_badge } from "./badge";
import type { FeedStatus, Tables } from "./state/state_source";
import type { EntityState, Ticket, Verdict } from "./types/TEMPORARY";
import type { VisionHealth } from "./vision_health";

export type GateState = "idle" | "pass" | "hold" | "fault";

export type GateView = {
  state: GateState;
  /** Why it isn't open, in a few words, when that isn't the verdict's own reasons. */
  note: string | null;
  plate_id: string | null;
  ticket: Ticket | undefined;
  verdict: Verdict | undefined;
};

export const GATE_TEXT: Record<GateState, string> = {
  idle: "WAITING",
  pass: "PASS",
  hold: "HOLD",
  fault: "FAULT",
};

type GateTables = Pick<Tables, "zone" | "entity" | "entity_state" | "plate_binding" | "ticket" | "verdict">;

export function gate_view(feed: FeedStatus, vision: VisionHealth, tables: GateTables): GateView {
  const closed = (state: GateState, note: string | null): GateView => ({
    state,
    note,
    plate_id: null,
    ticket: undefined,
    verdict: undefined,
  });

  if (feed !== "live") return closed("fault", "The kitchen feed is lost");
  if (vision === "stale") return closed("fault", "The camera stopped sending frames");

  // The two windows on how long a plate at the pass has gone unseen; see plates_at_pass.
  const plates = plates_at_pass(tables);
  const crowding = plates.filter((plate) => plate.unseen_ms <= LOST_PLATE_CROWDS_MS);
  const at_pass = plates.filter((plate) => plate.unseen_ms <= PLATE_LOST_MS);
  if (crowding.length > 1) return closed("hold", "Two plates at the pass: one at a time");
  if (at_pass.length === 0) return closed("idle", "No plate at the pass");

  const plate_id = at_pass[0].plate_id;
  const binding = tables.plate_binding.find((row) => row.plate_id === plate_id);
  const ticket = binding ? tables.ticket.find((row) => row.ticket_id === binding.ticket_id) : undefined;
  const verdicts = tables.verdict.filter((row) => row.plate_id === plate_id);
  const badge = plate_badge(feed, ticket, verdicts);
  const view = { plate_id, ticket, verdict: latest_verdict(verdicts) };

  if (badge === "pass") return { ...view, state: "pass", note: null };
  if (badge === "waiting") return { ...view, state: "idle", note: "Checking this plate" };
  if (badge === "rechecking") return { ...view, state: "hold", note: "The ticket changed: rechecking the plate" };
  if (!ticket) return { ...view, state: "hold", note: "This plate's ticket isn't known" };
  return { ...view, state: "hold", note: null };
}

/**
 * Same as GateSettings.plate_lost_ms: a copy of vision's track_lost_ms (1000 ms,
 * kitchen/vision/common.py), and must change with it.
 */
export const PLATE_LOST_MS = 1000;

/** Same as GateSettings.lost_plate_crowds_ms. */
export const LOST_PLATE_CROWDS_MS = 5000;

export type PlateAtPass = { plate_id: string; unseen_ms: number };

/**
 * Plates whose latest position is a pass zone, each with how long it had gone unseen at vision's
 * newest sighting: the same rule as kitchen/gate/service.py's plates_at_pass() and read_pass().
 *
 * A plate lifted out of view keeps a last row that says pass, so its zone alone can't say it
 * left. Vision ends a track once its object goes unseen for track_lost_ms and never revives it.
 * So gate_view takes a plate unseen for up to PLATE_LOST_MS as at the pass (only its verdict can
 * drive the gate; a lone plate lifted out goes WAITING 1 s after it was last seen), and one unseen
 * for up to LOST_PLATE_CROWDS_MS as still counting toward two plates at the pass (HOLD). A hand
 * can hide a plate for over a second; seen again, it is a new unbound plate (alone it would show
 * WAITING), and without that window the gate would open for a PASS plate beside it.
 *
 * What's left, on purpose: a plate hidden for over LOST_PLATE_CROWDS_MS beside a PASS plate lets
 * the gate open for that plate until the hidden one is seen again; the demo keeps one plate at the
 * pass at a time. The cost: after a plate is lifted out, the next plate can't pass for up to
 * LOST_PLATE_CROWDS_MS. Unseen time is measured against the newest row of any entity, not this
 * browser's clock: rows carry vision's frame times, so feed lag can't age out a plate vision
 * still has.
 */
export function plates_at_pass(tables: Pick<GateTables, "zone" | "entity" | "entity_state">): PlateAtPass[] {
  const pass_zones = new Set(tables.zone.filter((zone) => zone.kind === "pass").map((zone) => zone.zone_id));
  const plate_ids = new Set(
    tables.entity.filter((entity) => entity.kind === "plate").map((entity) => entity.entity_id),
  );
  const latest = latest_states(tables.entity_state);
  const newest_t_ms = newest_sighting(latest);
  const plates: PlateAtPass[] = [];
  for (const [entity_id, state] of latest) {
    const at_pass = state.zone !== null && pass_zones.has(state.zone);
    if (plate_ids.has(entity_id) && at_pass) plates.push({ plate_id: entity_id, unseen_ms: newest_t_ms - state.t_ms });
  }
  return plates;
}

/** The newest t_ms among entities' latest states: when vision last saw anything. */
function newest_sighting(latest: Map<string, EntityState>): number {
  let newest = -Infinity;
  for (const state of latest.values()) newest = Math.max(newest, state.t_ms);
  return newest;
}

/** Each entity's most recent state: the highest t_ms, and the row written last on a tie. */
export function latest_states(states: EntityState[]): Map<string, EntityState> {
  const latest = new Map<string, EntityState>();
  for (const state of states) {
    const seen = latest.get(state.entity_id);
    if (!seen || state.t_ms >= seen.t_ms) latest.set(state.entity_id, state);
  }
  return latest;
}
