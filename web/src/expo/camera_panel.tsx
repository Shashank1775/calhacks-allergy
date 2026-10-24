/** The camera view with overlays. Drawn as a placeholder until the live camera exists. */
import { ToBeImplemented } from "../components/to_be_implemented";

export function CameraPanel() {
  return (
    <section className="rail_section">
      <div className="rail_head">
        <h2 className="display">Camera</h2>
        <span className="rail_note">CAM-01</span>
      </div>
      <div className="camera_box">
        <span className="stamp stamp_blue">86'D</span>
        <ToBeImplemented>
          Live camera with overlays: each entity's latest box from <code>entity_state</code>, drawn over{" "}
          <code>GET /stream.mjpg</code> and colored by allergen risk.
        </ToBeImplemented>
      </div>
    </section>
  );
}
