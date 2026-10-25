/**
 * Sends what a <video> shows (this tab's camera, or a video file playing) to vision as JPEG frames.
 *
 * Emits: POST <WEB_VISION_URL>/frames, about TARGET_FPS a second, one request at a time.
 * Rules: fail closed. This only supplies pixels: vision stamps each frame with its own clock and decides everything.
 * When frames stop (stop(), a closed tab, the end of a video), vision's health goes stale and the gate shows FAULT.
 * With one request in flight at a time, a slow vision never builds a queue here either: the tab simply sends less.
 */
import { post_frame } from "../api/vision";

export const TARGET_FPS = 15;
/** Wider frames are scaled down to this: vision letterboxes every frame to 640 anyway. */
export const MAX_WIDTH = 1280;
const JPEG_QUALITY = 0.8;
// HTMLMediaElement.HAVE_CURRENT_DATA: the video has a picture to draw.
const HAVE_CURRENT_DATA = 2;

export type SenderStatus = {
  /** Frames vision took in the last second. */
  fps: number;
  /** Frames vision took since start. */
  sent: number;
  /** The error code of the last frame vision didn't take, or null once one gets through again. */
  error: string | null;
};

type Post = typeof post_frame;

/**
 * Starts sending; returns the function that stops it. `video` is read on every tick, so the element can be replaced
 * (React remounts it between screens). `post` is vision's endpoint; tests pass their own.
 */
export function start_frame_sender(
  video: () => HTMLVideoElement | null,
  on_status: (status: SenderStatus) => void,
  post: Post = post_frame,
): () => void {
  const canvas = document.createElement("canvas");
  const taken_at: number[] = [];
  let sent = 0;
  let in_flight = false;
  let stopped = false;

  async function tick() {
    const element = video();
    if (stopped || in_flight || !element || element.readyState < HAVE_CURRENT_DATA || !element.videoWidth) return;
    // A paused or finished video holds its last picture; sending it would show vision a frozen kitchen as live.
    if (element.paused || element.ended) return;
    in_flight = true;
    let result: Awaited<ReturnType<Post>> | null = null;
    try {
      const jpeg = await grab_jpeg(element, canvas);
      // post() never throws: a failure comes back as a code.
      result = jpeg ? await post(jpeg) : null;
    } catch {
      result = null; // the canvas couldn't take the picture
    } finally {
      in_flight = false;
    }
    if (stopped) return;

    const now = performance.now();
    if (result?.ok) {
      sent += 1;
      taken_at.push(now);
    }
    while (taken_at.length && taken_at[0] < now - 1000) taken_at.shift();
    const error = result === null ? "no_picture" : result.ok ? null : result.code;
    on_status({ fps: taken_at.length, sent, error });
  }

  const stop_ticker = start_ticker(1000 / TARGET_FPS, () => void tick());
  return () => {
    stopped = true;
    stop_ticker();
  };
}

/**
 * Calls `tick` every `interval_ms`, from a timer in a Web Worker. A browser slows a background tab's own timers to
 * once a second (measured: 1 frame/s, so START PLATE found vision's newest frame too old), but not a worker's.
 * Where there are no workers (tests), it's a plain interval.
 */
function start_ticker(interval_ms: number, tick: () => void): () => void {
  if (typeof Worker === "undefined" || typeof URL.createObjectURL !== "function") {
    const timer = setInterval(tick, interval_ms);
    return () => clearInterval(timer);
  }
  const script = URL.createObjectURL(new Blob([`setInterval(() => postMessage(0), ${interval_ms});`]));
  const worker = new Worker(script);
  worker.onmessage = tick;
  return () => {
    worker.terminate();
    URL.revokeObjectURL(script);
  };
}

/** The video's current picture as a JPEG, at most MAX_WIDTH wide, or null if the canvas couldn't make one. */
export function grab_jpeg(video: HTMLVideoElement, canvas: HTMLCanvasElement): Promise<Blob | null> {
  const scale = Math.min(1, MAX_WIDTH / video.videoWidth);
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
}
