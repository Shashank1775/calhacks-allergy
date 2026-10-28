/**
 * Keeps vision's camera picture coming back by itself, for every screen that shows GET <WEB_VISION_URL>/stream.mjpg.
 *
 * Reads: vision health (useVisionHealth), to see when frames flow again.
 * Rules: display only. A broken stream (an <img> error, e.g. the page loaded before vision was serving) is offline for
 * STREAM_RETRY_MS, then the picture mounts again, which opens a new request. A stream cut mid-way (vision restarted)
 * fires no error in the browser and just freezes on its last frame, so the picture also reconnects each time vision's
 * frames come back after stopping.
 */
import { useEffect, useRef, useState } from "react";
import { useVisionHealth } from "./vision_health";

/** How long a broken stream stays offline before it's asked for again. */
export const STREAM_RETRY_MS = 2000;

export type CameraStream = {
  /** The stream failed: show the screen's offline note instead of the picture until the next try. */
  offline: boolean;
  /** The picture's onError. */
  on_error: () => void;
  /** Key the picture on this: it goes up each time vision's frames come back after stopping. */
  connection: number;
};

export function useCameraStream(): CameraStream {
  const health = useVisionHealth();
  const [offline, set_offline] = useState(false);
  const [connection, set_connection] = useState(0);
  const seen_ok = useRef(false);

  useEffect(() => {
    if (!offline) return;
    const timer = setTimeout(() => set_offline(false), STREAM_RETRY_MS);
    return () => clearTimeout(timer);
  }, [offline]);

  useEffect(() => {
    if (health !== "ok") return;
    // The first "ok" belongs to the stream the page just opened; only a return after a stop needs a new one.
    if (seen_ok.current) set_connection((count) => count + 1);
    seen_ok.current = true;
  }, [health]);

  return { offline, on_error: () => set_offline(true), connection };
}
