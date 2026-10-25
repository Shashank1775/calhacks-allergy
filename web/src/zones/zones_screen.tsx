/**
 * Kitchen layout (/zones): where the bins, washing areas and serving area are on the counter, drawn over the camera,
 * and the place to change them. A bin slot slot-<n> holds the bin bin-<n> and its allergens; a washing area resets
 * what is washed there; the serving area (`pass`) is where each plate is judged before it leaves.
 *
 * Reads: GET <WEB_VISION_URL>/zones; entity and bin_profile rows, for bin labels only.
 * Emits: PUT <WEB_VISION_URL>/zones with the whole layout; vision uses it from the next frame.
 * Rules: vision checks every layout. A refused one (422 bad_zones) shows vision's message and keeps every edit. Save
 * stays off until a layout has loaded, so a failed load can never overwrite the kitchen's zones with an empty list.
 *
 * The home page reuses this screen as its "box the kitchen" step, over its own camera or video picture, and starts the
 * kitchen only once `on_ready` says vision has exactly the layout on screen.
 *
 * A screen opened while vision was down asks for the layout again every LAYOUT_RETRY_MS until it loads, but only while
 * no layout has loaded and nothing is drawn, so it never replaces anything on screen.
 */
import { useEffect, useState } from "react";
import { vision_url } from "../api/http";
import { get_zones, put_zones } from "../api/vision";
import { useTableRows } from "../state/hooks";
import type { Zone } from "../types/TEMPORARY";
import {
  allergen_text,
  bin_label,
  bins_without_slot,
  is_rectangle,
  kind_name,
  next_zone_id,
  ZONE_KINDS,
  type ZoneKind,
} from "./layout";
import { ZoneCanvas, type Picture } from "./zone_canvas";

type Message = { tone: "error" | "ok"; text: string };

/** How often a layout that hasn't loaded yet is asked for again. */
export const LAYOUT_RETRY_MS = 3000;

type Props = {
  /** Draw the zones on this picture instead of vision's stream. */
  picture?: Picture;
  /** Told whether vision has exactly the layout on screen: loaded or saved, with no edits since. */
  on_ready?: (ready: boolean) => void;
};

export function ZonesScreen({ picture, on_ready }: Props = {}) {
  const base = vision_url();
  const entities = useTableRows("entity");
  const bin_profiles = useTableRows("bin_profile");
  const [zones, set_zones] = useState<Zone[]>([]);
  // The layout as vision last confirmed it (loaded or saved), as JSON; null until one loads.
  const [saved_json, set_saved_json] = useState<string | null>(null);
  const [selected_id, set_selected_id] = useState<string | null>(null);
  const [adding, set_adding] = useState<ZoneKind | null>(null);
  const [busy, set_busy] = useState(false);
  const [message, set_message] = useState<Message | null>(null);
  // Counts failed loads, so each failure schedules the next try while no layout has loaded.
  const [failed_loads, set_failed_loads] = useState(0);

  const changed = saved_json !== null && JSON.stringify(zones) !== saved_json;
  const ready = saved_json !== null && !changed;
  const selected = zones.find((zone) => zone.zone_id === selected_id) ?? null;
  // Until a layout loads there are no slots to compare with, so no warnings either.
  const unslotted = saved_json === null ? [] : bins_without_slot(zones, bin_profiles);

  useEffect(() => on_ready?.(ready), [ready]);

  /** Loads the layout vision uses now, replacing any edits. */
  async function load(done_text?: string) {
    set_busy(true);
    const result = await get_zones();
    set_busy(false);
    if (!result.ok) {
      set_message({ tone: "error", text: `Couldn't load the layout from vision. ${result.message}` });
      set_failed_loads((count) => count + 1);
      return;
    }
    set_zones(result.data.zones);
    set_saved_json(JSON.stringify(result.data.zones));
    set_selected_id(null);
    set_message(done_text ? { tone: "ok", text: done_text } : null);
  }

  async function save() {
    set_busy(true);
    const result = await put_zones(zones);
    set_busy(false);
    if (!result.ok) {
      // Keep every edit, so the zone vision named can be fixed and saved again.
      set_message({ tone: "error", text: `Not saved. ${result.message}` });
      return;
    }
    set_zones(result.data.zones);
    set_saved_json(JSON.stringify(result.data.zones));
    set_message({
      tone: "ok",
      // The interactions step reads zone kinds once, at its first frame.
      text: "Saved. The kitchen uses this layout from the next frame. A new washing area starts washing after the runner restarts.",
    });
  }

  useEffect(() => {
    // Load once, when the screen opens.
    if (base) void load();
  }, []);

  // Still waiting for a first layout with an empty picture: keep asking. Anything drawn meanwhile is the person's, and
  // only REVERT may replace it.
  const awaiting_layout = Boolean(base) && saved_json === null && zones.length === 0;
  useEffect(() => {
    if (!awaiting_layout) return;
    const timer = setTimeout(() => void load(), LAYOUT_RETRY_MS);
    return () => clearTimeout(timer);
  }, [awaiting_layout, failed_loads]);

  // Esc stops adding.
  useEffect(() => {
    if (!adding) return;
    const on_key = (event: KeyboardEvent) => {
      if (event.key === "Escape") set_adding(null);
    };
    window.addEventListener("keydown", on_key);
    return () => window.removeEventListener("keydown", on_key);
  }, [adding]);

  function add_zone(zone: Zone) {
    set_zones([...zones, zone]);
    set_selected_id(zone.zone_id);
    set_adding(null);
  }

  function delete_zone(zone_id: string) {
    set_zones(zones.filter((zone) => zone.zone_id !== zone_id));
    if (selected_id === zone_id) set_selected_id(null);
  }

  const tags: Record<string, string> = {};
  for (const zone of zones) tags[zone.zone_id] = bin_label(zone, entities, bin_profiles) ?? kind_name(zone.kind);

  return (
    <section className="rail_section">
      <div className="rail_head">
        <h2 className="display">Kitchen layout</h2>
        <span className="rail_note">
          CAM-01 · {zones.length} zones{changed ? " · NOT SAVED" : ""}
        </span>
      </div>
      <p className="zone_intro">
        Where things are on the counter. A bin slot holds one bin (slot-4 holds bin-4 and its allergens), a washing area
        cleans whatever is washed there, and the serving area is where each plate is checked before it leaves.
      </p>

      {!base && (
        <p className="error">No vision set: start vision and set WEB_VISION_URL to see and change the layout.</p>
      )}

      {unslotted.length > 0 && (
        <ul className="zone_warnings" aria-label="Bins with no slot">
          {unslotted.map((profile) => (
            <li key={profile.entity_id}>
              {profile.entity_id} ({allergen_text(profile)}) has no slot: a bowl there will count as a clean plate.
            </li>
          ))}
        </ul>
      )}

      <div className="option_row zone_actions">
        <button type="button" className="btn" disabled={!changed || busy} onClick={() => void save()}>
          SAVE
        </button>
        {/* Never disabled by a request in flight: a request that hangs would leave no way to retry. */}
        <button
          type="button"
          className="btn btn_ghost"
          disabled={!base}
          onClick={() => void load("Reloaded the layout the kitchen uses now.")}
        >
          REVERT
        </button>
        {message?.tone === "error" && (
          <p role="alert" className="error zone_message">
            {message.text}
            {awaiting_layout && " Trying again."}
          </p>
        )}
        {message?.tone === "ok" && (
          <p aria-live="polite" className="bold zone_message">
            {message.text}
          </p>
        )}
      </div>

      <div className="option_row" role="group" aria-label="Add a zone">
        {ZONE_KINDS.map(({ kind, name }) => (
          <button
            key={kind}
            type="button"
            className={adding === kind ? "btn" : "btn btn_ghost"}
            aria-pressed={adding === kind}
            onClick={() => set_adding(adding === kind ? null : kind)}
          >
            <span className={`zone_swatch kind_${kind}`} aria-hidden="true" />
            {name}
          </button>
        ))}
      </div>
      <p className="muted zone_hint">{hint(adding, selected, zones)}</p>

      <div className="zone_layout">
        <ZoneCanvas
          picture={picture}
          zones={zones}
          tags={tags}
          selected_id={selected_id}
          adding={adding}
          on_zones={set_zones}
          on_select={set_selected_id}
          on_add={add_zone}
        />

        <aside className="zone_panel" aria-label="Zones">
          <h3 className="display zone_panel_title">Zones</h3>
          {zones.length === 0 && <p className="muted">No zones yet.</p>}
          <ul className="zone_list">
            {zones.map((zone) => (
              <li
                key={zone.zone_id}
                className={zone.zone_id === selected_id ? "zone_row zone_row_selected" : "zone_row"}
              >
                <button
                  type="button"
                  className="zone_pick"
                  aria-pressed={zone.zone_id === selected_id}
                  onClick={() => {
                    set_adding(null);
                    set_selected_id(zone.zone_id);
                  }}
                >
                  <span className={`zone_swatch kind_${zone.kind}`} aria-hidden="true" />
                  <span>
                    <strong>{zone.zone_id}</strong> <span className="muted">{kind_name(zone.kind)}</span>
                    {zone.kind === "bin_slot" && <span className="zone_bin">{tags[zone.zone_id]}</span>}
                  </span>
                </button>
                <button
                  type="button"
                  className="btn btn_ghost"
                  aria-label={`Delete ${zone.zone_id}`}
                  onClick={() => delete_zone(zone.zone_id)}
                >
                  DELETE
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </section>
  );
}

/** One line on what a drag does right now. */
function hint(adding: ZoneKind | null, selected: Zone | null, zones: Zone[]): string {
  if (adding) {
    return `Drag on the picture to draw ${next_zone_id(adding, zones)} (${kind_name(adding).toLowerCase()}). Esc cancels.`;
  }
  if (selected && is_rectangle(selected.polygon)) {
    return `${selected.zone_id}: drag it to move it, or drag a corner to resize it.`;
  }
  if (selected) {
    return `${selected.zone_id}: drag it to move it; to reshape it, delete it and draw a new one.`;
  }
  return "Pick a kind to add a zone, or click a zone to move, resize or delete it.";
}
