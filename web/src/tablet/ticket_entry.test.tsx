import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { describe, expect, it } from "vitest";
import send_unanswered_example from "../../../kitchen/orders/fakes/http/send_unanswered.json";
import { server, TEST_ORDERS_URL } from "../api/mocks/server";
import { TicketEntry } from "./ticket_entry";

const NOTE = "allergic to nuts, carries an epipen";

async function read_note(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Guest's note"), NOTE);
  await user.click(screen.getByRole("button", { name: "READ NOTE" }));
}

/** Clicks an option inside one question; both questions use the same option labels. */
async function answer(user: ReturnType<typeof userEvent.setup>, prompt: RegExp, option: string) {
  const question = await screen.findByRole("group", { name: prompt });
  await user.click(within(question).getByRole("button", { name: option }));
}

describe("safety: no unanswered questions", () => {
  it("keeps Send disabled until every question is answered", async () => {
    const user = userEvent.setup();
    render(<TicketEntry />);
    await read_note(user);

    await user.type(await screen.findByLabelText("Table"), "7");
    expect(screen.getByRole("button", { name: "SEND TICKET" })).toBeDisabled();

    await answer(user, /Keep tree nuts off/, "Yes, keep it off");
    expect(screen.getByRole("button", { name: "SEND TICKET" })).toBeDisabled();

    await answer(user, /Keep peanuts off/, "Yes, keep it off");
    expect(await screen.findByRole("button", { name: "SEND TICKET" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "SEND TICKET" }));
    expect(await screen.findByText(/SENT · TABLE 7/)).toBeInTheDocument();
  });

  it("keeps the questions up when orders answers 409 unanswered_questions", async () => {
    server.use(
      http.post(`${TEST_ORDERS_URL}/parse`, () =>
        HttpResponse.json({
          draft_id: "d-1",
          note: NOTE,
          source: "typed",
          parser: { name: "laya", version: "test" },
          constraints: [],
          exceptions: [],
          questions: [],
          question_threshold: 0.85,
          sendable: true,
        }),
      ),
      http.post(`${TEST_ORDERS_URL}/drafts/:id/send`, () =>
        HttpResponse.json(send_unanswered_example.response, { status: send_unanswered_example.status }),
      ),
    );
    const user = userEvent.setup();
    render(<TicketEntry />);
    await read_note(user);
    await user.type(await screen.findByLabelText("Table"), "3");
    await user.click(screen.getByRole("button", { name: "SEND TICKET" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("question is still open");
    expect(screen.getByLabelText("Table")).toBeInTheDocument();
  });
});

describe("TicketEntry", () => {
  it("says so when the table number was already sent this shift", async () => {
    server.use(
      http.post(`${TEST_ORDERS_URL}/drafts/:id/send`, () =>
        HttpResponse.json({ error: { code: "number_in_use", message: "in use" } }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    render(<TicketEntry />);
    await read_note(user);
    await answer(user, /Keep tree nuts off/, "Yes, keep it off");
    await answer(user, /Keep peanuts off/, "Yes, keep it off");
    await user.type(screen.getByLabelText("Table"), "7");
    await user.click(await screen.findByRole("button", { name: "SEND TICKET" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Table 7 already has a ticket this shift");
  });
});

describe("safety: fail closed when Laya is down", () => {
  it("shows the error and keeps the note, never a guessed parse", async () => {
    server.use(
      http.post(`${TEST_ORDERS_URL}/parse`, () =>
        HttpResponse.json({ error: { code: "laya_unavailable", message: "down" } }, { status: 503 }),
      ),
    );
    const user = userEvent.setup();
    render(<TicketEntry />);
    await read_note(user);

    expect(await screen.findByRole("alert")).toHaveTextContent("nothing was guessed");
    expect(screen.getByLabelText("Guest's note")).toHaveValue(NOTE);
    expect(screen.queryByText("LAYA READ:")).toBeNull();
    expect(screen.queryByRole("button", { name: "SEND TICKET" })).toBeNull();
  });
});
