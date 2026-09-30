# Design system — this client

Tokens, components and accessibility rules. Screens that invent random hex colours
outside Studio token overrides, ad-hoc fonts or unnamed controls fail the Gate 3
conform check and are regenerated. Nobody ships someone else's design system.

Every requirement picks **its own named theme**. Do not clone this factory's
orange-on-black chrome onto every product. React JSX only — no Figma.

Reviewers also get a **Design Studio**: theme swatches, colour pickers for the
seven tokens, photo/hero insets, button variants, and motion presets. Prefer
Studio or Ask over inventing one-off CSS.

## Themes (pick one per requirement)

Set `data-theme` on `Page`. Preview maps it onto the tokens. Prefer named
themes; Studio may override `--color-*` with hex on `Page` style only.

- `midnight` — dark industrial, orange
- `aurora` — navy, electric teal
- `paper` — warm cream, terracotta
- `grove` — forest, lime
- `coral` — charcoal, live coral
- `ink` — near-black, gold
- `glacier` — ice, steel blue
- `sand` — stone, rust (hospitality)
- `blush` — light pink + white (beauty / soft consumer)
- `noir` — pure black + white
- `ocean` — deep teal water
- `sunrise` — warm peach light
- `plum` — violet night

Choose from the BRD: booking/stay → sand; clinical → glacier; finance → ink;
retail → coral; media → aurora; editorial → paper; ops tools → midnight;
nature/food → grove; beauty/lifestyle → blush; luxury dark → noir;
marine/tech → ocean; warm consumer → sunrise; creative → plum.
Different requirements must not share a look by default.

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
- `Button` — primary action; `variant="solid|ghost|outline|soft"`; optional `motion`
- `Field` — labelled input
- `Table` — tabular data on `--color-surface`
- `Card` — elevated surface block (`.ds-card`)
- `Badge` — compact status chip (`.ds-badge`)
- `Hero` — full-bleed band for title + CTA (`.ds-hero`)
- `Image` — photo inset (`src`, `alt`, `caption`, `ratio`, `motion`); host supplies a
  placeholder when `src` is omitted

## Navigation

Screens are one connected app, not separate pages. The host defines a global
`navigate("ScreenName")`. Every `Sidebar` lists every screen in the roster:
`<Button onClick={() => navigate("ScreenName")}>Short label</Button>`, with the
current screen marked by bold + underline (never accent text on an accent
button). In-page actions that open another screen (View, Edit, Back) call
`navigate` too. Never invent nav items that are not in the roster.

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
  Enter fades, list stagger, hover lift. Keep it subtle. Host CSS also ships
  `.motion-rise`, `.motion-fade`, `.motion-slide`, `.motion-pulse`.
- **Dates — Day.js** (`dayjs`, MIT): `dayjs().add(1, "day").format("ddd D MMM")`.
  Realistic dates in tables and pickers.

## Layout

- App shell: sidebar + main. Full canvas — not a 720px article.
- Main: optional `Hero` / photo inset → `h1` / support → `Card`s or fields → table → accent CTA
- CSS transitions and modest enter animations are wanted (hover lift, fade-in)
- Accent on the primary action and the active nav item
- World-class polish: clear hierarchy, generous whitespace, one focal media, 1–2
  motion accents — not a wall of identical forms.

## Accessibility

- One `h1`. Contrast on accent buttons. Labels on every field. Focus visible.
- Decorative images need empty `alt=""`. Informative images need real `alt`.
