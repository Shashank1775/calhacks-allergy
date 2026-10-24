import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import hold_peanut from "../../../contracts/fixtures/valid/verdict/hold-peanut.json";
import verdict_schema from "../../../contracts/schemas/verdict.schema.json";
import { REASON_LABELS, reason_label } from "../reasons";
import type { Verdict } from "../types/TEMPORARY";
import { HoldDetails } from "./hold_details";

describe("HoldDetails", () => {
  it("renders every chain step with its confidence", () => {
    render(<HoldDetails verdict={hold_peanut as Verdict} />);
    expect(screen.getByText("bin-4 → tool-knife-17")).toBeInTheDocument();
    expect(screen.getByText("tool-knife-17 → plate-t7")).toBeInTheDocument();
    expect(screen.getByText("91%")).toBeInTheDocument();
    expect(screen.getByText("88%")).toBeInTheDocument();
  });

  it("has a human label for every reason code in the schema", () => {
    const codes: string[] = verdict_schema.properties.reasons.items.properties.code.enum;
    for (const code of codes) expect(REASON_LABELS[code]).toBeTruthy();
  });

  it("still shows a reason code it doesn't know", () => {
    expect(reason_label("brand_new_code")).toContain("brand_new_code");
  });
});
