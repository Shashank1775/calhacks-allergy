/**
 * Maps the URL path to a screen. No router library (web-framework decision):
 * /tablet, /expo, and anything else shows the home page with the sources.
 */
import { FeedBanner } from "./components/feed_banner";
import { TopBar } from "./components/top_bar";
import { ExpoScreen } from "./expo/expo_screen";
import { HomeScreen } from "./home/home_screen";
import { TabletScreen } from "./tablet/tablet_screen";

export type Screen = "home" | "tablet" | "expo";

export function screen_for_path(path: string): Screen {
  const trimmed = path.replace(/\/+$/, "");
  if (trimmed === "/tablet") return "tablet";
  if (trimmed === "/expo") return "expo";
  return "home";
}

export function App({ path }: { path: string }) {
  const screen = screen_for_path(path);
  return (
    <>
      <TopBar current={screen} />
      <FeedBanner />
      <main className="page">
        {screen === "tablet" && <TabletScreen />}
        {screen === "expo" && <ExpoScreen />}
        {screen === "home" && <HomeScreen />}
      </main>
    </>
  );
}
