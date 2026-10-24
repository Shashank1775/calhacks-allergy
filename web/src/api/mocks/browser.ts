/** MSW worker for demos on localhost (WEB_MOCK_API=1, dev build only). */
import { setupWorker } from "msw/browser";
import { orders_url, vision_url } from "../http";
import { orders_handlers, vision_handlers } from "./handlers";

export const worker = setupWorker(...orders_handlers(orders_url()!), ...vision_handlers(vision_url()!));
