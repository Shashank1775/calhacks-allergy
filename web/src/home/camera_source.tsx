/** Source 02: the live kitchen camera. 86'd until the Pi and Hailo are connected. */
import { Chit } from "../components/chit";
import { ToBeImplemented } from "../components/to_be_implemented";

export function CameraSource() {
  return (
    <Chit label="Live camera source">
      <div className="chit_row bold">
        <span>SOURCE 02</span>
        <span>SRC: CAM-01</span>
      </div>
      <hr className="dashed" />
      <h3 className="display chit_title">1× live camera</h3>
      <p className="muted">Pi Camera Module 3 · Hailo-8</p>
      <div className="stamp_box">
        <span className="stamp stamp_blue">86'D</span>
      </div>
      <button type="button" className="btn" disabled>
        CONNECT CAMERA
      </button>
      <ToBeImplemented>Live camera feed. It comes back once the Pi camera and Hailo are wired up.</ToBeImplemented>
    </Chit>
  );
}
