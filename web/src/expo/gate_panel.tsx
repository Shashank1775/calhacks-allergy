/**
 * The pass gate, on screen: the plate at the pass in large type, PASS, HOLD, FAULT or WAITING,
 * and the spoken hold.
 *
 * Reads: the state source's tables, vision health. Rules: every state comes from gate_view(),
 * which takes PASS only from plate_badge(); the voice follows what is already on screen.
 */
import { useState } from "react";
import { allergen_name } from "../allergens";
import { GATE_TEXT, gate_view } from "../gate";
import { reason_label } from "../reasons";
import { useFeedStatus, useStateSource, useTableRows } from "../state/hooks";
import { useVisionHealth } from "../vision_health";
import { speech_available, useSpokenHolds } from "../voice";

export function GatePanel() {
  const feed = useFeedStatus();
  const source = useStateSource();
  const vision = useVisionHealth();
  const tables = {
    zone: useTableRows("zone"),
    entity: useTableRows("entity"),
    entity_state: useTableRows("entity_state"),
    plate_binding: useTableRows("plate_binding"),
    ticket: useTableRows("ticket"),
    verdict: useTableRows("verdict"),
  };
  // A replay's rows run on the scenario's clock, not vision's, so only live rows are aged by its frames.
  const newest_frame_t_ms = source?.is_replay ? null : vision.last_frame_t_ms;
  const view = gate_view(feed, vision.health, tables, newest_frame_t_ms);

  const [voice_on, set_voice_on] = useState(false);
  useSpokenHolds(view.state === "hold" ? view.verdict : undefined, view.ticket?.number, voice_on);

  const reasons = view.state === "hold" && view.verdict ? view.verdict.reasons : [];

  return (
    <section className={`gate_panel gate_${view.state}`} aria-label="Gate at the pass">
      <div className="gate_head">
        <span className="rail_note">THE PASS</span>
        <VoiceButton on={voice_on} turn_on={() => set_voice_on(true)} />
      </div>
      <p className="gate_word display">{GATE_TEXT[view.state]}</p>
      {view.ticket && (
        <p className="gate_ticket bold">
          TABLE {view.ticket.number} · {view.plate_id}
        </p>
      )}
      {view.note && <p className="gate_note">{view.note}</p>}
      {reasons.length > 0 && (
        <ul className="gate_reasons">
          {reasons.map((reason, index) => (
            <li key={index}>
              {reason_label(reason.code)}
              {reason.allergen_unit_id && `: ${allergen_name(reason.allergen_unit_id)}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function VoiceButton({ on, turn_on }: { on: boolean; turn_on: () => void }) {
  if (!speech_available()) {
    return (
      <button type="button" className="btn btn_ghost" disabled>
        NO VOICE IN THIS BROWSER
      </button>
    );
  }
  return (
    <button type="button" className="btn btn_ghost" onClick={turn_on} disabled={on} aria-pressed={on}>
      {on ? "VOICE ON" : "TURN ON VOICE"}
    </button>
  );
}
