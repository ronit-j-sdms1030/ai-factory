# Design system — this client

Tokens, components and accessibility rules. Screens that invent hex colours,
ad-hoc fonts or unnamed controls fail the Gate 3 conform check and are
regenerated. Nobody ships someone else's design system.

Every requirement picks **its own named theme**. Do not clone this factory's
orange-on-black chrome onto every product. React JSX only — no Figma.

## Themes (pick one per requirement)

Set `data-theme` on `Page`. Preview maps it onto the tokens. No hex in JSX.

- `midnight` — dark industrial, orange
- `aurora` — navy, electric teal
- `paper` — warm cream, terracotta
- `grove` — forest, lime
- `coral` — charcoal, live coral
- `ink` — near-black, gold
- `glacier` — ice, steel blue
- `sand` — stone, rust (hospitality)

Choose from the BRD: booking/stay → sand; clinical → glacier; finance → ink;
retail → coral; media → aurora; editorial → paper; ops tools → midnight;
nature/food → grove. Different requirements must not share a look by default.

## Tokens

- `--color-bg` — app canvas
- `--color-sidebar` — left rail
- `--color-surface` — cards / tables
- `--color-text` — primary ink
- `--color-muted` — labels and support
- `--color-accent` — primary action
- `--color-line` — hairline borders
- `--space-md` — 8pt grid
- `--font-sans` — UI sans

## Components

- `Page` — full-viewport canvas; `data-theme` plus `Sidebar` then main column
- `Sidebar` — left rail: product name, then nav `Button`s for sibling screens
- `Button` — primary action in main; nav items inside `Sidebar`
- `Field` — labelled input
- `Table` — tabular data on `--color-surface`

## Navigation

Screens are one connected app, not separate pages. The host defines a global
`navigate("ScreenName")`. Every `Sidebar` lists every screen in the roster:
`<Button onClick={() => navigate("ScreenName")}>Short label</Button>`, with the
current screen marked by `--color-accent`. In-page actions that open another
screen (View, Edit, Back) call `navigate` too. Never invent nav items that are
not in the roster.

## Libraries (free and open source, already loaded by the host)

No bundler. Take them from the global, or write a normal `import` and the
factory rewrites it to the global. Nothing else is available.

- **Icons — Lucide** (`lucide-react`, ISC): `const { CalendarDays, Users } = LucideReact;`
  then `<CalendarDays size={18} strokeWidth={1.75} />`. Icons in nav items,
  stat cards and empty states. No emoji icons.
- **Charts — Recharts** (`recharts`, MIT): `const { ResponsiveContainer, BarChart, Bar,
  XAxis, YAxis, Tooltip } = Recharts;`. Wrap in `<div style={{height: 240}}>` +
  `ResponsiveContainer`. Colours are tokens: `fill="var(--color-accent)"`,
  axis `stroke="var(--color-muted)"`. Only when the BRD has numbers over time or
  per category.
- **Motion — Framer Motion** (`framer-motion`, MIT): `const { motion, AnimatePresence } = Motion;`
  `<motion.div initial={{opacity:0, y:8}} animate={{opacity:1, y:0}} transition={{duration:0.3}}>`.
  Enter fades, list stagger, hover lift. Keep it subtle.
- **Dates — Day.js** (`dayjs`, MIT): `dayjs().add(1, "day").format("ddd D MMM")`.
  Realistic dates in tables and pickers.

## Layout

- App shell: sidebar + main. Full canvas — not a 720px article.
- Main: kicker / `h1` / support → surface cards or fields → table → accent CTA
- CSS transitions and modest enter animations are wanted (hover lift, fade-in)
- Accent on the primary action and the active nav item

## Accessibility

- One `h1` per screen
- Form controls have a visible label
- Keyboard-reachable controls
- Contrast from the tokens, not ad-hoc greys
