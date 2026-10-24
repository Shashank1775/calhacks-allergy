/**
 * The rubber stamp on a plate. It only ever shows what plate_badge() returned;
 * this is the one place the word PASS is drawn.
 */
import { BADGE_TEXT, type Badge } from "../badge";

export function BadgeStamp({ badge }: { badge: Badge }) {
  return <span className={`stamp stamp_${badge}`}>{BADGE_TEXT[badge]}</span>;
}
