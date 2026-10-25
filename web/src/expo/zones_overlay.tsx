/**
 * The kitchen's zones on the expo camera: the bin slots, washing areas, serving area, plating spot and clean stacks,
 * outlined and labeled under the boxes, and the Zones toggle that shows them.
 *
 * Reads: GET <WEB_VISION_URL>/zones, again every few seconds while shown, so an edit on /zones shows up here without a
 * reload; entity and bin_profile rows, for bin labels only. Never /state's zone table, which keeps zones a save deleted.
 * Emits: nothing; the toggle's choice stays in this browser's localStorage.
 * Rules: display only. No verdict, gate or state reads these zones. A failed fetch draws no zones and says so, rather
 * than leaving an old layout up as if it were current.
 */
import { useEffect, useState } from "react";
import { get_zones } from "../api/vision";
import { useTableRows } from "../state/hooks";
import type { Zone } from "../types/TEMPORARY";
import { bin_label, bounds, kind_name } from "../zones/layout";
import { points_text } from "../zones/zone_canvas";

/** How often the shown layout is fetched again. */
export const ZONES_REFRESH_MS = 3000;

const STORAGE_KEY = "expo_show_zones";

/** Whether this viewer last turned the zones on. Off when nothing is stored or storage is blocked. */
function stored_show_zones(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

function store_show_zones(on: boolean) {
  try {
    window.localStorage.setItem(STORAGE_KEY, on ? "on" : "off");
  } catch {
    // Storage is blocked: the choice lasts until the page reloads.
  }
}

/** The Zones toggle's state, remembered per viewer. */
export function useShowZones(): [boolean, (on: boolean) => void] {
  const [on, set_on] = useState(stored_show_zones);

  function change(next: boolean) {
    set_on(next);
    store_show_zones(next);
  }

  return [on, change];
}

export function ZonesToggle({ on, on_change }: { on: boolean; on_change: (on: boolean) => void }) {
  return (
    <button type="button" className={on ? "btn" : "btn btn_ghost"} aria-pressed={on} onClick={() => on_change(!on)}>
      ZONES
    </button>
  );
}

/**
 * The layout vision uses now, fetched again every ZONES_REFRESH_MS: null after a failed fetch, empty until the first
 * one answers. The next fetch waits for the last one, so a slow vision never piles up requests.
 */
function useLiveZones(): Zone[] | null {
  const [zones, set_zones] = useState<Zone[] | null>([]);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load() {
      const result = await get_zones();
      if (!live) return;
      set_zones(result.ok && Array.isArray(result.data?.zones) ? result.data.zones : null);
      timer = setTimeout(load, ZONES_REFRESH_MS);
    }

    void load();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, []);

  return zones;
}

/** Every zone outlined over the camera picture, in the picture's own 0..1 coordinates, with its name or bin. */
export function ZonesOverlay() {
  const zones = useLiveZones();
  const entities = useTableRows("entity");
  const bin_profiles = useTableRows("bin_profile");

  return (
    <div className="zones_overlay" aria-label="Zones on the camera">
      <svg className="zone_svg" viewBox="0 0 1 1" preserveAspectRatio="none">
        {zones?.map((zone) => (
          <polygon
            key={zone.zone_id}
            className={`zone_shape zone_outline kind_${zone.kind}`}
            points={points_text(zone.polygon)}
            aria-label={`zone ${zone.zone_id}`}
          />
        ))}
      </svg>

      {zones?.map((zone) => {
        const { left, top, right } = bounds(zone.polygon);
        const tag = bin_label(zone, entities, bin_profiles) ?? kind_name(zone.kind);
        // As wide as its zone at most, so neighbouring bins' tags don't run into each other.
        return (
          <span
            key={zone.zone_id}
            className={`zone_tag kind_${zone.kind}`}
            style={{ left: `${left * 100}%`, top: `${top * 100}%`, maxWidth: `${(right - left) * 100}%` }}
            title={`${zone.zone_id} · ${tag}`}
          >
            <strong>{zone.zone_id}</strong> · {tag}
          </span>
        );
      })}

      {zones === null && <p className="zones_note">Zones unavailable</p>}
    </div>
  );
}
