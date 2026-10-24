/**
 * React access to the state source. With no source, every table is empty and
 * the feed is `lost` (fail closed).
 */
import { createContext, useContext, useSyncExternalStore } from "react";
import type { FeedStatus, StateSource, TableName, Tables } from "./state_source";

export const StateSourceContext = createContext<StateSource | null>(null);

const NO_ROWS: never[] = [];
const subscribe_nothing = () => () => {};

export function useStateSource(): StateSource | null {
  return useContext(StateSourceContext);
}

export function useTableRows<T extends TableName>(table: T): Tables[T] {
  const source = useStateSource();
  return useSyncExternalStore(source ? (listener) => source.subscribe(listener) : subscribe_nothing, () =>
    source ? source.rows(table) : (NO_ROWS as Tables[T]),
  );
}

export function useFeedStatus(): FeedStatus {
  const source = useStateSource();
  return useSyncExternalStore(source ? (listener) => source.subscribe(listener) : subscribe_nothing, () =>
    source ? source.feed_status() : "lost",
  );
}
