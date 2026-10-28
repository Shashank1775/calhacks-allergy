/**
 * The frame worker's entry point, started by start_camera_worker() in frame_sender.ts. The work is in
 * track_sender.ts; this only wires it to the page. The page stops it with terminate().
 */
import { handle_command, type WorkerCommand, type WorkerReport } from "./track_sender";

// TypeScript's DOM types describe a window; this file runs in a dedicated worker.
const scope = self as unknown as {
  onmessage: ((event: MessageEvent<WorkerCommand>) => void) | null;
  postMessage: (report: WorkerReport) => void;
};

// One start per worker: a second one would read nothing (the stream is already being read), so it's ignored.
scope.onmessage = (event) => {
  scope.onmessage = null;
  handle_command(event.data, (report) => scope.postMessage(report));
};
