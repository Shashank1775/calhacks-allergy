/**
 * Source 01: a recorded video of prep.
 * Picks a file and previews it in the browser. Handing it to vision is not
 * built: vision has no upload endpoint yet (see web/SCOPE.md).
 */
import { useEffect, useState } from "react";
import { Chit } from "../components/chit";
import { ToBeImplemented } from "../components/to_be_implemented";

export function VideoSource() {
  const [file, set_file] = useState<File | null>(null);
  const [preview_url, set_preview_url] = useState<string | null>(null);

  useEffect(() => {
    // jsdom has no createObjectURL; the preview is a nicety, not required.
    if (!file || typeof URL.createObjectURL !== "function") {
      set_preview_url(null);
      return;
    }
    const url = URL.createObjectURL(file);
    set_preview_url(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  return (
    <Chit label="Video source">
      <div className="chit_row bold">
        <span>SOURCE 01</span>
        <span>SRC: FILE</span>
      </div>
      <hr className="dashed" />
      <h3 className="display chit_title">1× upload a video</h3>
      <p className="muted">a recording of prep · mp4 · mov · webm</p>
      <label className="drop">
        <input
          type="file"
          accept="video/*"
          className="visually_hidden"
          onChange={(event) => set_file(event.target.files?.[0] ?? null)}
        />
        STAPLE VIDEO HERE →
      </label>

      {file && (
        <>
          <p className="chit_row">
            <span>FILE</span>
            <span className="wrap">{file.name}</span>
          </p>
          {preview_url && <video className="preview" src={preview_url} controls muted />}
          <p className="bold center">** REPLAY · NOT A LIVE PLATE **</p>
          <ToBeImplemented>
            Sending the video to vision. Vision has no upload endpoint yet; for now run vision on the file locally.
          </ToBeImplemented>
        </>
      )}
    </Chit>
  );
}
