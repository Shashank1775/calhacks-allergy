/**
 * The camera picture with the zones drawn over it, and the mouse or finger work on them: drag to draw a rectangle,
 * drag a zone to move it, drag a corner handle to resize it.
 *
 * Reads: GET <WEB_VISION_URL>/stream.mjpg (the picture only); the zones and selection from the layout screen.
 * Emits: the changed zone list, a new zone or a selection, through the screen's callbacks. It saves nothing itself.
 * Rules: with no camera, the zones still draw on a plain 16:9 frame, since points are shares of the frame, not pixels.
 */
import { useRef, useState, type PointerEvent } from "react";
import { vision_url } from "../api/http";
import type { Zone } from "../types/TEMPORARY";
import {
  big_enough,
  bounds,
  clamp_to_frame,
  is_rectangle,
  move_polygon,
  next_zone_id,
  rect_polygon,
  type Point,
  type ZoneKind,
} from "./layout";

const PLAIN_FRAME = 16 / 9;

type Drag =
  | { mode: "draw"; kind: ZoneKind; start: Point; end: Point }
  | { mode: "move"; zone_id: string; start: Point; polygon: Point[] }
  | { mode: "resize"; zone_id: string; anchor: Point };

type Props = {
  zones: Zone[];
  /** The text on each zone's tag, by zone_id. */
  tags: Record<string, string>;
  selected_id: string | null;
  /** The kind being added; a drag then draws a new zone of it, even over another zone. */
  adding: ZoneKind | null;
  on_zones: (zones: Zone[]) => void;
  on_select: (zone_id: string | null) => void;
  on_add: (zone: Zone) => void;
};

export function ZoneCanvas({ zones, tags, selected_id, adding, on_zones, on_select, on_add }: Props) {
  const base = vision_url();
  const frame_ref = useRef<HTMLDivElement>(null);
  const [drag, set_drag] = useState<Drag | null>(null);
  const [camera_offline, set_camera_offline] = useState(false);
  const [camera_aspect, set_camera_aspect] = useState(PLAIN_FRAME);
  const show_camera = Boolean(base) && !camera_offline;
  const selected = zones.find((zone) => zone.zone_id === selected_id) ?? null;

  /** Where the pointer is, as a share of the frame (0..1 across and down), like every zone point. */
  function point_at(event: PointerEvent): Point {
    const box = frame_ref.current!.getBoundingClientRect();
    return [
      clamp_to_frame((event.clientX - box.left) / box.width),
      clamp_to_frame((event.clientY - box.top) / box.height),
    ];
  }

  function begin(event: PointerEvent, next: Drag) {
    // Keep getting moves while the pointer is outside the frame, so a fast drag doesn't drop the zone.
    frame_ref.current?.setPointerCapture?.(event.pointerId);
    set_drag(next);
  }

  function replace_polygon(zone_id: string, polygon: Point[]) {
    on_zones(zones.map((zone) => (zone.zone_id === zone_id ? { ...zone, polygon } : zone)));
  }

  function on_frame_down(event: PointerEvent) {
    if (adding) {
      const point = point_at(event);
      begin(event, { mode: "draw", kind: adding, start: point, end: point });
      return;
    }
    on_select(null);
  }

  function on_zone_down(event: PointerEvent, zone: Zone) {
    // While adding, a drag that starts on a zone still draws, so a bin can go inside a bigger area.
    if (adding) return;
    event.stopPropagation();
    on_select(zone.zone_id);
    begin(event, { mode: "move", zone_id: zone.zone_id, start: point_at(event), polygon: zone.polygon });
  }

  function on_handle_down(event: PointerEvent, zone: Zone, corner: number) {
    event.stopPropagation();
    // The opposite corner stays put, whichever way the dragged one goes.
    begin(event, { mode: "resize", zone_id: zone.zone_id, anchor: zone.polygon[(corner + 2) % 4] });
  }

  function on_frame_move(event: PointerEvent) {
    if (!drag) return;
    const point = point_at(event);
    if (drag.mode === "draw") set_drag({ ...drag, end: point });
    if (drag.mode === "move") {
      replace_polygon(drag.zone_id, move_polygon(drag.polygon, point[0] - drag.start[0], point[1] - drag.start[1]));
    }
    // A corner dragged onto its opposite would leave no area, which vision refuses, so it stops short.
    if (drag.mode === "resize" && big_enough(drag.anchor, point)) {
      replace_polygon(drag.zone_id, rect_polygon(drag.anchor, point));
    }
  }

  function on_frame_up(event: PointerEvent) {
    // Esc mid-drag cancels adding, so the rectangle is dropped.
    if (drag?.mode === "draw" && adding) {
      const end = point_at(event);
      if (big_enough(drag.start, end)) {
        on_add({ zone_id: next_zone_id(drag.kind, zones), kind: drag.kind, polygon: rect_polygon(drag.start, end) });
      }
    }
    set_drag(null);
  }

  const draft = drag?.mode === "draw" ? { kind: drag.kind, polygon: rect_polygon(drag.start, drag.end) } : null;
  const show_handles = selected && !adding && is_rectangle(selected.polygon);

  return (
    <div className="camera_box zone_camera">
      <div
        ref={frame_ref}
        className={adding ? "zone_frame zone_frame_adding" : "zone_frame"}
        style={{ aspectRatio: show_camera ? camera_aspect : PLAIN_FRAME }}
        aria-label="Counter picture with zones"
        onPointerDown={on_frame_down}
        onPointerMove={on_frame_move}
        onPointerUp={on_frame_up}
        onPointerCancel={() => set_drag(null)}
      >
        {show_camera && (
          <img
            src={`${base}/stream.mjpg`}
            alt="Live view of the counter"
            draggable={false}
            onLoad={(event) => {
              const image = event.currentTarget;
              if (image.naturalWidth && image.naturalHeight)
                set_camera_aspect(image.naturalWidth / image.naturalHeight);
            }}
            onError={() => set_camera_offline(true)}
          />
        )}

        <svg className="zone_svg" viewBox="0 0 1 1" preserveAspectRatio="none">
          {zones.map((zone) => (
            <polygon
              key={zone.zone_id}
              className={`zone_shape kind_${zone.kind}${zone.zone_id === selected_id ? " zone_selected" : ""}`}
              points={points_text(zone.polygon)}
              aria-label={`zone ${zone.zone_id}`}
              onPointerDown={(event) => on_zone_down(event, zone)}
            />
          ))}
          {draft && (
            <polygon className={`zone_shape zone_draft kind_${draft.kind}`} points={points_text(draft.polygon)} />
          )}
        </svg>

        {zones.map((zone) => {
          const { left, top, right } = bounds(zone.polygon);
          // As wide as its zone at most, so neighbouring bins' tags don't run into each other; the list has it all.
          return (
            <span
              key={zone.zone_id}
              className={`zone_tag kind_${zone.kind}`}
              style={{ left: `${left * 100}%`, top: `${top * 100}%`, maxWidth: `${(right - left) * 100}%` }}
              title={`${zone.zone_id} · ${tags[zone.zone_id]}`}
            >
              <strong>{zone.zone_id}</strong>
              <br />
              {tags[zone.zone_id]}
            </span>
          );
        })}

        {show_handles &&
          selected.polygon.map(([x, y], corner) => (
            <span
              key={corner}
              className={`zone_handle kind_${selected.kind}`}
              style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
              aria-label={`resize ${selected.zone_id} from corner ${corner + 1}`}
              onPointerDown={(event) => on_handle_down(event, selected, corner)}
            />
          ))}
      </div>
      {!show_camera && (
        <p className="zone_no_camera">
          {base ? "Camera offline." : "No camera set (WEB_VISION_URL)."} Zones are drawn on a plain 16:9 frame.
        </p>
      )}
    </div>
  );
}

function points_text(polygon: Point[]): string {
  return polygon.map(([x, y]) => `${x},${y}`).join(" ");
}
