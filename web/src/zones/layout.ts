/**
 * The kitchen layout's plain logic: zone names, rectangles, moving and resizing, and bin labels.
 *
 * Reads: zone lists from the Vision API's GET /zones; the state's entity and bin_profile rows (labels only).
 * Rules: names follow vision's zones file: a bin slot is slot-<n>, because vision maps slot-<n> to the entity bin-<n>,
 * and every zone_id is unique. Points stay inside the normalized frame 0..1. Vision checks the whole layout again on
 * save, so nothing here is the last word.
 */
import { allergen_name } from "../allergens";
import type { BinProfile, Entity, Zone } from "../types/TEMPORARY";

export type ZoneKind = Zone["kind"];
export type Point = [number, number];

/** The kinds a person can add, in button order, named the way the kitchen says them. */
export const ZONE_KINDS: { kind: ZoneKind; name: string }[] = [
  { kind: "bin_slot", name: "Bin" },
  { kind: "wash", name: "Washing area" },
  { kind: "pass", name: "Serving area" },
  { kind: "plating_spot", name: "Plating spot" },
  { kind: "clean_stack", name: "Clean stack" },
  { kind: "other", name: "Other" },
];

/** The first zone_id of each kind other than bin slots, as in vision's zones files. */
const FIRST_ID: Record<Exclude<ZoneKind, "bin_slot">, string> = {
  wash: "wash",
  pass: "pass",
  plating_spot: "plating",
  clean_stack: "clean-stack",
  other: "other",
};

/** A drag shorter than this share of the frame, across or down, is a slip, not a rectangle. */
export const MIN_SIZE = 0.02;

export function kind_name(kind: ZoneKind): string {
  return ZONE_KINDS.find((entry) => entry.kind === kind)?.name ?? kind;
}

/**
 * The zone_id a new zone of this kind gets: the lowest free slot-<n> for a bin, so deleting slot-2 and drawing a new
 * bin brings slot-2 (and so bin-2) back; otherwise the kind's own name, then name-2, name-3, ...
 */
export function next_zone_id(kind: ZoneKind, zones: Zone[]): string {
  const used = new Set(zones.map((zone) => zone.zone_id));

  if (kind === "bin_slot") {
    let n = 1;
    while (used.has(`slot-${n}`)) n += 1;
    return `slot-${n}`;
  }

  const first = FIRST_ID[kind];
  if (!used.has(first)) return first;
  let n = 2;
  while (used.has(`${first}-${n}`)) n += 1;
  return `${first}-${n}`;
}

export function clamp_to_frame(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** Four decimals is far below a pixel of any camera, and keeps the saved file readable. */
function tidy(value: number): number {
  return Math.round(clamp_to_frame(value) * 10000) / 10000;
}

/** The upright rectangle with these two opposite corners, as 4 corners clockwise from the top left. */
export function rect_polygon(a: Point, b: Point): Point[] {
  const x1 = tidy(Math.min(a[0], b[0]));
  const x2 = tidy(Math.max(a[0], b[0]));
  const y1 = tidy(Math.min(a[1], b[1]));
  const y2 = tidy(Math.max(a[1], b[1]));
  return [
    [x1, y1],
    [x2, y1],
    [x2, y2],
    [x1, y2],
  ];
}

export function big_enough(a: Point, b: Point): boolean {
  return Math.abs(a[0] - b[0]) >= MIN_SIZE && Math.abs(a[1] - b[1]) >= MIN_SIZE;
}

/**
 * Whether a polygon is an upright rectangle with its corners in order, which the editor can resize by its corners.
 * Any other shape loaded from the file is drawn as it is and can only be moved, deleted or replaced.
 */
export function is_rectangle(polygon: Point[]): boolean {
  if (polygon.length !== 4) return false;
  const xs = new Set(polygon.map((point) => point[0]));
  const ys = new Set(polygon.map((point) => point[1]));
  const corners = new Set(polygon.map((point) => point.join(",")));
  if (xs.size !== 2 || ys.size !== 2 || corners.size !== 4) return false;

  // In order means every edge runs straight across or straight down; a bow-tie has a diagonal edge.
  return polygon.every((point, index) => {
    const next = polygon[(index + 1) % 4];
    return point[0] === next[0] || point[1] === next[1];
  });
}

/** The polygon shifted by (dx, dy), stopped at the frame's edges so no corner leaves it. */
export function move_polygon(polygon: Point[], dx: number, dy: number): Point[] {
  const xs = polygon.map((point) => point[0]);
  const ys = polygon.map((point) => point[1]);
  const shift_x = Math.min(1 - Math.max(...xs), Math.max(-Math.min(...xs), dx));
  const shift_y = Math.min(1 - Math.max(...ys), Math.max(-Math.min(...ys), dy));
  return polygon.map(([x, y]) => [tidy(x + shift_x), tidy(y + shift_y)]);
}

/** The polygon's bounding box, where its tag goes and how wide the tag may be. */
export function bounds(polygon: Point[]): { left: number; top: number; right: number } {
  const xs = polygon.map((point) => point[0]);
  const ys = polygon.map((point) => point[1]);
  return { left: Math.min(...xs), top: Math.min(...ys), right: Math.max(...xs) };
}

/**
 * What sits in a bin slot, from the state: slot-4 holds bin-4, with its label (entity row) and its allergens (bin
 * profile) when the state has them. Null for any other zone.
 */
export function bin_label(zone: Zone, entities: Entity[], bin_profiles: BinProfile[]): string | null {
  if (zone.kind !== "bin_slot") return null;
  const bin_id = zone.zone_id.replace(/^slot-/, "bin-");
  const entity = entities.find((row) => row.entity_id === bin_id);
  const profile = bin_profiles.find((row) => row.entity_id === bin_id);

  const parts = [bin_id];
  if (entity) parts.push(entity.label);
  if (profile) parts.push(profile_text(profile));
  return parts.join(" · ");
}

function profile_text(profile: BinProfile): string {
  if (profile.unknown) return "unknown, counts as every allergen";
  const allergens = Object.entries(profile.probabilities)
    .filter(([, probability]) => probability > 0)
    .map(([allergen_id]) => allergen_name(allergen_id));
  return allergens.length > 0 ? allergens.join(", ") : "no allergens";
}
