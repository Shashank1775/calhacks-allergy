/** Home: what this is, where frames come from, and the two stations. */
import { Chit } from "../components/chit";
import { Rail } from "../components/rail";
import { CameraSource } from "./camera_source";
import { VideoSource } from "./video_source";

export function HomeScreen() {
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

      <Rail title="Sources" note="Where the frames come from.">
        <VideoSource />
        <CameraSource />
      </Rail>

      <Rail title="Stations">
        <Chit label="Tablet station">
          <a className="display station_link" href="/tablet">
            Tablet →
          </a>
          <p>New tickets, one-tap questions, and bin setup at shift start.</p>
        </Chit>
        <Chit label="Expo station">
          <a className="display station_link" href="/expo">
            Expo →
          </a>
          <p>Tickets on the rail, plates on the pass, and why a plate is held.</p>
        </Chit>
      </Rail>
    </>
  );
}
