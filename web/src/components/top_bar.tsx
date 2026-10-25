/** The black band across the top: name and the five paths. */
import type { Screen } from "../app";

const LINKS: { screen: Screen; href: string; text: string }[] = [
  { screen: "home", href: "/", text: "SOURCES" },
  { screen: "tablet", href: "/tablet", text: "TABLET" },
  { screen: "expo", href: "/expo", text: "EXPO" },
  { screen: "summary", href: "/summary", text: "SUMMARY" },
  { screen: "zones", href: "/zones", text: "LAYOUT" },
];

export function TopBar({ current }: { current: Screen }) {
  return (
    <header className="top_bar">
      <a href="/" className="display brand">
        ALLERGEN TRACKER
      </a>
      <nav className="top_nav">
        {LINKS.map((link) => (
          <a key={link.href} href={link.href} aria-current={link.screen === current ? "page" : undefined}>
            {link.text}
          </a>
        ))}
      </nav>
    </header>
  );
}
