/**
 * Sends this tab's camera, or a video file playing, to vision as JPEG frames.
 *
 * Emits: POST <WEB_VISION_URL>/frames, about TARGET_FPS a second, one request at a time.
 * Rules: fail closed. This only supplies pixels: vision stamps each frame with its own clock and decides everything.
 * When frames stop (stop(), a closed tab, the end of a video), vision's health goes stale and the gate shows FAULT.
 * With one request in flight at a time, a slow vision never builds a queue here either: the tab simply sends less.
 *
 * Two ways to send. A camera is read off its track in a worker (track_sender.ts) where the browser can, and keeps
 * TARGET_FPS in a hidden tab (measured in Chrome). A video file, or a camera in a browser without
 * MediaStreamTrackProcessor, is drawn from its <video> by this page: even on start_ticker's worker timer, Chrome
 * slowed that to about a frame a second in a hidden tab (measured, camera and video alike), so that tab must stay
 * visible.
 */
import type { ApiResult } from "../api/http";
import { post_frame } from "../api/vision";
import type { WorkerCommand, WorkerReport } from "./track_sender";

/**
 * Vision kept up with 27 frames a second pushed to it (measured on the laptop); 24 keeps its /health above 20 with
 * a 30 fps camera, leaving room for a skipped tick while a frame is still in flight.
 */
export const TARGET_FPS = 24;
/** Wider frames are scaled down to this: vision letterboxes every frame to 640 anyway. */
export const MAX_WIDTH = 1280;
export const JPEG_QUALITY = 0.8;
// HTMLMediaElement.HAVE_CURRENT_DATA: the video has a picture to draw.
const HAVE_CURRENT_DATA = 2;

// Chrome's MediaStreamTrackProcessor (mediacapture-transform) isn't in TypeScript's DOM types yet.
declare const MediaStreamTrackProcessor: new (init: { track: MediaStreamTrack }) => {
  readable: ReadableStream<VideoFrame>;
};

export type SenderStatus = {
  /** Frames vision took in the last second. */
  fps: number;
  /** Frames vision took since start. */
  sent: number;
  /** The error code of the last frame vision didn't take, or null once one gets through again. */
  error: string | null;
};

/** How frames get to vision: read off the camera's track in a worker, or drawn from the <video> on this tab. */
export type SenderMode = "camera_worker" | "main_thread";

type Post = typeof post_frame;

/**
 * The camera worker needs a camera track, a Worker and MediaStreamTrackProcessor (Chrome and Edge have it). Anything
 * else, including every video file, uses the main thread, which slows to about a frame a second in a hidden tab.
 */
export function sender_mode(camera_track: MediaStreamTrack | undefined): SenderMode {
  const can_read_track = "MediaStreamTrackProcessor" in globalThis && "Worker" in globalThis;
  return camera_track && can_read_track ? "camera_worker" : "main_thread";
}

/**
 * Turns each frame's answer into the status line: frames vision took in the last second, in all, and the last error.
 * `null` means no picture could be taken. Both ways of sending count with this, so both report alike.
 */
export function frame_counter(now: () => number = () => performance.now()) {
  const taken_at: number[] = [];
  let sent = 0;
  return (result: ApiResult<null> | null): SenderStatus => {
    const at = now();
    if (result?.ok) {
      sent += 1;
      taken_at.push(at);
    }
    while (taken_at.length && taken_at[0] < at - 1000) taken_at.shift();
    const error = result === null ? "no_picture" : result.ok ? null : result.code;
    return { fps: taken_at.length, sent, error };
  };
}

/**
 * Starts sending a camera's frames from the frame worker; returns the function that stops it. The worker reads a
 * clone of the track, so stopping ends the clone and never the camera this page still shows.
 */
export function start_camera_worker(track: MediaStreamTrack, on_status: (status: SenderStatus) => void): () => void {
  const clone = track.clone();
  const frames = new MediaStreamTrackProcessor({ track: clone }).readable;
  const worker = new Worker(new URL("./frame_worker.ts", import.meta.url), { type: "module" });
  let last: SenderStatus = { fps: 0, sent: 0, error: null };
  let stopped = false;

  worker.onmessage = (event: MessageEvent<WorkerReport>) => {
    if (stopped || event.data.type !== "status") return;
    last = event.data.status;
    on_status(last);
  };
  // A worker that can't load or crashes sends nothing more: say so. Vision's health goes stale and the gate shows FAULT.
  worker.onerror = () => {
    if (!stopped) on_status({ ...last, fps: 0, error: "no_picture" });
  };

  const start: WorkerCommand = { type: "start", frames };
  worker.postMessage(start, [frames]);
  return () => {
    stopped = true;
    worker.terminate();
    clone.stop();
  };
}

/**
 * Starts sending from a <video> on the main thread; returns the function that stops it. `video` is read on every
 * tick, so the element can be replaced (React remounts it between screens). `post` is vision's endpoint; tests pass
 * their own.
 */
export function start_frame_sender(
  video: () => HTMLVideoElement | null,
  on_status: (status: SenderStatus) => void,
  post: Post = post_frame,
): () => void {
  const canvas = document.createElement("canvas");
  const count = frame_counter();
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
    on_status(count(result));
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
