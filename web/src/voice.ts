/**
 * Spoken holds from the browser: "Hold seven, peanut".
 *
 * Mirrors kitchen/gate/voice.py (number words, allergen words, the hold line), so the screen
 * says what the gate would. Uses the browser's speech; browsers only allow it after a click,
 * so the expo screen has a "turn on voice" button.
 * Rules: voice is never on the path to HOLD: the panel shows the hold first, and a broken or
 * missing voice changes nothing on screen.
 */
import { useEffect, useRef } from "react";
import type { Verdict } from "./types/TEMPORARY";

const ONES =
  "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(
    " ",
  );
const TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split(" ");

/** 7 → "seven", 42 → "forty two", 105 → "one oh five" (three digits or more, digit by digit). */
export function number_words(n: number): string {
  if (n < 20) return ONES[n];
  if (n < 100) {
    const tens = Math.floor(n / 10);
    const ones = n % 10;
    return ones === 0 ? TENS[tens] : `${TENS[tens]} ${ONES[ones]}`;
  }
  return String(n)
    .split("")
    .map((digit) => (digit === "0" ? "oh" : ONES[Number(digit)]))
    .join(" ");
}

/** ["peanut"] → "peanut", ["milk", "tree_nut"] → "milk and tree nut". */
export function allergen_words(allergen_unit_ids: string[]): string {
  const words = allergen_unit_ids.map((unit) => unit.replace(/_/g, " "));
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** The line for a verdict: "Hold seven, peanut" for a hold, nothing for a pass. */
export function spoken_line(verdict: Verdict, ticket_number: number): string | null {
  if (verdict.decision === "pass") return null;
  const line = `Hold ${number_words(ticket_number)}`;
  const flagged = verdict.flagged.map((item) => item.allergen_unit_id);
  return flagged.length > 0 ? `${line}, ${allergen_words(flagged)}` : line;
}

export function speech_available(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance === "function";
}

/** Speaks each new hold once: a verdict already spoken stays quiet, even when it shows again. */
export function useSpokenHolds(verdict: Verdict | undefined, ticket_number: number | undefined, enabled: boolean) {
  const spoken = useRef(new Set<string>());

  useEffect(() => {
    if (!enabled || !verdict || ticket_number === undefined || spoken.current.has(verdict.verdict_id)) return;
    const line = spoken_line(verdict, ticket_number);
    if (!line) return;
    spoken.current.add(verdict.verdict_id);
    try {
      window.speechSynthesis.speak(new SpeechSynthesisUtterance(line));
    } catch {
      // A browser without working speech still shows the hold on screen.
    }
  }, [enabled, verdict, ticket_number]);
}
