/**
 * Runs in the frame worker (frame_worker.ts): sends a camera's frames to vision, read straight off its track.
 *
 * Reads: the camera's VideoFrames, a MediaStreamTrackProcessor's stream handed over by start_camera_worker().
 * Emits: POST <WEB_VISION_URL>/frames, about TARGET_FPS a second, one request at a time; a status report after each.
 * Rules: fail closed. Each VideoFrame is a new picture from the camera and is sent at most once, so a camera that
 * stalls or freezes delivers nothing new and nothing is sent: vision's health goes stale and the gate shows FAULT.
 * Vision stamps each frame with its own clock; no browser time is sent. A worker's timers and stream reads keep
 * their pace in a hidden tab, where the main thread's slow to about one a second.
 */
import { post_frame } from "../api/vision";
import { frame_counter, JPEG_QUALITY, MAX_WIDTH, TARGET_FPS, type SenderStatus } from "./frame_sender";

/** Page → worker: the one message, sent once. The stream is transferred, not copied. */
export type WorkerCommand = { type: "start"; frames: ReadableStream<VideoFrame> };

/** Worker → page: after each frame vision took or didn't. */
export type WorkerReport = { type: "status"; status: SenderStatus };

type Post = typeof post_frame;
type Encode = (frame: VideoFrame) => Promise<Blob>;

/** The worker's side of the protocol: start sending the stream, reporting each frame's status back to the page. */
export function handle_command(command: WorkerCommand, report: (report: WorkerReport) => void): () => void {
  return run_track_sender(command.frames, (status) => report({ type: "status", status }));
}

/**
 * Sends the newest frame from `frames` about TARGET_FPS a second; returns the function that stops it. It also stops
 * when the stream ends (the camera's track ended). Every frame is closed, sent or not. `post` and `encode` are for
 * tests.
 */
export function run_track_sender(
  frames: ReadableStream<VideoFrame>,
  on_status: (status: SenderStatus) => void,
  post: Post = post_frame,
  encode: Encode = jpeg_encoder(),
): () => void {
  const reader = frames.getReader();
  const count = frame_counter();
  // The newest frame not yet sent. Taking it to send sets this back to null, so no frame is ever sent twice.
  let newest: VideoFrame | null = null;
  let last_sent_timestamp = -Infinity;
  let in_flight = false;
  let stopped = false;

  async function read_frames() {
    while (!stopped) {
      const { done, value } = await reader.read().catch(() => ({ done: true as const, value: undefined }));
      if (done) break;
      if (stopped) {
        value.close();
        break;
      }
      // A newer frame came before the last one was sent: the older one is never sent.
      newest?.close();
      newest = value;
    }
    stop();
  }

  async function tick() {
    if (stopped || in_flight || !newest) return;
    const frame = newest;
    newest = null;
    // A frame no newer than the last one sent is the same picture again, never a new one.
    if (frame.timestamp <= last_sent_timestamp) {
      frame.close();
      return;
    }
    last_sent_timestamp = frame.timestamp;
    in_flight = true;
    let result: Awaited<ReturnType<Post>> | null = null;
    try {
      // post() never throws: a failure comes back as a code.
      result = await post(await encode(frame));
    } catch {
      result = null; // the frame couldn't be made into a JPEG
    } finally {
      in_flight = false;
    }
    if (stopped) return;
    on_status(count(result));
  }

  const timer = setInterval(() => void tick(), 1000 / TARGET_FPS);
  void read_frames();

  function stop() {
    if (stopped) return;
    stopped = true;
    clearInterval(timer);
    newest?.close();
    newest = null;
    void reader.cancel().catch(() => {});
  }
  return stop;
}

/** Makes each frame a JPEG at most MAX_WIDTH wide on one OffscreenCanvas, closing the frame once it's drawn. */
function jpeg_encoder(): Encode {
  const canvas = new OffscreenCanvas(1, 1);
  return async (frame) => {
    try {
      const scale = Math.min(1, MAX_WIDTH / frame.displayWidth);
      canvas.width = Math.round(frame.displayWidth * scale);
      canvas.height = Math.round(frame.displayHeight * scale);
      const context = canvas.getContext("2d");
      if (!context) throw new Error("No 2D context on the OffscreenCanvas");
      context.drawImage(frame, 0, 0, canvas.width, canvas.height);
    } finally {
      frame.close();
    }
    return canvas.convertToBlob({ type: "image/jpeg", quality: JPEG_QUALITY });
  };
}
