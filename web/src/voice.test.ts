import { describe, expect, it } from "vitest";
import hold_peanut from "../../contracts/fixtures/valid/verdict/hold-peanut.json";
import pass_clean from "../../contracts/fixtures/valid/verdict/pass-clean.json";
import type { Verdict } from "./types/TEMPORARY";
import { allergen_words, number_words, spoken_line } from "./voice";

describe("spoken lines match kitchen/gate/voice.py", () => {
  it.each([
    [0, "zero"],
    [7, "seven"],
    [19, "nineteen"],
    [20, "twenty"],
    [42, "forty two"],
    [105, "one oh five"],
  ])("says %i as %s", (n, words) => {
    expect(number_words(n)).toBe(words);
  });

  it("joins allergens the way an expediter would", () => {
    expect(allergen_words(["peanut"])).toBe("peanut");
    expect(allergen_words(["milk", "tree_nut"])).toBe("milk and tree nut");
    expect(allergen_words(["egg", "milk", "soy"])).toBe("egg, milk and soy");
  });

  it("speaks a hold with its flagged allergens, and never a pass", () => {
    expect(spoken_line(hold_peanut as Verdict, 7)).toBe("Hold seven, peanut");
    expect(spoken_line(pass_clean as Verdict, 7)).toBeNull();
  });
});
