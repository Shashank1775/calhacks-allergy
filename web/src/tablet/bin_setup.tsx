/**
 * Bin setup at shift start: Laya reads a bin's label, a person checks the
 * profile, and it is saved.
 *
 * Reads: entity (bins), bin_profile. Calls: POST /bins/read, PUT /bins/{id}.
 * Rules: fail closed. If Laya is down the profile starts as unknown, which
 * counts as every allergen, never as an empty (clean) bin.
 */
import { useState } from "react";
import { allergen_name, ALLERGENS } from "../allergens";
import { read_bin_label, save_bin_profile } from "../api/orders";
import { Chit } from "../components/chit";
import { useTableRows } from "../state/hooks";
import type { AllergenProfile } from "../types/TEMPORARY";

const UNKNOWN_PROFILE: AllergenProfile = { probabilities: {}, unknown: true, source: "manual" };

export function BinSetup() {
  const entities = useTableRows("entity");
  const profiles = useTableRows("bin_profile");
  const bins = entities.filter((entity) => entity.kind === "bin");

  const [entity_id, set_entity_id] = useState("");
  const [label_text, set_label_text] = useState("");
  const [profile, set_profile] = useState<AllergenProfile | null>(null);
  const [edited, set_edited] = useState(false);
  const [busy, set_busy] = useState(false);
  const [message, set_message] = useState<string | null>(null);
  const [error, set_error] = useState<string | null>(null);

  async function handle_read() {
    set_busy(true);
    set_error(null);
    set_message(null);
    const result = await read_bin_label(label_text);
    set_busy(false);
    set_edited(false);
    if (result.ok) {
      set_profile(result.data);
    } else if (result.code === "laya_unavailable") {
      set_profile(UNKNOWN_PROFILE);
      set_message("Laya is unavailable. This bin counts as every allergen until you set it by hand.");
    } else {
      set_profile(null);
      set_error(`Couldn't read the label: ${result.message}`);
    }
  }

  function set_probability(allergen_id: string, value: number) {
    if (!profile) return;
    const probabilities = { ...profile.probabilities, [allergen_id]: value };
    if (value === 0) delete probabilities[allergen_id];
    set_profile({ ...profile, probabilities });
    set_edited(true);
  }

  async function handle_save() {
    if (!profile || entity_id.trim() === "") return;
    set_busy(true);
    set_error(null);
    const to_save: AllergenProfile = edited ? { ...profile, source: "manual" } : profile;
    const result = await save_bin_profile(entity_id.trim(), to_save);
    set_busy(false);
    if (result.ok) {
      set_message(`Saved ${entity_id.trim()}.`);
    } else {
      set_error(`Couldn't save: ${result.message}`);
    }
  }

  return (
    <>
      <Chit label="Bins on the line">
        <div className="chit_row bold">
          <span>BINS</span>
          <span>{bins.length}</span>
        </div>
        <hr className="dashed" />
        {bins.length === 0 && <p className="muted">No bins seen yet.</p>}
        <ul className="bin_list">
          {bins.map((bin) => {
            const bin_profile = profiles.find((candidate) => candidate.entity_id === bin.entity_id);
            return (
              <li key={bin.entity_id}>
                <button
                  type="button"
                  className="btn btn_ghost"
                  onClick={() => {
                    set_entity_id(bin.entity_id);
                    set_label_text(bin.label);
                    set_profile(null);
                  }}
                >
                  {bin.entity_id}
                </button>{" "}
                {bin.label} · {profile_summary(bin_profile)}
              </li>
            );
          })}
        </ul>
      </Chit>

      <Chit label="Bin label" wide>
        <div className="chit_row bold">
          <span>READ A LABEL</span>
          <span>{entity_id || "—"}</span>
        </div>
        <hr className="dashed" />
        <label className="field">
          <span>Bin ID</span>
          <input value={entity_id} onChange={(event) => set_entity_id(event.target.value)} placeholder="bin-4" />
        </label>
        <label className="field">
          <span>Label or prep sheet text</span>
          <textarea value={label_text} onChange={(event) => set_label_text(event.target.value)} rows={2} />
        </label>
        <button type="button" className="btn" onClick={handle_read} disabled={busy || label_text.trim() === ""}>
          READ LABEL
        </button>

        {profile && (
          <>
            <hr className="dashed" />
            <label className="check">
              <input
                type="checkbox"
                checked={profile.unknown}
                onChange={(event) => {
                  set_profile({ ...profile, unknown: event.target.checked });
                  set_edited(true);
                }}
              />{" "}
              Contents unknown (counts as every allergen)
            </label>
            <div className="profile_grid">
              {ALLERGENS.map((allergen) => (
                <label key={allergen.id} className="field">
                  <span>{allergen.name}</span>
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={profile.probabilities[allergen.id] ?? 0}
                    onChange={(event) => set_probability(allergen.id, Number(event.target.value))}
                  />
                </label>
              ))}
            </div>
            <button type="button" className="btn" onClick={handle_save} disabled={busy || entity_id.trim() === ""}>
              SAVE BIN
            </button>
          </>
        )}

        {message && <p className="bold">{message}</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </Chit>
    </>
  );
}

function profile_summary(profile: AllergenProfile | undefined): string {
  if (!profile) return "not set up";
  if (profile.unknown) return "unknown (every allergen)";
  const entries = Object.entries(profile.probabilities);
  if (entries.length === 0) return "no allergens";
  return entries.map(([id, probability]) => `${allergen_name(id)} ${Math.round(probability * 100)}%`).join(", ");
}
