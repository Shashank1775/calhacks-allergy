/**
 * The kitchen, fed from this tab: box the kitchen over the chosen camera or video, then send its frames to vision.
 *
 * Reads: the chosen source (a camera stream or a video file); the layout through ZonesScreen (GET /zones).
 * Emits: PUT /zones (ZonesScreen's SAVE); POST /frames while running (frame_sender.ts).
 * Rules: known-clean source. An object's origin is set the first time vision sees it, so START stays off until vision
 * has exactly the layout on screen. The first frame starts vision's shift, so what's in view in its first 3 s starts
 * clean. Fail closed: STOP, a closed tab, a video that ends or a camera that stops all stop the frames, and the gate
 * shows FAULT. A frozen picture is never sent.
 */
import { useEffect, useRef, useState, type RefObject } from "react";
import { FRAME_ERRORS } from "../api/vision";
import { ZonesScreen } from "../zones/zones_screen";
import { start_frame_sender, type SenderStatus } from "./frame_sender";
import { useObjectUrl } from "./use_object_url";

export type Source = { kind: "video"; file: File } | { kind: "camera"; stream: MediaStream; label: string };

type Phase = "layout" | "running" | "stopped";

export function KitchenRun({ source, on_change_source }: { source: Source; on_change_source: () => void }) {
  const [phase, set_phase] = useState<Phase>("layout");
  const [ready, set_ready] = useState(false);
  const [started, set_started] = useState(false);
  const [aspect, set_aspect] = useState(16 / 9);
  const [status, set_status] = useState<SenderStatus | null>(null);
  const video_ref = useRef<HTMLVideoElement>(null);
  const video_url = useObjectUrl(source.kind === "video" ? source.file : null);
  const name = source.kind === "camera" ? source.label : source.file.name;

  useEffect(() => {
    if (phase !== "running") return;
    set_status(null);
    return start_frame_sender(() => (source_live(source) ? video_ref.current : null), set_status);
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
            <button type="button" className="btn" disabled={!ready} onClick={start}>
              START
            </button>
            <button type="button" className="btn btn_ghost" onClick={on_change_source}>
              CHANGE SOURCE
            </button>
          </div>
          {!ready && <p className="muted">START turns on once vision has this layout: SAVE your changes first.</p>}
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
      <p className="bold">Keep this tab open: it is the kitchen's camera. Open the stations in new tabs.</p>
      <div className="option_row">
        <button type="button" className="btn" onClick={() => set_phase("layout")}>
          {running ? "STOP" : "BACK TO LAYOUT"}
        </button>
        <a className="btn btn_ghost" href="/expo" target="_blank" rel="noopener">
          OPEN EXPO ↗
        </a>
        <a className="btn btn_ghost" href="/tablet" target="_blank" rel="noopener">
          OPEN TABLET ↗
        </a>
      </div>
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
};

/** The source's picture: the camera live, or the video file (its first frame until it plays). */
function SourceVideo({ source, url, video_ref, playing, on_aspect, on_ended }: SourceVideoProps) {
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
    />
  );
}
