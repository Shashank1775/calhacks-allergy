/**
 * The one interface every screen reads live tables through.
 *
 * Reads: nothing itself; implementations fill it (FakeStateSource now, the
 * partner's SpacetimeDB bindings later).
 * Rules: fail closed. A source that is missing or not `live` must never let a
 * screen show PASS (see badge.ts).
 */
import type {
  BinProfile,
  Entity,
  EntityState,
  Interaction,
  PlateBinding,
  Ticket,
  Verdict,
  Zone,
} from "../types/TEMPORARY";

export type FeedStatus = "connecting" | "live" | "lost";

export type Tables = {
  ticket: Ticket[];
  bin_profile: BinProfile[];
  entity: Entity[];
  entity_state: EntityState[];
  plate_binding: PlateBinding[];
  interaction: Interaction[];
  verdict: Verdict[];
  zone: Zone[];
};

export type TableName = keyof Tables;

export interface StateSource {
  /** Shown in the banner, so a replay is never mistaken for the live kitchen. */
  label: string;
  is_replay: boolean;
  /** The table's rows. The array is replaced, never mutated, when the table changes. */
  rows<T extends TableName>(table: T): Tables[T];
  feed_status(): FeedStatus;
  /** Calls `listener` after any change; returns the unsubscribe function. */
  subscribe(listener: () => void): () => void;
  /** Connects (or starts a replay). The feed stays `connecting` until data is in. */
  start(): void;
}

export function empty_tables(): Tables {
  return {
    ticket: [],
    bin_profile: [],
    entity: [],
    entity_state: [],
    plate_binding: [],
    interaction: [],
    verdict: [],
    zone: [],
  };
}
