/**
 * Vitest setup: MSW answers every API call, and an unhandled request is an
 * error, so no test can reach a real service.
 */
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import { reset_orders_mock } from "./api/mocks/handlers";
import { server } from "./api/mocks/server";

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));

afterEach(() => {
  server.resetHandlers();
  reset_orders_mock();
  cleanup();
});

afterAll(() => server.close());
