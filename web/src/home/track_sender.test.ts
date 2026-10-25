import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResult } from "../api/http";
import { vision_frames_taken } from "../api/mocks/handlers";
import { MAX_WIDTH, TARGET_FPS, type SenderStatus } from "./frame_sender";
import { handle_command, run_track_sender, type WorkerReport } from "./track_sender";

type FrameResult = ApiResult<null>;
type FakeFrame = VideoFrame & { close: ReturnType<typeof vi.fn> };

const TICK_MS = 1000 / TARGET_FPS;

/** A camera track's frames, as MediaStreamTrackProcessor gives them: the test says when each one arrives. */
function fake_camera() {
  let controller!: ReadableStreamDefaultController<VideoFrame>;
  const frames = new ReadableStream<VideoFrame>({ start: (c) => void (controller = c) });
  let timestamp = 0;
  return {
    frames,
    /** One new frame, 33 ms after the last (a 30 fps camera), or at the timestamp given. */
    deliver(at = (timestamp += 33_333)): FakeFrame {
      const frame = { timestamp: at, close: vi.fn() } as unknown as FakeFrame;
      controller.enqueue(frame);
      return frame;
    },
    end: () => controller.close(),
  };
}

/** Stands in for the OffscreenCanvas: each frame's "JPEG" says which frame it was. Closes the frame, like the real one. */
const encode = vi.fn(async (frame: VideoFrame) => {
  frame.close();
  return new Blob([String(frame.timestamp)], { type: "image/jpeg" });
});

/** A post() whose answers the test hands out one at a time, to see what waits on what. */
function held_post() {
  const answers: ((result: FrameResult) => void)[] = [];
  const post = vi.fn((_jpeg: Blob) => new Promise<FrameResult>((resolve) => answers.push(resolve)));
  return { post, answer: (result: FrameResult = { ok: true, data: null }) => answers.shift()!(result) };
}

async function sent_timestamps(post: ReturnType<typeof held_post>["post"]) {
  return Promise.all(post.mock.calls.map(async ([jpeg]) => Number(await jpeg.text())));
}

beforeEach(() => vi.useFakeTimers());

afterEach(() => {
  vi.useRealTimers();
  encode.mockClear();
});

describe("run_track_sender", () => {
  it("sends the newest frame, one request at a time, and closes every frame it skips", async () => {
    const camera = fake_camera();
    const { post, answer } = held_post();
    const stop = run_track_sender(camera.frames, () => {}, post, encode);

    const older = camera.deliver();
    const newer = camera.deliver();
    await vi.advanceTimersByTimeAsync(TICK_MS);
    expect(await sent_timestamps(post)).toEqual([newer.timestamp]);
    expect(older.close).toHaveBeenCalled();

    // While vision hasn't answered, new frames wait: only the newest is sent once it does.
    const skipped = camera.deliver();
    const latest = camera.deliver();
    await vi.advanceTimersByTimeAsync(TICK_MS * 5);
    expect(post).toHaveBeenCalledTimes(1);

    answer();
    await vi.advanceTimersByTimeAsync(TICK_MS);
    expect(await sent_timestamps(post)).toEqual([newer.timestamp, latest.timestamp]);
    expect(skipped.close).toHaveBeenCalled();
    stop();
  });

  it("safety: a stalled or frozen camera sends nothing, never its last frame again", async () => {
    const camera = fake_camera();
    const { post, answer } = held_post();
    const stop = run_track_sender(camera.frames, () => {}, post, encode);

    camera.deliver();
    await vi.advanceTimersByTimeAsync(TICK_MS);
    answer();
    await vi.advanceTimersByTimeAsync(TICK_MS * 30);
    stop();

    expect(post).toHaveBeenCalledTimes(1);
  });

  it("safety: never sends a frame that is no newer than the last one sent", async () => {
    const camera = fake_camera();
    const { post, answer } = held_post();
    const stop = run_track_sender(camera.frames, () => {}, post, encode);

    camera.deliver(100_000);
    await vi.advanceTimersByTimeAsync(TICK_MS);
    answer();
    const repeat = camera.deliver(100_000);
    await vi.advanceTimersByTimeAsync(TICK_MS * 3);
    stop();

    expect(post).toHaveBeenCalledTimes(1);
    expect(repeat.close).toHaveBeenCalled();
  });

  it("reports the frames vision took, its codes, and a frame that couldn't be encoded", async () => {
    const camera = fake_camera();
    const statuses: SenderStatus[] = [];
    const { post, answer } = held_post();
    const broken_once = vi.fn(encode).mockRejectedValueOnce(new Error("no 2D context"));
    const stop = run_track_sender(camera.frames, (status) => statuses.push(status), post, broken_once);

    camera.deliver();
    await vi.advanceTimersByTimeAsync(TICK_MS);
    camera.deliver();
    await vi.advanceTimersByTimeAsync(TICK_MS);
    answer();
    camera.deliver();
    await vi.advanceTimersByTimeAsync(TICK_MS);
    answer({ ok: false, status: 409, code: "not_web_source", message: "" });
    await vi.advanceTimersByTimeAsync(0);
    stop();

    expect(statuses).toEqual([
      { fps: 0, sent: 0, error: "no_picture" },
      { fps: 1, sent: 1, error: null },
      { fps: 1, sent: 1, error: "not_web_source" },
    ]);
  });

  it("safety: stops when the camera's track ends, closing the frame it held", async () => {
    const camera = fake_camera();
    const { post, answer } = held_post();
    run_track_sender(camera.frames, () => {}, post, encode);

    camera.deliver();
    await vi.advanceTimersByTimeAsync(TICK_MS);
    answer();
    const held = camera.deliver();
    camera.end();
    await vi.advanceTimersByTimeAsync(TICK_MS * 5);

    expect(post).toHaveBeenCalledTimes(1);
    expect(held.close).toHaveBeenCalled();
  });

  it("safety: reports nothing once stopped, even an answer already on its way", async () => {
    const camera = fake_camera();
    const statuses: SenderStatus[] = [];
    const { post, answer } = held_post();
    const stop = run_track_sender(camera.frames, (status) => statuses.push(status), post, encode);
    camera.deliver();
    await vi.advanceTimersByTimeAsync(TICK_MS);

    stop();
    answer();
    await vi.advanceTimersByTimeAsync(TICK_MS * 5);

    expect(post).toHaveBeenCalledTimes(1);
    expect(statuses).toEqual([]);
  });
});

describe("handle_command", () => {
  it("starts on the transferred stream, sends JPEGs at most MAX_WIDTH wide, and reports as status messages", async () => {
    vi.useRealTimers();
    const draw_image = vi.fn();
    vi.stubGlobal(
      "OffscreenCanvas",
      class {
        width = 0;
        height = 0;
        getContext = () => ({ drawImage: draw_image });
        convertToBlob = async () => new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: "image/jpeg" });
      },
    );
    let controller!: ReadableStreamDefaultController<VideoFrame>;
    const frames = new ReadableStream<VideoFrame>({ start: (c) => void (controller = c) });
    const frame = { timestamp: 1, displayWidth: 1920, displayHeight: 1080, close: vi.fn() };
    const reports: WorkerReport[] = [];

    // The real encoder and post_frame, answered by the MSW vision mock.
    const stop = handle_command({ type: "start", frames }, (report) => reports.push(report));
    controller.enqueue(frame as unknown as VideoFrame);
    await vi.waitFor(() => expect(reports).toHaveLength(1));
    stop();
    vi.unstubAllGlobals();

    expect(reports).toEqual([{ type: "status", status: { fps: 1, sent: 1, error: null } }]);
    expect(vision_frames_taken()).toBe(1);
    expect(draw_image).toHaveBeenCalledWith(frame, 0, 0, MAX_WIDTH, 720);
    expect(frame.close).toHaveBeenCalled();
  });
});
