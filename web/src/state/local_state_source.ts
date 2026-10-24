/**
 * LocalStateSource: polls the local runner (tools/run_local.py) for every table.
 *
 * Reads: GET <base_url>/state → {scenario_id, t_ms, tables}, the runner's
 * snapshot of the tables in state-tables.md.
 * Emits: the tables the screens use, as the runner sent them. The allergen
 * table is ignored; zone may be missing, which leaves the gate panel closed.
 * Rules: fail closed. Any failed poll (network error, timeout, an error status,
 * a body that isn't JSON or is missing a table) sets the feed to `lost`, so
 * every screen shows FEED LOST and no badge can show PASS. The last rows stay on
 * screen until a good poll brings the feed back. The runner replays a golden
 * scenario on fakes, so `is_replay` is true and the banner says REPLAY.
 */
import { empty_tables, type FeedStatus, type StateSource, type TableName, type Tables } from "./state_source";

/** How often to ask for a snapshot. The runner itself steps every 250 ms. */
export const POLL_MS = 500;

/** A poll slower than this counts as a lost feed. */
export const POLL_TIMEOUT_MS = 2000;

const TABLE_NAMES = Object.keys(empty_tables()) as TableName[];

// A missing optional table reads as empty. Without zones nothing is at the pass, so the gate stays closed.
const OPTIONAL_TABLES: TableName[] = ["zone"];

type Snapshot = { scenario_id: string; tables: Tables };

export class LocalStateSource implements StateSource {
  label = "local runner";
  is_replay = true;

  private base_url: string;
  private tables: Tables = empty_tables();
  // The JSON of each table as last stored, so a poll that brings the same rows keeps the same array.
  private table_json = {} as Record<TableName, string>;
  private status: FeedStatus = "connecting";
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  /** `base_url` is the runner's address with no trailing slash, e.g. http://localhost:8001. */
  constructor(base_url: string) {
    this.base_url = base_url;
    for (const name of TABLE_NAMES) this.table_json[name] = JSON.stringify(this.tables[name]);
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

  /** Polls now, then POLL_MS after each poll settles, so two polls never overlap. */
  start(): void {
    this.stopped = false;
    void this.poll_then_schedule();
  }

  /** Stops polling and marks the feed lost; the last rows stay, as when the runner drops. */
  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.status = "lost";
    this.notify();
  }

  /** Fetches one snapshot and applies it. Never throws: any failure sets the feed to `lost`. */
  async poll_once(): Promise<void> {
    const snapshot = await fetch_snapshot(this.base_url);
    // A poll that lands after stop() must not bring the feed back to live.
    if (this.stopped) return;
    if (snapshot) {
      this.apply(snapshot);
    } else {
      this.lose();
    }
  }

  private async poll_then_schedule(): Promise<void> {
    await this.poll_once();
    if (!this.stopped) this.timer = setTimeout(() => void this.poll_then_schedule(), POLL_MS);
  }

  /** Goes live and replaces only the tables whose rows changed, so screens re-render only on change. */
  private apply(snapshot: Snapshot): void {
    let changed = this.status !== "live";
    this.status = "live";

    const label = `local runner · ${snapshot.scenario_id}`;
    if (label !== this.label) {
      this.label = label;
      changed = true;
    }

    for (const name of TABLE_NAMES) {
      const json = JSON.stringify(snapshot.tables[name]);
      if (json !== this.table_json[name]) {
        this.table_json[name] = json;
        (this.tables as Record<TableName, unknown[]>)[name] = snapshot.tables[name];
        changed = true;
      }
    }

    if (changed) this.notify();
  }

  private lose(): void {
    if (this.status === "lost") return;
    this.status = "lost";
    this.notify();
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}

/** One snapshot from the runner, or null if the poll failed in any way. */
async function fetch_snapshot(base_url: string): Promise<Snapshot | null> {
  try {
    const response = await fetch(`${base_url}/state`, { signal: AbortSignal.timeout(POLL_TIMEOUT_MS) });
    if (!response.ok) return null;
    return parse_snapshot(await response.json());
  } catch {
    // A network error, a timeout or a body that isn't JSON: we can't see the kitchen.
    return null;
  }
}

/**
 * Checks the snapshot's outline: a scenario id and an array for each table
 * (zone may be left out). Rows are passed on as sent: the runner writes every ticket and
 * verdict through FakeStateClient, which checks each against its contract
 * schema, and keeps one row per primary key.
 */
function parse_snapshot(body: unknown): Snapshot | null {
  if (typeof body !== "object" || body === null) return null;

  const { scenario_id, tables } = body as { scenario_id?: unknown; tables?: unknown };
  if (typeof scenario_id !== "string") return null;
  if (typeof tables !== "object" || tables === null) return null;

  const checked = { ...(tables as Record<string, unknown>) };
  for (const name of TABLE_NAMES) {
    if (checked[name] === undefined && OPTIONAL_TABLES.includes(name)) checked[name] = [];
    if (!Array.isArray(checked[name])) return null;
  }
  return { scenario_id, tables: checked as Tables };
}
