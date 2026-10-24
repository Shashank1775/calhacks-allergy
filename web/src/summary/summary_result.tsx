/**
 * A finished scene summary: warnings first, then the text, the touch timeline, and what each entity could carry.
 * Rules: this describes a recording. It never shows PASS, HOLD or "clean"; unknown history reads "could carry any
 * allergen".
 */
import { allergen_name } from "../allergens";
import type { Summary } from "../api/summary";
import { Chit } from "../components/chit";
import { Rail } from "../components/rail";

const ACTION_WORDS: Record<string, string> = {
  "transfer.scoop": "scoop",
  "transfer.contact": "contact",
  "transfer.wipe": "wipe",
  "clean.wash": "wash",
};

/** m:ss.s, e.g. 65250 ms is 1:05.3. Rounded to tenths first, so 59960 ms is 1:00.0, never 0:60.0. */
export function format_time(t_ms: number): string {
  const tenths = Math.round(t_ms / 100);
  const minutes = Math.floor(tenths / 600);
  const seconds = ((tenths % 600) / 10).toFixed(1).padStart(4, "0");
  return `${minutes}:${seconds}`;
}

function percent(probability: number): string {
  return `${Math.round(probability * 100)}%`;
}

export function SummaryResult({ summary }: { summary: Summary }) {
  const labels = new Map(summary.entities.map((entity) => [entity.entity_id, entity.label]));
  const name = (entity_id: string) => {
    const label = labels.get(entity_id);
    return label && label !== entity_id ? `${label} (${entity_id})` : entity_id;
  };

  return (
    <Rail title="Summary" note={`${summary.video.name} · ${format_time(summary.video.duration_ms)}`}>
      <Chit label="Scene summary" wide>
        {summary.warnings.length > 0 && (
          <ul className="error" aria-label="Warnings">
            {summary.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}
        <p>{summary.text}</p>

        <div className="table_scroll">
          <table className="chain_table">
            <caption>Every touch</caption>
            <thead>
              <tr>
                <th>At</th>
                <th>Who → what</th>
                <th>Action</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {summary.touches.map((touch) => (
                <tr key={touch.event_id}>
                  <td>{format_time(touch.t_start_ms)}</td>
                  <td>
                    {name(touch.a_id)} → {name(touch.b_id)}
                  </td>
                  <td>{ACTION_WORDS[touch.action] ?? touch.action}</td>
                  <td>{percent(touch.probability)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {summary.touches.length === 0 && <p className="muted">No touches found.</p>}
      </Chit>

      <Chit label="What each could carry" wide>
        <ul className="exposure_list">
          {summary.exposure.map((entry) => (
            <li key={entry.entity_id}>
              <span className="bold">{name(entry.entity_id)}</span>
              <span className="chips">
                {entry.unknown_history && <span className="chip chip_unknown">could carry any allergen</span>}
                {entry.allergens.map((allergen) => (
                  <span className="chip" key={allergen.allergen_unit_id}>
                    {allergen_name(allergen.allergen_unit_id)} {percent(allergen.probability)}
                  </span>
                ))}
                {!entry.unknown_history && entry.allergens.length === 0 && (
                  <span className="muted">no allergen found</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      </Chit>
    </Rail>
  );
}
