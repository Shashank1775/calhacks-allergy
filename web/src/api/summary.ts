/**
 * Client for the video summary service (tools/video_summary.py --serve): upload a recorded video, then poll its job.
 * The shapes are the service's own API (issue #198), not a contract table.
 * Rules: a summary describes a recording. It is never a verdict, and nothing here touches the gate.
 */
import { fetch_api, summary_url } from "./http";

export type SummaryEntity = {
  entity_id: string;
  kind: string;
  label: string;
  origin: string;
  first_seen_t_ms: number;
  last_seen_t_ms: number;
};

export type SummaryTouch = {
  event_id: string;
  t_start_ms: number;
  t_end_ms: number;
  a_id: string;
  b_id: string;
  action: string;
  probability: number;
};

export type SummaryExposure = {
  entity_id: string;
  kind: string;
  unknown_history: boolean;
  allergens: { allergen_unit_id: string; probability: number; chain: unknown[] }[];
};

export type Summary = {
  video: { name: string; duration_ms: number; frames: number; frames_processed: number };
  detector: string;
  shift_start_ms: number;
  entities: SummaryEntity[];
  touches: SummaryTouch[];
  exposure: SummaryExposure[];
  text: string;
  warnings: string[];
};

export type SummaryJob = {
  summary_id: string;
  status: "running" | "done" | "failed";
  progress: number;
  summary: Summary | null;
  error: { code: string; message: string } | null;
};

export function upload_video(video: File) {
  const form = new FormData();
  form.append("video", video);
  return fetch_api<{ summary_id: string; status: string }>(summary_url(), "/summaries", { method: "POST", body: form });
}

export function get_summary(summary_id: string) {
  return fetch_api<SummaryJob>(summary_url(), `/summaries/${encodeURIComponent(summary_id)}`, { method: "GET" });
}

/** What to tell the person for each way an upload or a job can fail. */
export function summary_error_message(status: number, code: string, message: string): string {
  if (code === "not_configured") return "The summary service isn't set up: WEB_SUMMARY_URL is empty.";
  if (code === "network_error") return "Couldn't reach the summary service. Is tools/video_summary.py --serve running?";
  if (code === "bad_video") return `The service couldn't read that video: ${message}`;
  if (code === "bad_zones") return `The service's zones file is wrong: ${message}`;
  if (status === 413) return "That video is too big for the summary service (the limit is 200 MB).";
  if (status === 404) return "The summary service no longer has this job. Upload the video again.";
  return `The summary failed: ${message || code}`;
}
