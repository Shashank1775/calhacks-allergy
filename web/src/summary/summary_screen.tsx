/**
 * /summary: upload a recorded video to the summary service and read its scene summary: the text, every touch, and
 * what each tool, hand and plate could carry.
 * Rules: a recording is never the live kitchen and never a verdict, so the banner saying so is always on screen.
 * Nothing here reads the state source, decides PASS or HOLD, or touches the gate. A failed job shows its error and
 * no partial summary.
 */
import { useEffect, useState } from "react";
import { summary_url } from "../api/http";
import type { ApiResult } from "../api/http";
import { get_summary, summary_error_message, upload_video } from "../api/summary";
import type { Summary, SummaryJob } from "../api/summary";
import { Chit } from "../components/chit";
import { Rail } from "../components/rail";
import { SummaryResult } from "./summary_result";

export const RECORDED_BANNER = "RECORDED VIDEO · NOT THE LIVE KITCHEN · NOT A PASS/HOLD VERDICT";

type Job =
  | { stage: "idle" }
  | { stage: "uploading" }
  | { stage: "running"; summary_id: string; progress: number }
  | { stage: "done"; summary: Summary }
  | { stage: "failed"; message: string };

/** The next stage after one poll of the job. Anything unexpected is a failure, never a partial summary. */
function after_poll(result: ApiResult<SummaryJob>, summary_id: string): Job {
  if (!result.ok)
    return { stage: "failed", message: summary_error_message(result.status, result.code, result.message) };
  const job = result.data;
  if (job.status === "failed") {
    return { stage: "failed", message: `The summary failed: ${job.error?.message ?? "the service gave no reason"}` };
  }
  if (job.status === "done") {
    if (job.summary) return { stage: "done", summary: job.summary };
    return { stage: "failed", message: "The service said it was done but sent no summary." };
  }
  return { stage: "running", summary_id, progress: job.progress };
}

export function SummaryScreen({ poll_ms = 1000 }: { poll_ms?: number }) {
  const [file, set_file] = useState<File | null>(null);
  const [job, set_job] = useState<Job>({ stage: "idle" });
  const configured = Boolean(summary_url());
  const busy = job.stage === "uploading" || job.stage === "running";

  // Poll a running job every poll_ms, one request at a time, until it's done or failed.
  useEffect(() => {
    if (job.stage !== "running") return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      const result = await get_summary(job.summary_id);
      if (!cancelled) set_job(after_poll(result, job.summary_id));
    }, poll_ms);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job, poll_ms]);

  async function summarize() {
    if (!file) return;
    set_job({ stage: "uploading" });
    const result = await upload_video(file);
    if (!result.ok) {
      set_job({ stage: "failed", message: summary_error_message(result.status, result.code, result.message) });
      return;
    }
    set_job({ stage: "running", summary_id: result.data.summary_id, progress: 0 });
  }

  return (
    <>
      <div className="banner banner_replay banner_sticky" role="note">
        {RECORDED_BANNER}
      </div>

      <section className="hero">
        <p className="kicker">RECORDED VIDEO</p>
        <h1 className="display hero_title">Scene summary.</h1>
        <p className="hero_text">
          Upload a recording of prep. The summary service runs it through vision and the provenance graph and says what
          was on the counter, every touch, and what each tool, hand and plate could carry.
        </p>
      </section>

      <Rail title="Recording">
        <Chit label="Video to summarize" wide>
          {!configured && (
            <p className="error" role="alert">
              The summary service isn't set up. Put WEB_SUMMARY_URL=http://localhost:8003 in web/.env.local, start the
              service with "uv run python tools/video_summary.py --serve", and restart the web app.
            </p>
          )}
          <label className="drop">
            <input
              type="file"
              accept="video/*"
              className="visually_hidden"
              disabled={busy}
              onChange={(event) => set_file(event.target.files?.[0] ?? null)}
            />
            {file ? file.name : "PICK A RECORDED VIDEO →"}
          </label>
          <button className="btn" type="button" disabled={!file || !configured || busy} onClick={summarize}>
            SUMMARIZE
          </button>

          {job.stage === "uploading" && <p>Uploading…</p>}
          {job.stage === "running" && (
            <>
              <progress className="summary_progress" max={1} value={job.progress} aria-label="Summary progress" />
              <p>Summarizing… {Math.round(job.progress * 100)}%</p>
            </>
          )}
          {job.stage === "failed" && (
            <p className="error" role="alert">
              {job.message}
            </p>
          )}
        </Chit>
      </Rail>

      {job.stage === "done" && <SummaryResult summary={job.summary} />}
    </>
  );
}
