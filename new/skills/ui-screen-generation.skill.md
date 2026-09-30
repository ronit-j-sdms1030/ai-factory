# UI screen generation — Gate 3 JSX

Adapter between open-design craft (vendored under `skills/vendor/open-design/`)
and this factory's compile/conform gate. Screens are JSX components, not HTML
prototypes. Open-design HTML `<artifact>` contracts do not apply.

React only. No Figma, no screenshots-as-layout, no design-tool JSON.

Each requirement gets a **distinct** named theme and a composed app shell.
Do not stamp Stark Factory orange-on-black onto every product.

## Output contract

- JSX only. No markdown fence. Balanced braces and parentheses.
- Do not define `Page`, `Sidebar`, `Button`, `Field`, `Table`, `Card`, `Badge`,
  `Hero`, or `Image`. The host already declares each of those once. A second
  `function Sidebar` fails the preview (`Identifier 'Sidebar' has already been declared`).
  Use them as tags. Then define only the screen component named in the request.
- `Page` sets `data-theme` to one of: midnight, aurora, paper, grove, coral,
  ink, glacier, sand.
- The screen returns `( <Page data-theme="…"><Sidebar>…</Sidebar><div>…</div></Page> )`.
- Use only factory tokens: `var(--color-bg)`, `var(--color-sidebar)`,
  `var(--color-surface)`, `var(--color-text)`, `var(--color-muted)`,
  `var(--color-accent)`, `var(--color-line)`, `var(--space-md)`,
  `var(--font-sans)`.
- Never emit raw hex, RGB, or named CSS colours.
- Motion via `transition` / `animation` in style objects is allowed (no hex).

## Layout

- Ops/admin/list/form: left `Sidebar` (product name from the BRD, one nav
  `Button` per roster screen calling `navigate("ScreenName")`) + main column
  full remaining width. Screens connect through the sidebar and in-page
  buttons, like a real app — no top tab strip.
- Pick a **role** per screen from the BRD page line and vary composition:
  - auth — centred sign-in card; no data table
  - dashboard — KPI cards + short attention list
  - catalog — search row + results table
  - loan / checkout — split form + active loans
  - detail / profile — identity card, not a status grid
  - portal — quick actions + personal activity
  - import / export — file/period controls + mapping or history table
- Main: real fields/actions, concrete table headers when a table belongs,
  product microcopy from the BRD.
- Never stamp the same form+table shell onto every screen.

## Anti-slop

- No indigo/purple gradient heroes, emoji icons, or decorative blobs.
- No lorem or "Feature one". Empty state is a short labelled message.
- One `h1`. Support text muted. One accent CTA.
- Look expensive: spacing rhythm, surface cards, hover transitions.

## Accessibility

- One `h1` per screen.
- Labels on fields. Keyboard-reachable controls.
- Contrast from tokens.

## Open-design files

Craft files are judgment. Map any palette they suggest onto a **named theme
+ tokens**. Never paste their hex. Emit factory JSX, not HTML documents.
