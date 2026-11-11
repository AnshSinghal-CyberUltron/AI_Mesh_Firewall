// Single source of truth for the three F6 visual directions.
// `node build-tokens.mjs` writes <direction>/tokens.css from this file;
// `node check-contrast.mjs` verifies every text/UI pair against WCAG 2.1 AA.
//
// Token contract (same names in every direction so a pick maps 1:1 onto the
// app's semantic Tailwind tokens):
//   page, surface, surface2, raised     backgrounds, lowest to highest
//   line                                decorative hairline (no contrast floor)
//   control                             input/button boundary (>= 3:1, WCAG 1.4.11)
//   ink, ink2                           primary / secondary text (>= 4.5:1)
//   accent, onAccent, accentText        brand fill, text on it, brand as text
//   focus                               focus ring (>= 3:1 vs surfaces)
//   allow / redact / monitor / block    verdict fills; *Ink = text on the fill;
//                                       *Text = verdict colour used as text
//   nav, navInk, navInk2                navigation surface and its text

export const directions = {
  checkpoint: {
    title: "Checkpoint",
    primary: "light",
    fonts: {
      ui: "'Overpass', system-ui, sans-serif",
      mono: "'Overpass Mono', ui-monospace, monospace",
      google: "family=Overpass:ital,wght@0,400;0,600;0,700;0,800;0,900;1,700&family=Overpass+Mono:wght@400;600",
    },
    themes: {
      light: {
        page: "#ECEEEA", surface: "#FFFFFF", surface2: "#F4F5F2", raised: "#FFFFFF",
        line: "#D5D9D3", control: "#7D8782",
        ink: "#1B2124", ink2: "#4A5458",
        accent: "#0B6A4C", onAccent: "#FFFFFF", accentText: "#0B6A4C",
        focus: "#1A56A0",
        allow: "#0B6A4C", allowInk: "#FFFFFF", allowText: "#0B6A4C",
        redact: "#1A56A0", redactInk: "#FFFFFF", redactText: "#1A56A0",
        monitor: "#F2B01E", monitorInk: "#1B2124", monitorText: "#7A5300",
        block: "#BF2A1D", blockInk: "#FFFFFF", blockText: "#BF2A1D",
        nav: "#1B2124", navInk: "#EEF1ED", navInk2: "#AEB7B2",
      },
      dark: {
        page: "#141A1C", surface: "#1C2427", surface2: "#232C30", raised: "#28333A",
        line: "#33403E", control: "#7E8C88",
        ink: "#EDF0EC", ink2: "#AEB8B4",
        accent: "#3DBE89", onAccent: "#0D1714", accentText: "#5ED3A2",
        focus: "#7AB2F5",
        allow: "#3DBE89", allowInk: "#0D1714", allowText: "#5ED3A2",
        redact: "#6EA8F0", redactInk: "#0D1714", redactText: "#8DBBF4",
        monitor: "#F2B01E", monitorInk: "#1B2124", monitorText: "#F5C24E",
        block: "#FF6F61", blockInk: "#1B0E0C", blockText: "#FF8B7F",
        nav: "#0F1416", navInk: "#EDF0EC", navInk2: "#A3ADA9",
      },
    },
  },

  nightShift: {
    title: "Night Shift",
    primary: "dark",
    fonts: {
      ui: "'Atkinson Hyperlegible Next', system-ui, sans-serif",
      mono: "'Atkinson Hyperlegible Mono', ui-monospace, monospace",
      google: "family=Atkinson+Hyperlegible+Next:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400&family=Atkinson+Hyperlegible+Mono:wght@400;500;600",
    },
    themes: {
      dark: {
        page: "#0E1A2B", surface: "#13233A", surface2: "#192C47", raised: "#203656",
        line: "#273D5E", control: "#5E7698",
        ink: "#EAF0F8", ink2: "#A9B8CE",
        accent: "#7CC4FF", onAccent: "#0B1B2E", accentText: "#8FCDFF",
        focus: "#9AD3FF",
        allow: "#52D6A6", allowInk: "#08241A", allowText: "#62DDB0",
        redact: "#BBA3FF", redactInk: "#1E1240", redactText: "#C5B1FF",
        monitor: "#F6C85F", monitorInk: "#2A1F05", monitorText: "#F6C85F",
        block: "#FF8173", blockInk: "#2E0B07", blockText: "#FF9488",
        nav: "#0B1524", navInk: "#EAF0F8", navInk2: "#9FB0C8",
      },
      light: {
        page: "#F2F5F9", surface: "#FFFFFF", surface2: "#E9EEF5", raised: "#FFFFFF",
        line: "#D3DCE7", control: "#7A8AA1",
        ink: "#11223A", ink2: "#46566E",
        accent: "#1460B8", onAccent: "#FFFFFF", accentText: "#1460B8",
        focus: "#1460B8",
        allow: "#0C7A55", allowInk: "#FFFFFF", allowText: "#0A6E4C",
        redact: "#6A4BD1", redactInk: "#FFFFFF", redactText: "#5B3DC2",
        monitor: "#F2BE45", monitorInk: "#2A1F05", monitorText: "#805400",
        block: "#C0362B", blockInk: "#FFFFFF", blockText: "#B3302A",
        nav: "#11223A", navInk: "#EAF0F8", navInk2: "#A9B8CE",
      },
    },
  },

  redline: {
    title: "Redline",
    primary: "light",
    fonts: {
      ui: "'Public Sans', system-ui, sans-serif",
      display: "'Spectral', Georgia, serif",
      mono: "'JetBrains Mono', ui-monospace, monospace",
      google: "family=Spectral:ital,wght@0,400;0,500;0,600;1,400&family=Public+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500",
    },
    themes: {
      light: {
        page: "#E8ECF1", surface: "#FFFFFF", surface2: "#F4F6F9", raised: "#FFFFFF",
        line: "#D6DCE4", control: "#7C8799",
        ink: "#161D2B", ink2: "#4A5467",
        accent: "#2443C4", onAccent: "#FFFFFF", accentText: "#2443C4",
        focus: "#2443C4",
        allow: "#16704F", allowInk: "#FFFFFF", allowText: "#16704F",
        redact: "#161D2B", redactInk: "#FFFFFF", redactText: "#161D2B",
        monitor: "#FFE27A", monitorInk: "#161D2B", monitorText: "#6E5600",
        block: "#B3261E", blockInk: "#FFFFFF", blockText: "#B3261E",
        nav: "#FFFFFF", navInk: "#161D2B", navInk2: "#4A5467",
      },
      dark: {
        page: "#11161F", surface: "#19202D", surface2: "#20293A", raised: "#263043",
        line: "#323D52", control: "#76839B",
        ink: "#E8ECF3", ink2: "#AAB4C5",
        accent: "#93A9FF", onAccent: "#0F1530", accentText: "#A3B6FF",
        focus: "#A3B6FF",
        allow: "#5DD3A0", allowInk: "#0B1E16", allowText: "#6BDAA9",
        redact: "#E8ECF3", redactInk: "#11161F", redactText: "#E8ECF3",
        monitor: "#4F4416", monitorInk: "#FFF1B8", monitorText: "#F2D36B",
        block: "#FF8A80", blockInk: "#2A0B08", blockText: "#FF9C93",
        nav: "#19202D", navInk: "#E8ECF3", navInk2: "#AAB4C5",
      },
    },
  },
};

// [foreground, background, minimum ratio, what the pair is used for]
export const pairs = [
  ["ink", "page", 4.5, "body text on page"],
  ["ink", "surface", 4.5, "body text on surface"],
  ["ink", "surface2", 4.5, "body text on inset surface"],
  ["ink", "raised", 4.5, "body text on raised surface"],
  ["ink2", "page", 4.5, "secondary text on page"],
  ["ink2", "surface", 4.5, "secondary text on surface"],
  ["ink2", "surface2", 4.5, "secondary text on inset surface"],
  ["accentText", "surface", 4.5, "link / brand text on surface"],
  ["accentText", "page", 4.5, "link / brand text on page"],
  ["onAccent", "accent", 4.5, "primary button label"],
  ["control", "surface", 3, "input / button boundary (1.4.11)"],
  ["focus", "surface", 3, "focus ring on surface (1.4.11)"],
  ["focus", "page", 3, "focus ring on page (1.4.11)"],
  ["allowInk", "allow", 4.5, "label on allow fill"],
  ["redactInk", "redact", 4.5, "label on redact fill"],
  ["monitorInk", "monitor", 4.5, "label on monitor fill"],
  ["blockInk", "block", 4.5, "label on block fill"],
  ["allowText", "surface", 4.5, "allow as text"],
  ["redactText", "surface", 4.5, "redact as text"],
  ["monitorText", "surface", 4.5, "monitor as text"],
  ["blockText", "surface", 4.5, "block as text"],
  ["navInk", "nav", 4.5, "navigation text"],
  ["navInk2", "nav", 4.5, "navigation secondary text"],
];
