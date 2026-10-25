/**
 * Source 01: a recorded video of prep.
 * Picks a file and previews it in the browser. USE THIS VIDEO takes it to the layout step; once started, this tab
 * plays it once, in real time, and sends its frames to vision like a camera's.
 */
import { useState } from "react";
import { Chit } from "../components/chit";
import { useObjectUrl } from "./use_object_url";

export function VideoSource({ on_use }: { on_use: (file: File) => void }) {
  const [file, set_file] = useState<File | null>(null);
  const preview_url = useObjectUrl(file);

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
      <p>
        <a href="/summary">Summarize a recording →</a> what was touched and what could carry which allergen.
      </p>

      {file && (
        <>
          <p className="chit_row">
            <span>FILE</span>
            <span className="wrap">{file.name}</span>
          </p>
          {preview_url && <video className="preview" src={preview_url} controls muted />}
          <p className="bold center">** REPLAY · NOT A LIVE PLATE **</p>
          <button type="button" className="btn" onClick={() => on_use(file)}>
            USE THIS VIDEO
          </button>
        </>
      )}
    </Chit>
  );
}
