import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { describe, expect, it } from "vitest";
import { server, TEST_ORDERS_URL } from "../api/mocks/server";
import { render_with_source } from "../test_utils";
import { BinSetup } from "./bin_setup";

describe("BinSetup", () => {
  it("reads a label, lets the person adjust it, and saves it as manual", async () => {
    let saved: unknown = null;
    server.use(
      http.put(`${TEST_ORDERS_URL}/bins/:entity_id`, async ({ request, params }) => {
        const body = (await request.json()) as Record<string, number | boolean | string | object>;
        saved = { entity_id: params.entity_id, body };
        return HttpResponse.json(body);
      }),
    );
    const user = userEvent.setup();
    render_with_source(<BinSetup />, null);

    await user.type(screen.getByLabelText("Bin ID"), "bin-4");
    await user.type(screen.getByLabelText("Label or prep sheet text"), "satay sauce: peanuts, soy");
    await user.click(screen.getByRole("button", { name: "READ LABEL" }));

    const sesame = await screen.findByLabelText("Sesame");
    await user.clear(sesame);
    await user.type(sesame, "0.5");
    await user.click(screen.getByRole("button", { name: "SAVE BIN" }));

    await screen.findByText("Saved bin-4.");
    expect(saved).toMatchObject({ entity_id: "bin-4", body: { source: "manual", probabilities: { sesame: 0.5 } } });
  });

  it("safety: starts as unknown (every allergen) when Laya is down", async () => {
    server.use(
      http.post(`${TEST_ORDERS_URL}/bins/read`, () =>
        HttpResponse.json({ error: { code: "laya_unavailable", message: "down" } }, { status: 503 }),
      ),
    );
    const user = userEvent.setup();
    render_with_source(<BinSetup />, null);

    await user.type(screen.getByLabelText("Label or prep sheet text"), "house sauce");
    await user.click(screen.getByRole("button", { name: "READ LABEL" }));

    expect(await screen.findByLabelText(/Contents unknown/)).toBeChecked();
  });
});
