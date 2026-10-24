/**
 * The live camera with overlays: vision's MJPEG stream, with each entity's latest box drawn over
 * it and colored by its allergen risk.
 *
 * Reads: GET <WEB_VISION_URL>/stream.mjpg; entity_state (latest box, probabilities, unknown).
 * Rules: display only. A missing or broken stream shows "camera offline" and changes no verdict;
 * the gate panel's FAULT comes from vision health, not from this picture.
 */
import { useState } from "react";
import { allergen_name } from "../allergens";
import { vision_url } from "../api/http";
import { latest_states } from "../gate";
import { useTableRows } from "../state/hooks";
import type { EntityState } from "../types/TEMPORARY";

export type Risk = "unknown" | "high" | "some" | "clean";

/** Unknown history counts as every allergen; otherwise the strongest allergen sets the color. */
export function risk_of(state: EntityState): Risk {
  if (state.unknown) return "unknown";
  const strongest = Math.max(0, ...Object.values(state.probabilities));
  if (strongest >= 0.5) return "high";
  if (strongest > 0) return "some";
  return "clean";
}

export function CameraPanel() {
  const base = vision_url();
  const states = useTableRows("entity_state");
  const [offline, set_offline] = useState(false);
  const boxes = [...latest_states(states).values()].filter((state) => state.box !== null);

  return (
    <section className="rail_section">
      <div className="rail_head">
        <h2 className="display">Camera</h2>
        <span className="rail_note">CAM-01 · {boxes.length} tracked</span>
      </div>
      <div className="camera_box">
        {!base && <p className="muted">No camera set: start vision and set WEB_VISION_URL to see the counter.</p>}
        {base && offline && <p className="error">Camera offline. Verdicts don't depend on this picture.</p>}
        {base && !offline && (
          <div className="camera_frame">
            <img src={`${base}/stream.mjpg`} alt="Live view of the counter" onError={() => set_offline(true)} />
            {boxes.map((state) => (
              <EntityBox key={state.entity_id} state={state} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function EntityBox({ state }: { state: EntityState }) {
  const [x1, y1, x2, y2] = state.box!;
  const risk = risk_of(state);
  const top = Object.entries(state.probabilities).sort((a, b) => b[1] - a[1])[0];
  const label = risk === "unknown" ? "unknown history" : top && top[1] > 0 ? allergen_name(top[0]) : "clean";
  return (
    <div
      className={`entity_box risk_${risk}`}
      style={{ left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`, height: `${(y2 - y1) * 100}%` }}
      aria-label={`${state.entity_id}: ${label}`}
    >
      <span className="entity_tag">
        {state.entity_id} · {label}
      </span>
    </div>
  );
}
