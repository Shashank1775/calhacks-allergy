/**
 * Home: what this is, where frames come from, the two stations and the kitchen layout. Picking a video or adding a
 * camera turns this tab into the kitchen's camera: box the kitchen, then start (kitchen_run.tsx).
 *
 * The whole flow stays on this page: links reload the app, and a camera stream or a picked file can't follow.
 */
import { useState } from "react";
import { Chit } from "../components/chit";
import { Rail } from "../components/rail";
import { stop } from "./camera_dialog";
import { CameraSource } from "./camera_source";
import { KitchenRun, type Source } from "./kitchen_run";
import { VideoSource } from "./video_source";

export function HomeScreen() {
  const [source, set_source] = useState<Source | null>(null);

  function change_source() {
    if (source?.kind === "camera") stop(source.stream);
    set_source(null);
  }

  if (source) return <KitchenRun source={source} on_change_source={change_source} />;

  return (
    <>
      <section className="hero">
        <p className="kicker">KITCHEN ALLERGEN TRACKING</p>
        <h1 className="display hero_title">Every plate gets a ticket.</h1>
        <p className="hero_text">
          A camera watches the line. Every touch between a bin, a tool, a hand and a plate goes on the plate's ticket,
          with the time it happened. <strong>At the pass, any doubt means HOLD.</strong>
        </p>
      </section>

      <Rail title="Sources" note="Step 1 of 3: where the frames come from.">
        <VideoSource on_use={(file) => set_source({ kind: "video", file })} />
        <CameraSource on_use={(stream, label) => set_source({ kind: "camera", stream, label })} />
      </Rail>

      <Rail title="Stations">
        <Chit label="Tablet station">
          <a className="display station_link" href="/tablet">
            Tablet →
          </a>
          <p>New tickets and one-tap questions.</p>
        </Chit>
        <Chit label="Expo station">
          <a className="display station_link" href="/expo">
            Expo →
          </a>
          <p>Tickets on the rail, plates on the pass, and why a plate is held.</p>
        </Chit>
        <Chit label="Kitchen layout">
          <a className="display station_link" href="/zones">
            Kitchen layout →
          </a>
          <p>Where the bins, washing areas and serving area are on the camera.</p>
        </Chit>
      </Rail>
    </>
  );
}
