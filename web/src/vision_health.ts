/**
 * Watches vision's GET /health, so the gate panel shows FAULT when the camera stops.
 *
 * Reads: GET <WEB_VISION_URL>/health → {fps, detector, last_frame_t_ms}.
 * Rules: fail closed. Health is judged by when last_frame_t_ms last changed, on our own clock
 * (vision's clock isn't ours), as kitchen/gate/service.py does. A failed poll, or frames that
 * stop advancing for HEALTH_LIMIT_MS, is stale; so is the time before the first good answer.
 */
import { useEffect, useState } from "react";
import { vision_url } from "./api/http";

/** "not_used": no vision URL is set (a replay), so there is no camera to watch. */
export type VisionHealth = "ok" | "stale" | "not_used";

/** Same limit as GateSettings.health_limit_ms. */
export const HEALTH_LIMIT_MS = 1000;
export const HEALTH_POLL_MS = 250;

export function useVisionHealth(): VisionHealth {
  const base = vision_url();
  const [health, set_health] = useState<VisionHealth>(base ? "stale" : "not_used");

  useEffect(() => {
    if (!base) return;
    let last_frame: number | null = null;
    let last_change_at = 0;
    let stopped = false;

    async function poll() {
      const frame = await read_last_frame(base!);
      if (stopped) return;
      const now = Date.now();
      if (frame !== null && frame !== last_frame) {
        last_frame = frame;
        last_change_at = now;
      }
      set_health(last_frame !== null && now - last_change_at <= HEALTH_LIMIT_MS ? "ok" : "stale");
    }

    void poll();
    const timer = setInterval(() => void poll(), HEALTH_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [base]);

  return health;
}

/** last_frame_t_ms from one health poll, or null if the poll failed in any way. */
async function read_last_frame(base: string): Promise<number | null> {
  try {
    const response = await fetch(`${base}/health`, { signal: AbortSignal.timeout(HEALTH_LIMIT_MS) });
    if (!response.ok) return null;
    const body = await response.json();
    return typeof body?.last_frame_t_ms === "number" ? body.last_frame_t_ms : null;
  } catch {
    return null;
  }
}
