import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { describe, expect, it } from "vitest";
import { server, TEST_ORDERS_URL } from "../api/mocks/server";
import { TicketEntry } from "./ticket_entry";

async function read_note(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Guest's note"), "severe nut allergy, coconut is fine");
  await user.click(screen.getByRole("button", { name: "READ NOTE" }));
}

describe("safety: no unanswered questions", () => {
  it("keeps Send disabled until every question is answered", async () => {
    const user = userEvent.setup();
    render(<TicketEntry />);
    await read_note(user);

    await user.type(await screen.findByLabelText("Table"), "7");
    const send = screen.getByRole("button", { name: "SEND TICKET" });
    expect(send).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Yes, peanuts too" }));
    expect(screen.getByRole("button", { name: "SEND TICKET" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Yes, even traces" }));
    expect(await screen.findByRole("button", { name: "SEND TICKET" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "SEND TICKET" }));
    expect(await screen.findByText(/SENT · TABLE 7/)).toBeInTheDocument();
  });

  it("keeps the questions up when orders answers 409 unanswered_questions", async () => {
    server.use(
      http.post(`${TEST_ORDERS_URL}/parse`, () =>
        HttpResponse.json({
          draft_id: "d-1",
          note: "x",
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
        HttpResponse.json({ error: { code: "unanswered_questions", message: "open" } }, { status: 409 }),
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

describe("safety: fail closed when Laya is down", () => {
  it("shows manual entry, never a guessed parse", async () => {
    server.use(
      http.post(`${TEST_ORDERS_URL}/parse`, () =>
        HttpResponse.json({ error: { code: "laya_unavailable", message: "down" } }, { status: 503 }),
      ),
    );
    const user = userEvent.setup();
    render(<TicketEntry />);
    await read_note(user);

    expect(await screen.findByRole("region", { name: "Manual entry" })).toBeInTheDocument();
    expect(screen.queryByText("LAYA READ:")).toBeNull();
  });
});
