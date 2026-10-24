/** MSW server for tests. Base URLs match `test.env` in vite.config.ts. */
import { setupServer } from "msw/node";
import { orders_handlers, summary_handlers, vision_handlers } from "./handlers";

export const TEST_ORDERS_URL = "http://orders.test";
export const TEST_VISION_URL = "http://vision.test";
export const TEST_SUMMARY_URL = "http://summary.test";

export const server = setupServer(
  ...orders_handlers(TEST_ORDERS_URL),
  ...vision_handlers(TEST_VISION_URL),
  ...summary_handlers(TEST_SUMMARY_URL),
);
