# Design

> Visual system for ZeroShield — AI Mesh Firewall. Captured from the live code
> (`frontend/src/index.css`, Tailwind v4 `@theme`, `ThemeContext`). Impeccable
> passes must preserve this identity; changes to `components/ui/*` and theme
> tokens are **styles/tokens only, API-compatible** (never props/structure/exports),
> because parallel branch sessions depend on the contract.

## Theme

Dual theme, first-class **dark and light**. Toggle is class-based: `.dark` on
`<html>`, driven by `ThemeContext` (`system` | `dark` | `light`, persisted to
`localStorage`, default resolves to dark). Tailwind v4 with
`@custom-variant dark (&:where(.dark, .dark *))`. Body carries layered radial
brand-tint gradients (sky/teal) over a slate base; `.zs-app-shell` adds subtle
button lift micro-interactions.

Physical scene: an operator in a dim SOC / on-call at night is the default (dark);
a compliance reviewer in a bright office is the light case. Both must be exact.

## Color

All colors are **OKLCH**. Primary is an indigo/violet (`hue ~270-275`). Semantic
tokens are consumed via Tailwind color utilities (`bg-card`, `text-muted-foreground`,
`border-border`, `ring-ring`, `bg-primary`, `text-destructive`, `bg-success`, …).

### Light (`:root`)

| Token | Value | Role |
|---|---|---|
| `--background` | `oklch(0.99 0 0)` | app bg (over gradient) |
| `--foreground` | `oklch(0.18 0.03 260)` | primary ink |
| `--surface-1` / `--surface-2` | `oklch(1 0 0)` / `oklch(0.975 0.005 250)` | raised surfaces |
| `--card` / `--card-foreground` | `oklch(1 0 0)` / `oklch(0.18 0.03 260)` | cards |
| `--muted` / `--muted-foreground` | `oklch(0.965 0.008 250)` / `oklch(0.5 0.03 255)` | muted bg / secondary text ⚠ |
| `--primary` / `--primary-foreground` | `oklch(0.55 0.22 270)` / `oklch(0.99 0.005 250)` | brand |
| `--accent` / `--accent-foreground` | `oklch(0.95 0.03 270)` / `oklch(0.3 0.12 270)` | accent |
| `--destructive` | `oklch(0.6 0.22 25)` | error/danger (red) |
| `--success` | `oklch(0.65 0.16 155)` | pass/healthy (green) |
| `--warning`/`--warn` | `oklch(0.78 0.16 75)` | caution (amber) |
| `--border` / `--input` / `--ring` | `0.92`/`0.93` neutrals / `--primary` | lines & focus |
| `--radius` | `0.5rem` | base radius (sm/md/lg/xl derived) |

### Dark (`.dark`)

| Token | Value | Role |
|---|---|---|
| `--background` / `--foreground` | `oklch(0.16 0.02 260)` / `oklch(0.97 0.005 250)` | slate bg / near-white ink |
| `--surface-1` / `--surface-2` | `oklch(0.2 0.025 260)` / `oklch(0.235 0.028 262)` | raised |
| `--card` / `--muted-foreground` | `oklch(0.2 0.025 260)` / `oklch(0.68 0.025 258)` | cards / secondary text |
| `--primary` / `--ring` | `oklch(0.7 0.2 275)` | brighter indigo for dark |
| `--border` / `--input` | `oklch(1 0 0 / 0.08)` / `oklch(1 0 0 / 0.1)` | hairlines |
| `--destructive`/`--success`/`--warn` | `0.65 0.22 25` / `0.72 0.17 158` / `0.82 0.16 78` | status (dark-tuned) |

Extras: `--shadow-elegant`, `--shadow-glow`, `--gradient-hero`, `--gradient-card-glow`,
`--shimmer(-bg)`, and a legacy `--mesh-*` set (ink/muted/border/panel/glow/shadow)
for the marketing-style enterprise page.

> ⚠ **Known contrast risk to verify (item 21):** light `--muted-foreground`
> (`oklch(0.5 0.03 255)`) on `--background`/`--card` is near the 4.5:1 floor for
> body text and likely fails for placeholder text. Audit before signing off.

## Typography

- **Body:** Plus Jakarta Sans → Inter fallback (`--font-sans` lists Inter first for utility classes; `body{}` sets Plus Jakarta Sans).
- **Mono:** JetBrains Mono (`--font-mono`) for keys, IDs, code, telemetry values.
- Loaded via Google Fonts `@import` at top of `index.css`.
- Pairing is one humanist-sans family in weights + a mono for data — no similar-sans clash.

## Components

shadcn-style primitives under `frontend/src/components/ui/`: Badge, Button, Card,
Dialog, EmptyState, Input, Label, PanelHeader, Progress, SegmentedControl, Select,
Skeleton, Slider, Spinner, Switch, Table, Tabs, Textarea, Toast, Tooltip. Built
with `class-variance-authority` + `clsx` + `tailwind-merge`; Radix used for
Progress/Slot. **These are the shared contract — restyle only, never change the API.**

Charts go through `SafeResponsiveChart` (a ResizeObserver-gated wrapper). Chart
libs in play: `echarts` + `echarts-for-react` (interactive dashboard charts),
`uplot` (dense time-series), and legacy `recharts` (being migrated out). See
FRONTEND_AUDIT.md → "Chart migration" for the wrapper-API nuance.

## Layout

App shell (`.zs-app-shell`) → `DashboardLayout` with a `Header` (theme toggle,
account) and a single protected route (`/`) that renders `DashboardApp`; panels
are reached via **in-app tab/section navigation**, not per-panel routes. Cards
and data tables are the workhorses; density is intentional. Responsive targets:
**1440 / 1024 / 768 / 375**.

## Motion

Subtle and functional: button hover `translateY(-1px)` + shadow, active
`scale(0.99)`, `fwShimmer` skeleton loading, `motion` (Framer) available for
richer transitions. All motion must have a `prefers-reduced-motion: reduce`
fallback (crossfade/instant).
