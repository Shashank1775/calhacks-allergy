import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { live_replay, render_with_source } from "../test_utils";
import { GatePanel } from "./gate_panel";

function panel() {
  return within(screen.getByRole("region", { name: "Gate at the pass" }));
}

describe("GatePanel on a replay", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(window, "speechSynthesis");
  });

  it("holds the peanut plate at the pass, with its table and reason", async () => {
    const source = live_replay("knife-carries-peanut-to-plate");
    render_with_source(<GatePanel />, source);
    act(() => source.advance_to(Number.POSITIVE_INFINITY));

    expect(await panel().findByText("HOLD")).toBeInTheDocument();
    expect(panel().getByText(/TABLE 1/)).toBeInTheDocument();
    expect(panel().getByText(/A forbidden allergen can reach the plate: Peanuts/)).toBeInTheDocument();
  });

  it("passes the clean plate, and waits before any plate reaches the pass", async () => {
    const source = live_replay("clean-plate-from-stack");
    render_with_source(<GatePanel />, source);
    act(() => source.advance_to(0));
    expect(await panel().findByText("WAITING")).toBeInTheDocument();

    act(() => source.advance_to(Number.POSITIVE_INFINITY));
    expect(await panel().findByText("PASS")).toBeInTheDocument();
  });

  it("safety: shows FAULT, not the old PASS, when the feed drops", async () => {
    const source = live_replay("clean-plate-from-stack");
    render_with_source(<GatePanel />, source);
    act(() => source.advance_to(Number.POSITIVE_INFINITY));
    expect(await panel().findByText("PASS")).toBeInTheDocument();

    act(() => source.stop());
    expect(panel().getByText("FAULT")).toBeInTheDocument();
    expect(panel().queryByText("PASS")).toBeNull();
  });

  it("speaks each hold once, after the voice is turned on", async () => {
    const speak = vi.fn();
    vi.stubGlobal(
      "SpeechSynthesisUtterance",
      class {
        text: string;
        constructor(text: string) {
          this.text = text;
        }
      },
    );
    Object.defineProperty(window, "speechSynthesis", { value: { speak }, configurable: true });

    const user = userEvent.setup();
    const source = live_replay("knife-carries-peanut-to-plate");
    render_with_source(<GatePanel />, source);
    act(() => source.advance_to(Number.POSITIVE_INFINITY));
    await panel().findByText("HOLD");
    expect(speak).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "TURN ON VOICE" }));
    expect(speak).toHaveBeenCalledTimes(1);
    expect(speak.mock.calls[0][0].text).toBe("Hold one, peanut");

    act(() => source.advance_to(Number.POSITIVE_INFINITY));
    expect(speak).toHaveBeenCalledTimes(1);
  });
});
