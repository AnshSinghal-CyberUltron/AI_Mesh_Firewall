# F6: three visual directions for the console and the public landing page

Status: **for the owner to pick. No redesign build starts until one is chosen** (owner decision, 11 Nov).
Audit of the current UI: [AUDIT.md](AUDIT.md). Mockup source: [`directions/mockups/`](directions/mockups/).

Each direction renders the **same three screens with the same content**, so the choice is about the
visual system only:

1. Public landing page (hero and first sections)
2. Console overview (the dashboard an operator opens first)
3. Policy and rules editor

Every screen was captured at desktop 1440x900 and mobile 390x844 (deviceScaleFactor 2), in the
direction's primary theme. The console overview is also captured in the other theme, because both
themes are production (PRODUCT.md, principle 5).

## What is the same in all three

- **Content and data.** One shared file, [`shared/sample-data.js`](directions/mockups/shared/sample-data.js),
  whose fields mirror the real control API (`soc-kpis`, `module-kpis`, `policies`, `policies/{id}/rules`,
  `owasp-stats`). Every console screen carries a visible "Sample data" marker, and the landing
  vignettes say "Example". No customer names, logos, certifications or performance claims are shown.
- **Proposed navigation.** Overview, Audit log, then the seven checkpoints keeping their module IDs
  (1.1 Gateway and keys, 1.2 Policies, 1.3 RAG and vectors, 1.4 MCP tools, 1.5 Model routing,
  1.6 Kill switch, 1.7 Output guardrails), then Tools (Attack simulator, Connections). "Inputs" is
  renamed "Connections" because that page holds model and database connections (audit finding A-08).
  This is a proposal to confirm; see the questions at the end.
- **Verdict vocabulary.** Four verdicts, always shown as a word plus a colour (never colour alone):
  allow, redact, monitor (shown as Watched / Flagged), block.
- **Token contract.** Each direction defines the same token names in
  [`tokens.mjs`](directions/mockups/tokens.mjs), so the pick maps 1:1 onto the app's existing semantic
  Tailwind tokens and the `components/ui/*` primitives keep their API (DESIGN.md contract):

  | Direction token | App token (`frontend/src/index.css`) |
  |---|---|
  | `page` | `--background` |
  | `surface`, `surface2`, `raised` | `--card` / `--surface-1`, `--surface-2` / `--muted`, `--popover` |
  | `ink`, `ink2` | `--foreground`, `--muted-foreground` |
  | `accent`, `onAccent` | `--primary`, `--primary-foreground` |
  | `control`, `line` | `--input`, `--border` |
  | `focus` | `--ring` |
  | `allow`, `monitor`, `block` (+ `*Ink`, `*Text`) | `--success`, `--warning`, `--destructive` (+ foregrounds) |
  | `redact` (+ `redactInk`, `redactText`) | new: `--redact` (the product's most common verdict has no token today) |
  | `nav`, `navInk`, `navInk2` | new: `--sidebar`, `--sidebar-foreground`, `--sidebar-muted` |

- **Accessibility, verified rather than asserted.**
  - [`check-contrast.mjs`](directions/mockups/check-contrast.mjs) checks 23 text and UI pairs per theme:
    body and secondary text on every surface at 4.5:1 or better, control borders and focus rings at 3:1
    or better (WCAG 1.4.11), labels on every verdict fill, verdict colours used as text. That is
    **138 pairs across 3 directions x 2 themes, 0 failures**
    ([contrast-report.md](directions/mockups/contrast-report.md)).
  - axe-core 4.10.3 (rules `wcag2a`, `wcag2aa`, `wcag21aa`, `best-practice`) on every mockup page, both
    themes, desktop and mobile: **36 of 36 page variants report no violations**.
  - Charts use the `*Text` verdict colours, which clear 3:1 against the surface; the lighter fills are
    kept for labels and plates.
  - Every page has a `prefers-reduced-motion` fallback and a visible `:focus-visible` ring.
  - No horizontal scroll at 390px on any page (checked by `shoot.mjs`).

## Direction 1: Checkpoint

**Pitch: AI traffic is traffic. A wayfinding system where every verdict is a road sign and nothing else is.**

Why it fits: buyers on the landing page grasp "every request stops at a checkpoint" in one look, and
operators get a route board that maps one-to-one onto the seven modules they already know by number.
Sign plates (with the inset keyline of a real road sign) are used only for verdicts and checkpoint IDs,
so colour always means a decision. It is the most distinctive and brandable of the three.

- **Type:** Overpass 400 to 900 (its design descends from US highway signage lettering) with Overpass Mono
  for IDs, keys and patterns. Scale 13 / 14 / 16 / 20 / 25 / 32 / 44 / 64 / 96.
- **Palette (light, primary):** asphalt ink `#1B2124`, page `#ECEEEA`, guide green `#0B6A4C` (brand and allow),
  info blue `#1A56A0` (redact), caution `#F2B01E` (monitor), stop `#BF2A1D` (block). Dark theme included.
- **Signature:** the road in the landing hero (three requests passing overhead gantries) and the route
  board on the overview.
- **Trade-offs:** the boldest identity, and the highest risk of feeling loud if overused. Rules for
  restraint are built in: plates only for verdicts and IDs, everything else flat.

| Landing (desktop) | Console overview (desktop) |
|---|---|
| ![Checkpoint landing desktop](directions/shots/checkpoint--landing--desktop.webp) | ![Checkpoint console desktop](directions/shots/checkpoint--console--desktop.webp) |
| **Policy editor (desktop)** | **Console overview, dark theme** |
| ![Checkpoint policy desktop](directions/shots/checkpoint--policy--desktop.webp) | ![Checkpoint console dark](directions/shots/checkpoint--console--desktop--dark.webp) |

Mobile: [landing](directions/shots/checkpoint--landing--mobile.webp),
[console](directions/shots/checkpoint--console--mobile.webp),
[policy](directions/shots/checkpoint--policy--mobile.webp).
Full-length pages: [landing](directions/shots/checkpoint--landing--desktop--full.webp),
[console](directions/shots/checkpoint--console--desktop--full.webp),
[policy](directions/shots/checkpoint--policy--desktop--full.webp).

## Direction 2: Night Shift

**Pitch: built for the person on call at 3 a.m. Dark by default, dense, legible first, every verdict traceable.**

Why it fits: PRODUCT.md describes operators deciding and acting on live data in a dim SOC, wanting dense
and trustworthy surfaces. Atkinson Hyperlegible (designed by the Braille Institute for low-vision readers)
keeps `I l 1` and `O 0` distinct, which matters for key prefixes, request IDs and regex patterns. The
overview leads with what changed since you last looked, the kill switch state and open incidents; the
policy editor reviews a draft as a diff against the live version and tests it before publishing.

- **Type:** Atkinson Hyperlegible Next with Atkinson Hyperlegible Mono. Scale 13 / 15 / 18 / 22 / 26 / 38 / 56.
- **Palette (dark, primary):** midnight page `#0E1A2B`, surfaces `#13233A` / `#192C47`, ink `#EAF0F8`,
  ion blue `#7CC4FF` (accent and focus), mint `#52D6A6` (allow), lavender `#BBA3FF` (redact),
  amber `#F6C85F` (monitor), coral `#FF8173` (block). Light theme included.
- **Signature:** the request trace (landing hero) and the 15-minute verdict heat strip (overview).
  Verdict pills carry an icon and a word.
- **Trade-offs:** the closest to today's console (dark slate, dense), so the cheapest migration and the
  least risk for operators; also the least "marketing" landing page of the three.

| Landing (desktop) | Console overview (desktop) |
|---|---|
| ![Night Shift landing desktop](directions/shots/night-shift--landing--desktop.webp) | ![Night Shift console desktop](directions/shots/night-shift--console--desktop.webp) |
| **Policy editor (desktop)** | **Console overview, light theme** |
| ![Night Shift policy desktop](directions/shots/night-shift--policy--desktop.webp) | ![Night Shift console light](directions/shots/night-shift--console--desktop--light.webp) |

Mobile: [landing](directions/shots/night-shift--landing--mobile.webp),
[console](directions/shots/night-shift--console--mobile.webp) (bottom tab bar, kill switch first),
[policy](directions/shots/night-shift--policy--mobile.webp).
Full-length pages: [landing](directions/shots/night-shift--landing--desktop--full.webp),
[console](directions/shots/night-shift--console--desktop--full.webp),
[policy](directions/shots/night-shift--policy--desktop--full.webp).

## Direction 3: Redline

**Pitch: securing language is editing language. Prompts are marked up like a reviewed document, and policies read as numbered clauses.**

Why it fits: the firewall's actions are edits (redact, strike, flag), and the people who sign off on
it (compliance reviewers, AI-governance owners, buyers) read evidence. Redaction bars, strike-outs
and highlighter marks are the verdicts; the reviewer's margin note carries the rule that fired. The
overview opens with a short written brief bound to real numbers; the policy editor renders each rule
as a clause in priority order (numbering is honest here, because order decides the outcome), with
tracked changes before publishing.

- **Type:** Spectral (a screen-first serif designed for document reading) for headings and prose,
  Public Sans for interface text, JetBrains Mono for code. Scale 13 / 14 / 16 / 18 / 22 / 28 / 40 / 64.
- **Palette (light, primary):** desk `#E8ECF1`, sheet `#FFFFFF`, ink `#161D2B`, blue pencil `#2443C4`
  (accent), redaction bar `#161D2B`, strike red `#B3261E` (block), highlighter `#FFE27A` (monitor),
  green `#16704F` (allow). Dark theme included.
- **Signature:** the marked-up prompt with margin notes (landing hero), the morning brief (overview), the
  clause editor with tracked changes.
- **Trade-offs:** the strongest story for compliance buyers and auditors, and the calmest to read; the
  least dense, so operators see fewer numbers per screen than in Night Shift.

| Landing (desktop) | Console overview (desktop) |
|---|---|
| ![Redline landing desktop](directions/shots/redline--landing--desktop.webp) | ![Redline console desktop](directions/shots/redline--console--desktop.webp) |
| **Policy editor (desktop)** | **Console overview, dark theme** |
| ![Redline policy desktop](directions/shots/redline--policy--desktop.webp) | ![Redline console dark](directions/shots/redline--console--desktop--dark.webp) |

Mobile: [landing](directions/shots/redline--landing--mobile.webp),
[console](directions/shots/redline--console--mobile.webp),
[policy](directions/shots/redline--policy--mobile.webp).
Full-length pages: [landing](directions/shots/redline--landing--desktop--full.webp),
[console](directions/shots/redline--console--desktop--full.webp),
[policy](directions/shots/redline--policy--desktop--full.webp).

## Side by side

| | Checkpoint | Night Shift | Redline |
|---|---|---|---|
| Default theme | Light | Dark | Light |
| Density | Medium | High | Low to medium |
| Landing personality | Bold, memorable | Technical, calm | Editorial, evidential |
| Strongest for | Buyers, brand recognition | On-call operators, SREs | Compliance reviewers, auditors |
| Distance from today's UI | Far | Near | Far |
| Main risk | Loud if plates spread beyond verdicts | Reads as "another dark console" on the landing page | Serif-led console may feel slow to power users |

**Design lead's recommendation (the owner decides):** Night Shift for the console, because it matches the
users and principles in PRODUCT.md most directly and migrates with the least risk. If the landing page
needs a stronger identity than Night Shift gives it, choose Checkpoint for both surfaces rather than
mixing two systems; its route board also works for operators.

## What happens after the pick (not started)

1. Tokens and primitives: write the chosen tokens into `frontend/src/index.css`, restyle `components/ui/*`
   (styles only, API unchanged), add `--redact` and sidebar tokens.
2. Shell and navigation: new sidebar and top bar, real URLs for every view (fixes A-02, A-03), a working
   search (A-04).
3. Page-by-page migration from raw palette classes to tokens (audit A-05 counts 9,651 raw palette
   classes), with screenshots per page for the owner.
4. Landing page as a public route or separate build (question 3 below).

Each step is its own PR with before/after screenshots.

## How to reproduce

```bash
cd docs/plans/evidence/2026-11-11-f6-frontend/directions/mockups
node build-tokens.mjs        # tokens.mjs -> */tokens.css
node check-contrast.mjs      # WCAG pairs, writes contrast-report.md, exit 1 on any failure
node shoot.mjs /tmp/shots    # Playwright screenshots (needs `playwright` resolvable)
```

The pages are plain HTML and CSS with a little inline JavaScript to render the sample data; open any
`*/landing.html`, `*/console.html` or `*/policy.html` directly in a browser. Add `?theme=dark` or
`?theme=light` to switch theme. Fonts load from Google Fonts.

## Questions for the owner

1. **Which direction?** Or which parts to combine (for example Night Shift console with the Checkpoint landing).
2. **Product name on the landing page.** The repo uses "ZeroShield", "AI Mesh Firewall", "ZeroShield AI Mesh
   Firewall" and "AI Security Platform" (sidebar). The mockups use "ZeroShield" with "AI Mesh Firewall"
   as the product line. Is that right, or should the landing page use the AISecShield name?
3. **Where does the landing page live?** A public route in this app (today `/` is the protected
   console), a separate static site, or a page on zeroshield.ai?
4. **Brand assets.** Is there an existing logo, wordmark or brand colour that must be kept? The mockups use
   placeholder marks drawn for each direction; none is a final logo.
5. **Calls to action and pricing.** Is the primary CTA "Book a demo"? Should there be a pricing page, and
   is pricing public?
6. **Claims we may make.** The landing copy avoids performance numbers, customer names and certifications.
   Which claims are approved (for example deployment model "runs as containers in your environment",
   supported providers, latency figures), and who signs off copy?
7. **Navigation.** Keep the 1.1 to 1.7 module numbering in the console (all three mockups keep it), or move
   to task names only?
8. **Default theme.** DESIGN.md says the console defaults to dark; the code defaults to light (audit A-09).
   Which should ship?
