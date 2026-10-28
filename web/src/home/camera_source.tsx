/** Source 02: a camera on this computer, opened by the browser, whose frames this tab sends to vision. */
import { useState } from "react";
import { Chit } from "../components/chit";
import { CameraDialog } from "./camera_dialog";

export function CameraSource({ on_use }: { on_use: (stream: MediaStream, label: string) => void }) {
  const [adding, set_adding] = useState(false);

  return (
    <Chit label="Live camera source">
      <div className="chit_row bold">
        <span>SOURCE 02</span>
        <span>SRC: CAM-01</span>
      </div>
      <hr className="dashed" />
      <h3 className="display chit_title">1× live camera</h3>
      <p className="muted">a camera on this computer, through the browser · looking straight down at the counter</p>
      <button type="button" className="btn" onClick={() => set_adding(true)}>
        ADD CAMERA
      </button>
      {adding && (
        <CameraDialog
          on_use={(stream, label) => {
            set_adding(false);
            on_use(stream, label);
          }}
          on_cancel={() => set_adding(false)}
        />
      )}
    </Chit>
  );
}
