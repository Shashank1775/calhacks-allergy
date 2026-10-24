import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { describe, expect, it } from "vitest";
import { server, TEST_VISION_URL } from "../api/mocks/server";
import { START_PLATE_ERRORS } from "../api/vision";
import { StartPlateButton } from "./start_plate_button";

describe("StartPlateButton", () => {
  it("shows the plate vision bound", async () => {
    const user = userEvent.setup();
    render(<StartPlateButton ticket_id="t7" />);
    await user.click(screen.getByRole("button", { name: "START PLATE" }));
    expect(await screen.findByText("Bound plate-t7.")).toBeInTheDocument();
  });

  it.each(Object.keys(START_PLATE_ERRORS))("shows a distinct message for 409 %s and binds nothing", async (code) => {
    server.use(
      http.post(`${TEST_VISION_URL}/plates/start`, () =>
        HttpResponse.json({ error: { code, message: code } }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    render(<StartPlateButton ticket_id="t7" />);
    await user.click(screen.getByRole("button", { name: "START PLATE" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(START_PLATE_ERRORS[code]);
    expect(screen.queryByText(/^Bound/)).toBeNull();
  });
});
