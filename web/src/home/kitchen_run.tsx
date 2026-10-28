/**
 * The kitchen, fed from this tab: box the kitchen over the chosen camera or video, then send its frames to vision.
 *
 * Reads: the chosen source (a camera stream or a video file); the layout through ZonesScreen (GET /zones).
 * Emits: PUT /zones (ZonesScreen's SAVE); POST /frames while running (frame_sender.ts).
 * Rules: known-clean source. An object's origin is set the first time vision sees it, so START stays off until vision
 * has exactly the layout on screen. The first frame starts vision's shift, so what's in view in its first 3 s starts
 * clean. Fail closed: STOP, a closed tab, a video that ends or a camera that stops all stop the frames, and the gate
 * shows FAULT. A frozen picture is never sent. Where frames come from this tab's main thread (a video file, or a
 * browser that can't read a camera in a worker), a hidden tab slows them to about one a second, and the run screen
 * says so.
 */
import { useEffect, useRef, useState, type RefObject } from "react";
import { FRAME_ERRORS } from "../api/vision";
import { ZonesScreen } from "../zones/zones_screen";
import { sender_mode, start_camera_worker, start_frame_sender, type SenderStatus } from "./frame_sender";
import { useObjectUrl } from "./use_object_url";
import { UNPLAYABLE_VIDEO } from "./video_source";

export type Source = { kind: "video"; file: File } | { kind: "camera"; stream: MediaStream; label: string };

type Phase = "layout" | "running" | "stopped";

/** On the run screen when frames come from the main thread and this tab has been hidden since START. */
export const HIDDEN_TAB_WARNING =
  "This tab was hidden. While it is, frames slow to about 1 a second and the kitchen loses track: keep this tab " +
  "visible, or open /expo in its own window side by side.";

export function KitchenRun({ source, on_change_source }: { source: Source; on_change_source: () => void }) {
  const [phase, set_phase] = useState<Phase>("layout");
  const [ready, set_ready] = useState(false);
  const [started, set_started] = useState(false);
  const [aspect, set_aspect] = useState(16 / 9);
  const [unplayable, set_unplayable] = useState(false);
  const [status, set_status] = useState<SenderStatus | null>(null);
  const [hidden_since_start, set_hidden_since_start] = useState(false);
  const video_ref = useRef<HTMLVideoElement>(null);
  const video_url = useObjectUrl(source.kind === "video" ? source.file : null);
  const name = source.kind === "camera" ? source.label : source.file.name;
  const camera_track = source.kind === "camera" ? source.stream.getVideoTracks()[0] : undefined;
  const mode = sender_mode(camera_track);

  useEffect(() => {
    if (phase !== "running") return;
    set_status(null);
    if (mode === "camera_worker" && camera_track) return start_camera_worker(camera_track, set_status);
    return start_frame_sender(() => (source_live(source) ? video_ref.current : null), set_status);
  }, [phase]);

  // Only the main thread slows in a hidden tab. The warning stays up after the tab is back, so it's seen.
  useEffect(() => {
    if (phase !== "running" || mode !== "main_thread") return;
    set_hidden_since_start(document.hidden);
    const on_visibility = () => {
      if (document.hidden) set_hidden_since_start(true);
    };
    document.addEventListener("visibilitychange", on_visibility);
    return () => document.removeEventListener("visibilitychange", on_visibility);
  }, [phase]);

  // A camera that is unplugged or taken by another app ends its track: stop, rather than resend its last picture.
  useEffect(() => {
    if (source.kind !== "camera") return;
    const track = source.stream.getVideoTracks()[0];
    const on_ended = () => set_phase("stopped");
    track?.addEventListener("ended", on_ended);
    return () => track?.removeEventListener("ended", on_ended);
  }, [source]);

  function start() {
    set_started(true);
    set_phase("running");
  }

  const picture = (
    <SourceVideo
      source={source}
      url={video_url}
      video_ref={video_ref}
      playing={phase === "running"}
      on_aspect={set_aspect}
      on_ended={() => set_phase("stopped")}
      on_error={() => set_unplayable(true)}
    />
  );

  if (phase === "layout") {
    return (
      <>
        <section className="rail_section" aria-label="Box the kitchen">
          <div className="rail_head">
            <h2 className="display">Box the kitchen</h2>
            <span className="rail_note">STEP 2 OF 3 · {name}</span>
          </div>
          <p>
            Drag a box over each bin, the wash, the plating spot, the pass and the clean stacks, then SAVE. Put the
            props in place first: everything in view in the first 3 s after START starts clean, and anything that
            appears later has unknown history.
          </p>
          <div className="option_row">
            <button type="button" className="btn" disabled={!ready || unplayable} onClick={start}>
              START
            </button>
            <button type="button" className="btn btn_ghost" onClick={on_change_source}>
              CHANGE SOURCE
            </button>
          </div>
          {!ready && <p className="muted">START turns on once vision has this layout: SAVE your changes first.</p>}
          {unplayable && (
            <p role="alert" className="error">
              {UNPLAYABLE_VIDEO}
            </p>
          )}
          {started && (
            <p className="muted">
              The shift started with the first frame. Starting again continues it; restart the runner for a fresh shift.
            </p>
          )}
        </section>
        <ZonesScreen picture={{ element: picture, aspect }} on_ready={set_ready} />
      </>
    );
  }

  const running = phase === "running";
  return (
    <section className="rail_section" aria-label="Feeding the kitchen">
      <div className="rail_head">
        <h2 className="display">{running ? "Feeding the kitchen" : "Frames stopped"}</h2>
        <span className="rail_note">
          STEP 3 OF 3 · {running ? `LIVE · ${status?.fps ?? 0} frames/s · ${status?.sent ?? 0} sent` : "STOPPED"}
        </span>
      </div>
      <p className="bold">
        {mode === "camera_worker"
          ? "Keep this tab open: it is the kitchen's camera. The stations open in windows of their own."
          : "Keep this tab open and in front: it is the kitchen's camera. The stations open in windows of their own."}
      </p>
      <div className="option_row">
        <button type="button" className="btn" onClick={() => set_phase("layout")}>
          {running ? "STOP" : "BACK TO LAYOUT"}
        </button>
        <StationWindowLink path="/expo" name="EXPO" />
        <StationWindowLink path="/tablet" name="TABLET" />
      </div>
      {running && mode === "main_thread" && hidden_since_start && (
        <p role="alert" className="error">
          {HIDDEN_TAB_WARNING}
        </p>
      )}
      {running && status?.error && (
        <p role="alert" className="error">
          {FRAME_ERRORS[status.error] ?? `Vision didn't take the frame (${status.error}).`}
        </p>
      )}
      {!running && (
        <p role="status">
          {source.kind === "video" ? "The video ended." : "The camera stopped."} No frames reach vision now, so the gate
          shows FAULT.
        </p>
      )}
      <div className="camera_box">
        <div className="zone_frame" style={{ aspectRatio: aspect }}>
          {picture}
        </div>
      </div>
    </section>
  );
}

/**
 * Opens a station in a window of its own rather than a tab: a new tab would hide this one, and a browser can pause a
 * hidden tab's video. The link still works as a link (middle-click, keyboard) if script can't open the window.
 */
function StationWindowLink({ path, name }: { path: string; name: string }) {
  return (
    <a
      className="btn btn_ghost"
      href={path}
      target={name.toLowerCase()}
      onClick={(event) => {
        if (window.open(path, name.toLowerCase(), "popup,width=1200,height=900")) event.preventDefault();
      }}
    >
      OPEN {name} WINDOW ↗
    </a>
  );
}

/** A camera whose track has ended can keep showing its last picture, which must never be sent as live. */
function source_live(source: Source): boolean {
  return source.kind === "video" || source.stream.getVideoTracks()[0]?.readyState === "live";
}

type SourceVideoProps = {
  source: Source;
  url: string | null;
  video_ref: RefObject<HTMLVideoElement | null>;
  /** A video file plays (once, from the start) only while running; a camera always shows live. */
  playing: boolean;
  on_aspect: (aspect: number) => void;
  on_ended: () => void;
  /** The browser can't play the video file: it could send no frames. */
  on_error: () => void;
};

/** The source's picture: the camera live, or the video file (its first frame until it plays). */
function SourceVideo({ source, url, video_ref, playing, on_aspect, on_ended, on_error }: SourceVideoProps) {
  useEffect(() => {
    const video = video_ref.current;
    if (!video || source.kind !== "camera") return;
    video.srcObject = source.stream;
    void video.play?.()?.catch(() => {});
  }, [source]);

  return (
    <video
      ref={video_ref}
      src={source.kind === "video" ? (url ?? undefined) : undefined}
      aria-label={source.kind === "camera" ? `Camera: ${source.label}` : `Video: ${source.file.name}`}
      muted
      playsInline
      preload="auto"
      autoPlay={source.kind === "camera" || playing}
      onLoadedMetadata={(event) => {
        const video = event.currentTarget;
        if (video.videoWidth && video.videoHeight) on_aspect(video.videoWidth / video.videoHeight);
      }}
      onEnded={on_ended}
      onError={on_error}
    />
  );
}
