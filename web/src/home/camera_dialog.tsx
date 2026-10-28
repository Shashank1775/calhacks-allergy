/**
 * The "add a camera" popup: asks the browser for a camera, lists the computer's cameras with a live preview of the
 * chosen one, and hands that camera's stream to the home page.
 *
 * Reads: navigator.mediaDevices (getUserMedia, enumerateDevices). Works on http://localhost or HTTPS only.
 * Emits: on_use(stream, label) with the open stream; nothing reaches vision until the kitchen is started.
 * Rules: a stream not handed over is stopped (camera switched, popup cancelled), so the camera light goes out.
 */
import { useEffect, useRef, useState } from "react";

type Props = {
  on_use: (stream: MediaStream, label: string) => void;
  on_cancel: () => void;
};

export function CameraDialog({ on_use, on_cancel }: Props) {
  const [cameras, set_cameras] = useState<MediaDeviceInfo[]>([]);
  // "" opens the browser's default camera; picking one in the list opens that one.
  const [device_id, set_device_id] = useState("");
  const [stream, set_stream] = useState<MediaStream | null>(null);
  const [error, set_error] = useState<string | null>(null);
  const preview_ref = useRef<HTMLVideoElement>(null);
  const handed_over = useRef<MediaStream | null>(null);

  // Open the chosen camera; the first open also asks for permission, which the camera names need.
  useEffect(() => {
    let cancelled = false;
    let opened: MediaStream | null = null;

    async function open() {
      const media = navigator.mediaDevices;
      if (!media?.getUserMedia) {
        set_error("This browser can't open a camera on this page. Open the app at http://localhost or over HTTPS.");
        return;
      }
      try {
        opened = await media.getUserMedia({ video: device_id ? { deviceId: { exact: device_id } } : true });
      } catch (problem) {
        if (!cancelled) set_error(camera_error(problem));
        return;
      }
      if (cancelled) {
        stop(opened);
        return;
      }
      set_stream(opened);
      set_error(null);
      const devices = await media.enumerateDevices();
      if (!cancelled) set_cameras(devices.filter((device) => device.kind === "videoinput"));
    }

    void open();
    return () => {
      cancelled = true;
      if (opened && opened !== handed_over.current) stop(opened);
    };
  }, [device_id]);

  useEffect(() => {
    const preview = preview_ref.current;
    if (!preview || !stream) return;
    preview.srcObject = stream;
    void preview.play?.()?.catch(() => {});
  }, [stream]);

  const current_id = device_id || stream?.getVideoTracks()[0]?.getSettings().deviceId || "";
  const current = cameras.find((camera) => camera.deviceId === current_id);

  function use() {
    if (!stream) return;
    handed_over.current = stream;
    on_use(stream, current?.label || "Camera");
  }

  return (
    <div className="dialog_backdrop">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="camera_dialog_title">
        <h2 id="camera_dialog_title" className="display">
          Add a camera
        </h2>
        <p className="muted">The browser asks once to use the camera. Pick the one that looks down at the counter.</p>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {cameras.length > 0 && (
          <label className="dialog_field">
            CAMERA
            <select value={current_id} onChange={(event) => set_device_id(event.target.value)}>
              {cameras.map((camera, index) => (
                <option key={camera.deviceId} value={camera.deviceId}>
                  {camera.label || `Camera ${index + 1}`}
                </option>
              ))}
            </select>
          </label>
        )}
        <video ref={preview_ref} className="preview" aria-label="Camera preview" muted playsInline />
        <div className="option_row">
          <button type="button" className="btn" disabled={!stream} onClick={use}>
            USE THIS CAMERA
          </button>
          <button type="button" className="btn btn_ghost" onClick={on_cancel}>
            CANCEL
          </button>
        </div>
      </div>
    </div>
  );
}

/** Stops every track, so the camera turns off. */
export function stop(stream: MediaStream): void {
  for (const track of stream.getTracks()) track.stop();
}

/** Why the camera didn't open, in words the cook can act on. */
function camera_error(problem: unknown): string {
  const name = problem instanceof Error || problem instanceof DOMException ? problem.name : "";
  if (name === "NotAllowedError") {
    return "Camera access was refused. Allow the camera for this page (the icon in the address bar), then try again.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No camera found. Plug one in and try again.";
  if (name === "NotReadableError") return "The camera is in use by another app. Close it and try again.";
  return `Couldn't open the camera${name ? ` (${name})` : ""}.`;
}
