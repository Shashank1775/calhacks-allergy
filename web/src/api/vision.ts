/**
 * Client for the Vision HTTP API on the Pi (contracts/apis.md#vision-http-api-on-the-pi).
 */
import type { Zone } from "../types/TEMPORARY";
import { call_api, fetch_api, vision_url } from "./http";

export function start_plate(ticket_id: string) {
  return call_api<{ plate_id: string }>(vision_url(), "/plates/start", "POST", { ticket_id });
}

/** Why vision refused to bind a plate. It never guesses, so neither do we. */
export const START_PLATE_ERRORS: Record<string, string> = {
  no_plate_at_spot: "No plate at the plating spot. Put one down and try again.",
  multiple_plates_at_spot: "More than one plate at the plating spot. Leave just one.",
  plate_already_bound: "That plate already belongs to another ticket. Use a clean plate.",
};

export type ZoneLayout = { zones: Zone[] };

/** GET /zones: the layout the kitchen uses now. */
export function get_zones() {
  return fetch_api<ZoneLayout>(vision_url(), "/zones", { method: "GET" });
}

/**
 * PUT /zones: replace the whole layout. Vision checks it and uses it from the next frame; a bad layout comes back
 * 422 `bad_zones` with a message that names the zone.
 */
export function put_zones(zones: Zone[]) {
  return fetch_api<ZoneLayout>(vision_url(), "/zones", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ zones }),
  });
}

/**
 * POST /frames: one frame from this tab's camera or video, as a JPEG. Vision stamps it with its own clock when it
 * arrives and keeps only the newest one, so a slow detector never builds a backlog. 204, no body.
 */
export function post_frame(jpeg: Blob) {
  return fetch_api<null>(vision_url(), "/frames", {
    method: "POST",
    headers: { "Content-Type": "image/jpeg" },
    body: jpeg,
  });
}

/** Why vision didn't take a frame, in words for the camera screen. */
export const FRAME_ERRORS: Record<string, string> = {
  not_web_source: "Vision reads its own camera. Restart the runner with --live --from-web to feed it from here.",
  bad_frame: "Vision couldn't read the frame as a JPEG.",
  network_error: "Can't reach vision. Is the runner still running?",
  not_configured: "No vision set: set WEB_VISION_URL.",
  no_picture: "The browser couldn't take a picture from the video.",
};
