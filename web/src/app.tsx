/**
 * Maps the URL path to a screen. No router library (web-framework decision):
 * /tablet, /expo, /summary, /zones (the kitchen layout), and anything else shows the home page with the sources.
 * /summary is about a recorded video, not the live feed, so it shows its own banner instead of the feed's.
 */
import { FeedBanner } from "./components/feed_banner";
import { TopBar } from "./components/top_bar";
import { ExpoScreen } from "./expo/expo_screen";
import { HomeScreen } from "./home/home_screen";
import { SummaryScreen } from "./summary/summary_screen";
import { TabletScreen } from "./tablet/tablet_screen";
import { ZonesScreen } from "./zones/zones_screen";
import {GraphScreen} from "./graph/graph_screen";


export type Screen = "home" | "tablet" | "expo" | "summary" | "zones" | "graph";


export function screen_for_path(path: string): Screen {
  const trimmed = path.replace(/\/+$/, "");
  if (trimmed === "/tablet") return "tablet";
  if (trimmed === "/expo") return "expo";
  if (trimmed === "/summary") return "summary";
  if (trimmed === "/zones") return "zones";
  if (trimmed === "/graph") return "graph";
  return "home";
}

export function App({ path }: { path: string }) {
  const screen = screen_for_path(path);
  return (
    <>
      <TopBar current={screen} />
      {screen !== "summary" && <FeedBanner />}
      <main className="page">
        {screen === "tablet" && <TabletScreen />}
        {screen === "expo" && <ExpoScreen />}
        {screen === "home" && <HomeScreen />}
        {screen === "summary" && <SummaryScreen />}
        {screen === "zones" && <ZonesScreen />}
        {screen === "graph" && <GraphScreen />}
      </main>
    </>
  );
}
