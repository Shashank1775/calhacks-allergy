/**
 * Marks a piece of UI that is drawn but not wired up yet.
 * Every use is listed in web/SCOPE.md.
 */
import type { ReactNode } from "react";

export function ToBeImplemented({ children }: { children: ReactNode }) {
  return (
    <p className="tbi">
      <strong>To be implemented.</strong> {children}
    </p>
  );
}
