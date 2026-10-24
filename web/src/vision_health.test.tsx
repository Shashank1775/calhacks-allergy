import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw/http";
import { describe, expect, it } from "vitest";
import { server, TEST_VISION_URL } from "./api/mocks/server";
import { useVisionHealth } from "./vision_health";

describe("safety: vision health fails closed", () => {
  it("is stale until the first answer, then ok while frames advance", async () => {
    const { result } = renderHook(() => useVisionHealth());
    expect(result.current).toBe("stale");
    await waitFor(() => expect(result.current).toBe("ok"));
  });

  it("goes stale when last_frame_t_ms stops advancing", async () => {
    server.use(
      http.get(`${TEST_VISION_URL}/health`, () =>
        HttpResponse.json({ fps: 0, detector: "onnx", last_frame_t_ms: 5000 }),
      ),
    );
    const { result } = renderHook(() => useVisionHealth());
    await waitFor(() => expect(result.current).toBe("ok"));
    await waitFor(() => expect(result.current).toBe("stale"), { timeout: 3000 });
  });

  it("is stale when health can't be read", async () => {
    server.use(http.get(`${TEST_VISION_URL}/health`, () => HttpResponse.error()));
    const { result } = renderHook(() => useVisionHealth());
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(result.current).toBe("stale");
  });
});
