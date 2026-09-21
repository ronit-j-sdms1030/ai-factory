# Design system — this client

Tokens, components and accessibility rules. Screens that invent hex colours,
ad-hoc fonts or unnamed controls fail the Gate 3 conform check and are
regenerated. Nobody ships someone else's design system.

## Tokens

- `--color-bg`: page background
- `--color-text`: primary text
- `--color-accent`: primary action
- `--space-md`: default spacing
- `--font-sans`: UI typeface

## Components

- `Page` — landmark main with a heading
- `Button` — primary action; never a raw `<button>` with inline colour
- `Field` — labelled input
- `Table` — tabular data

## Accessibility

- One `h1` per screen
- Form controls have a visible label
- Interactive elements are reachable by keyboard
- Contrast uses the tokens above, not ad-hoc greys
