/** A titled steel rail that chits hang from. */
import type { ReactNode } from "react";

export function Rail({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="rail_section">
      <div className="rail_head">
        <h2 className="display">{title}</h2>
        {note && <span className="rail_note">{note}</span>}
      </div>
      <div className="rail" aria-hidden="true" />
      <div className="rail_row">{children}</div>
    </section>
  );
}
