# UI Context

## Theme

shadcn/ui "new-york" neutral theme. Colour carries meaning only: green = verified / fine, amber = attention, red = problem.
Everything else is greyscale. Light and dark follow the operating system (`prefers-color-scheme`).

## Colour tokens (`ui/app/globals.css`, OKLCH CSS variables; never hard-code colours)

| Role | Variable |
|---|---|
| Page background | `--page` / `--background` |
| Card / popover | `--card`, `--popover` |
| Primary / secondary / muted / accent | `--primary`, `--secondary`, `--muted`, `--accent` (+ `-foreground`) |
| Border / input / focus ring | `--border`, `--input`, `--ring` |
| Error | `--destructive` |
| Success / warning | `--success`, `--warning` |

## Radius

`--radius: 0.625rem`; use the shadcn radius classes (`rounded-md`, `rounded-lg`, `rounded-xl` for cards).

## Component library

shadcn/ui on Tailwind 4 and Radix, icons from `lucide-react`. Components live in `ui/components/ui/` (alert, alert-dialog, badge,
button, card, input, label, scroll-area, select, separator, skeleton, sonner, table, tabs, textarea, tooltip). Add new ones with
the shadcn CLI; do not edit them by hand. App-level pieces: `app-shell.tsx`, `call-record.tsx`, `labels.tsx`, `answer-details.tsx`.

## Pages

`/` console (text and voice test), `/kb` knowledge base, `/calls` recorded calls and reviews, `/desk` human agent desk, `/fixes` the fix list from post-call reviews.

## Layout rules

- No horizontal page scroll at 390, 768, 1024 or 1440 px. Flex children that hold text get `min-w-0`; action rows wrap.
- Tables sit in an `overflow-x-auto` container with the first column kept visible.
- Transcripts use `break-words`. The side navigation collapses under 1024 px.
- Use skeletons for loading, `sonner` toasts for results, `alert-dialog` before anything destructive.
