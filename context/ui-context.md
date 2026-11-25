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
button, card, dropdown-menu, input, label, scroll-area, select, separator, sheet, skeleton, sonner, table, tabs, textarea, tooltip).
The shadcn CLI once imported `cn` from an npm package of that name: always check the import is `@/lib/utils`. Add new ones with
the shadcn CLI; do not edit them by hand. App-level pieces: `app-shell.tsx`, `call-record.tsx`, `labels.tsx`, `answer-details.tsx`.

## Pages

`/` console (text and voice test), `/kb` knowledge base, `/calls` recorded calls and reviews, `/desk` human agent desk, `/fixes` the fix list from post-call reviews, `/journeys` the journey catalogue editor (sentences, languages, prompts, settings, releases), `/bench` speed and quality of the running setup against a benchmark.

## Layout rules

- No horizontal page scroll at 390, 768, 1024 or 1440 px. Flex children that hold text get `min-w-0`; action rows wrap.
- Tables sit in an `overflow-x-auto` container with the first column kept visible.
- Transcripts and chat bubbles use `break-words` / `[overflow-wrap:anywhere]` (long links and ids wrap).
- The top navigation is one row from 1280 px (`xl`); below that, the page name and a menu button that opens a left `sheet`.
- Full-height app pages (console, desk, calls) use `100dvh` (mobile browser bars), and only from `lg` where columns sit side by
  side; on phones the page scrolls normally. The console's side cards move into a right `sheet` ("Details") under `lg`.
- Chat composer: auto-growing textarea, Enter sends, Shift+Enter is a new line; what waits on the caller (keypad code,
  transfer, voice note) sits right above it.
- Use skeletons for loading, `sonner` toasts for results, `alert-dialog` before anything destructive.
