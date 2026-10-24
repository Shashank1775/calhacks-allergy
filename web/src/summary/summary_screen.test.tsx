import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SUMMARY_EXAMPLE } from "../api/mocks/handlers";
import { server, TEST_SUMMARY_URL } from "../api/mocks/server";
import { RECORDED_BANNER, SummaryScreen } from "./summary_screen";
import { format_time } from "./summary_result";

const VIDEO = new File(["frames"], "prep.mp4", { type: "video/mp4" });

async function summarize() {
  const user = userEvent.setup();
  render(<SummaryScreen poll_ms={20} />);
  await user.upload(screen.getByLabelText(/PICK A RECORDED VIDEO/), VIDEO);
  await user.click(screen.getByRole("button", { name: "SUMMARIZE" }));
}

function banner_shown() {
  expect(screen.getByRole("note")).toHaveTextContent(RECORDED_BANNER);
}

afterEach(() => vi.unstubAllEnvs());

describe("SummaryScreen", () => {
  it("uploads, shows progress, then the text, the timeline and the exposure", async () => {
    await summarize();
    expect(await screen.findByText("Summarizing… 50%")).toBeInTheDocument();
    expect(await screen.findByText(SUMMARY_EXAMPLE.text)).toBeInTheDocument();

    expect(screen.getByText("0:05.1")).toBeInTheDocument();
    expect(screen.getByText("1:05.3")).toBeInTheDocument();
    expect(screen.getAllByText("knife (tool-knife-1) → plate (plate-1)")).toHaveLength(1);
    expect(screen.getByText("scoop")).toBeInTheDocument();
    expect(screen.getByText("Peanuts 84%")).toBeInTheDocument();
    expect(screen.getByText("could carry any allergen")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
    banner_shown();
  });

  it("shows the warnings above everything else in the summary", async () => {
    await summarize();
    const text = await screen.findByText(SUMMARY_EXAMPLE.text);
    const warnings = screen.getByRole("list", { name: "Warnings" });
    expect(warnings).toHaveTextContent(SUMMARY_EXAMPLE.warnings[0]);
    expect(warnings.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows a failed job's error and no partial summary", async () => {
    server.use(
      http.get(`${TEST_SUMMARY_URL}/summaries/:summary_id`, () =>
        HttpResponse.json({
          summary_id: "s-1",
          status: "failed",
          progress: 0.4,
          summary: SUMMARY_EXAMPLE,
          error: { code: "decode_failed", message: "the video ended early" },
        }),
      ),
    );
    await summarize();
    expect(await screen.findByRole("alert")).toHaveTextContent("the video ended early");
    expect(screen.queryByText(SUMMARY_EXAMPLE.text)).toBeNull();
    expect(screen.queryByText("Every touch")).toBeNull();
    banner_shown();
  });

  it("treats done without a summary as a failure", async () => {
    server.use(
      http.get(`${TEST_SUMMARY_URL}/summaries/:summary_id`, () =>
        HttpResponse.json({ summary_id: "s-1", status: "done", progress: 1, summary: null, error: null }),
      ),
    );
    await summarize();
    expect(await screen.findByRole("alert")).toHaveTextContent("sent no summary");
  });

  it.each([
    [400, { error: { code: "bad_video", message: "not a video file" } }, "couldn't read that video: not a video file"],
    [400, { error: { code: "bad_zones", message: "no pass zone" } }, "zones file is wrong: no pass zone"],
    [413, null, "too big for the summary service (the limit is 200 MB)"],
  ])("shows a clear message when the upload gets %i", async (status, body, message) => {
    server.use(
      http.post(`${TEST_SUMMARY_URL}/summaries`, () =>
        body ? HttpResponse.json(body, { status }) : new HttpResponse("Request Entity Too Large", { status }),
      ),
    );
    await summarize();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("Every touch")).toBeNull();
  });

  it("says plainly when WEB_SUMMARY_URL isn't set, and can't upload", async () => {
    vi.stubEnv("WEB_SUMMARY_URL", "");
    const user = userEvent.setup();
    render(<SummaryScreen />);
    expect(screen.getByRole("alert")).toHaveTextContent("WEB_SUMMARY_URL");
    await user.upload(screen.getByLabelText(/PICK A RECORDED VIDEO/), VIDEO);
    expect(screen.getByRole("button", { name: "SUMMARIZE" })).toBeDisabled();
    banner_shown();
  });

  it("safety: shows the recorded-video banner and never PASS or HOLD", async () => {
    render(<SummaryScreen />);
    banner_shown();
    expect(screen.queryByText(/^(PASS|HOLD)$/)).toBeNull();
  });
});

describe("format_time", () => {
  it.each([
    [0, "0:00.0"],
    [5100, "0:05.1"],
    [59960, "1:00.0"],
    [65250, "1:05.3"],
    [600000, "10:00.0"],
  ])("formats %i ms as %s", (t_ms, expected) => {
    expect(format_time(t_ms)).toBe(expected);
  });
});
