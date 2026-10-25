import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { vision_frames_taken } from "../api/mocks/handlers";
import type { ApiResult } from "../api/http";
import { post_frame } from "../api/vision";
import { MAX_WIDTH, start_frame_sender, TARGET_FPS, type SenderStatus } from "./frame_sender";

type FrameResult = ApiResult<null>;

const TICK_MS = 1000 / TARGET_FPS;
const JPEG = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: "image/jpeg" });
const draw_image = vi.fn();

/** A playing 1920 × 1080 video, as far as the sender can tell. */
function playing_video(overrides: Partial<HTMLVideoElement> = {}) {
  return { readyState: 4, videoWidth: 1920, videoHeight: 1080, paused: false, ended: false, ...overrides };
}

/** A post() whose answers the test hands out one at a time, to see what waits on what. */
function held_post() {
  const answers: ((result: FrameResult) => void)[] = [];
  const post = vi.fn(() => new Promise<FrameResult>((resolve) => answers.push(resolve)));
  return { post, answer: (result: FrameResult) => answers.shift()!(result) };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: draw_image,
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((done) => done(JPEG));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  draw_image.mockClear();
});

describe("start_frame_sender", () => {
  it("sends the picture as a JPEG at most MAX_WIDTH wide, one request at a time", async () => {
    const video = playing_video();
    const { post, answer } = held_post();
    const stop = start_frame_sender(
      () => video as HTMLVideoElement,
      () => {},
      post,
    );

    await vi.advanceTimersByTimeAsync(TICK_MS);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(JPEG);
    expect(draw_image).toHaveBeenCalledWith(video, 0, 0, MAX_WIDTH, 720);

    // While vision hasn't answered, ticks send nothing: a slow vision never builds a queue.
    await vi.advanceTimersByTimeAsync(TICK_MS * 10);
    expect(post).toHaveBeenCalledTimes(1);

    answer({ ok: true, data: null });
    await vi.advanceTimersByTimeAsync(TICK_MS);
    expect(post).toHaveBeenCalledTimes(2);
    stop();
  });

  it("reports the frames vision took, and the code of one it refused", async () => {
    const statuses: SenderStatus[] = [];
    const { post, answer } = held_post();
    const stop = start_frame_sender(
      () => playing_video() as HTMLVideoElement,
      (status) => statuses.push(status),
      post,
    );

    await vi.advanceTimersByTimeAsync(TICK_MS);
    answer({ ok: true, data: null });
    await vi.advanceTimersByTimeAsync(TICK_MS);
    answer({ ok: false, status: 409, code: "not_web_source", message: "" });
    await vi.advanceTimersByTimeAsync(0);
    stop();

    expect(statuses).toEqual([
      { fps: 1, sent: 1, error: null },
      { fps: 1, sent: 1, error: "not_web_source" },
    ]);
  });

  it.each([
    ["paused", { paused: true }],
    ["ended", { ended: true }],
    ["without a picture yet", { readyState: 1 }],
  ])("safety: sends nothing from a video that is %s", async (_, overrides) => {
    const { post } = held_post();
    const stop = start_frame_sender(
      () => playing_video(overrides) as HTMLVideoElement,
      () => {},
      post,
    );

    await vi.advanceTimersByTimeAsync(TICK_MS * 5);
    stop();

    expect(post).not.toHaveBeenCalled();
  });

  it("safety: sends nothing once stopped, even an answer already on its way", async () => {
    const statuses: SenderStatus[] = [];
    const { post, answer } = held_post();
    const stop = start_frame_sender(
      () => playing_video() as HTMLVideoElement,
      (status) => statuses.push(status),
      post,
    );
    await vi.advanceTimersByTimeAsync(TICK_MS);

    stop();
    answer({ ok: true, data: null });
    await vi.advanceTimersByTimeAsync(TICK_MS * 5);

    expect(post).toHaveBeenCalledTimes(1);
    expect(statuses).toEqual([]);
  });
});

describe("post_frame", () => {
  beforeEach(() => vi.useRealTimers());

  it("sends a JPEG to vision's POST /frames", async () => {
    expect(await post_frame(JPEG)).toEqual({ ok: true, data: null });
    expect(vision_frames_taken()).toBe(1);
  });

  it("comes back with vision's code for a body that isn't a JPEG", async () => {
    const result = await post_frame(new Blob(["not a picture"], { type: "image/jpeg" }));
    expect(result).toMatchObject({ ok: false, status: 422, code: "bad_frame" });
    expect(vision_frames_taken()).toBe(0);
  });
});
