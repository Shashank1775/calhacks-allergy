/** A paper order ticket hanging from the rail, with a torn bottom edge. */
import type { ReactNode } from "react";

const TEAR_PATH =
  "M0 0 L2.5 5 L5 0 L7.5 5 L10 0 L12.5 5 L15 0 L17.5 5 L20 0 L22.5 5 L25 0 L27.5 5 L30 0 L32.5 5 L35 0 L37.5 5 L40 0 L42.5 5 L45 0 L47.5 5 L50 0 L52.5 5 L55 0 L57.5 5 L60 0 L62.5 5 L65 0 L67.5 5 L70 0 L72.5 5 L75 0 L77.5 5 L80 0 L82.5 5 L85 0 L87.5 5 L90 0 L92.5 5 L95 0 L97.5 5 L100 0 Z";

export function Chit({ children, label, wide = false }: { children: ReactNode; label: string; wide?: boolean }) {
  return (
    <article className={wide ? "chit_wrap chit_wide" : "chit_wrap"} aria-label={label}>
      <div className="chit_clip" aria-hidden="true" />
      <div className="chit">{children}</div>
      <svg className="tear" viewBox="0 0 100 5" preserveAspectRatio="none" aria-hidden="true">
        <path d={TEAR_PATH} />
      </svg>
    </article>
  );
}
