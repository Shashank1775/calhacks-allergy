/** Why a plate is held: every reason, the flagged allergens, and the chain step by step. */
import { allergen_name } from "../allergens";
import { reason_label } from "../reasons";
import type { Verdict } from "../types/TEMPORARY";

export function HoldDetails({ verdict }: { verdict: Verdict }) {
  return (
    <div className="hold_details">
      <h3>Reasons</h3>
      <ul>
        {verdict.reasons.map((reason, index) => (
          <li key={index}>
            {reason_label(reason.code)}
            {reason.allergen_unit_id && `: ${allergen_name(reason.allergen_unit_id)}`}
            {reason.detail && ` (${reason.detail})`}
          </li>
        ))}
      </ul>

      {verdict.flagged.length > 0 && (
        <p className="bold">
          FLAGGED:{" "}
          {verdict.flagged
            .map((flag) => `${allergen_name(flag.allergen_unit_id)} ${Math.round(flag.probability * 100)}%`)
            .join(", ")}
        </p>
      )}

      {verdict.chain.length > 0 && (
        <div className="table_scroll">
          <table className="chain_table">
            <caption>How it got there</caption>
            <thead>
              <tr>
                <th>Step</th>
                <th>From → to</th>
                <th>Action</th>
                <th>At</th>
                <th>Allergen</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {verdict.chain.map((step) => (
                <tr key={step.step}>
                  <td>{step.step}</td>
                  <td>
                    {step.from_id} → {step.to_id}
                  </td>
                  <td>{step.action}</td>
                  <td>{(step.t_ms / 1000).toFixed(1)} s</td>
                  <td>{allergen_name(step.allergen_unit_id)}</td>
                  <td>{Math.round(step.step_confidence * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
