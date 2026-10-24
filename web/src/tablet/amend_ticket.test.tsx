import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { live_replay, render_with_source } from "../test_utils";
import { AmendTicket } from "./amend_ticket";

async function answer(user: ReturnType<typeof userEvent.setup>, prompt: RegExp) {
  const question = await screen.findByRole("group", { name: prompt });
  await user.click(within(question).getByRole("button", { name: "Yes, keep it off" }));
}

describe("safety: an amendment can't be sent with open questions", () => {
  it("reads the change, holds Send until every question is answered, then sends revision 2", async () => {
    const user = userEvent.setup();
    const source = live_replay("knife-carries-peanut-to-plate");
    render_with_source(<AmendTicket />, source);
    act(() => source.advance_to(Number.POSITIVE_INFINITY));

    await user.selectOptions(screen.getByLabelText("Ticket"), "t1");
    await user.type(screen.getByLabelText("What changed?"), "also no dairy");
    await user.click(screen.getByRole("button", { name: "READ CHANGE" }));

    await answer(user, /Keep tree nuts off/);
    expect(screen.getByRole("button", { name: "SEND AMENDMENT" })).toBeDisabled();
    await answer(user, /Keep peanuts off/);
    await user.click(await screen.findByRole("button", { name: "SEND AMENDMENT" }));

    expect(await screen.findByText(/AMENDED · TABLE 7 · REV 2/)).toBeInTheDocument();
  });

  it("says when there are no sent tickets to change", () => {
    render_with_source(<AmendTicket />, live_replay("knife-carries-peanut-to-plate"));
    expect(screen.getByText("No sent tickets yet.")).toBeInTheDocument();
  });
});
