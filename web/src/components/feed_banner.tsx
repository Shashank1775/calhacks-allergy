/**
 * Says where the data on screen comes from.
 * Rules: fail closed. FEED LOST whenever there is no live source; REPLAY
 * whenever the data is a scenario, so a replay never passes for the kitchen.
 */
import { useFeedStatus, useStateSource } from "../state/hooks";

export function FeedBanner() {
  const source = useStateSource();
  const feed = useFeedStatus();

  if (feed === "lost") {
    return (
      <div className="banner banner_lost" role="status">
        FEED LOST · NO PLATE CAN PASS
      </div>
    );
  }
  if (feed === "connecting") {
    return (
      <div className="banner banner_connecting" role="status">
        CONNECTING…
      </div>
    );
  }
  if (source?.is_replay) {
    return (
      <div className="banner banner_replay" role="status">
        REPLAY · {source.label} · NOT THE LIVE KITCHEN
      </div>
    );
  }
  return null;
}
