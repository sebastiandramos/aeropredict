# AeroPredict Web — WCAG AA Accessibility Improvements

## Summary
The web UI has been updated to meet WCAG 2.1 AA requirements for color contrast, keyboard navigation, focus management, form labeling, ARIA announcements and non-color information encoding.

## Changes
### Contrast
- `--color-warning` `#d97706` → `#b45309` for status demo and moderate severity
- `--color-text-secondary` `#71717a` → `#374151` for tabs and secondary text
- `--color-success` `#16a34a` → `#15803d` for on-time severity
- `--color-danger` `#dc2626` → `#b91c1c` for severe delay
- `--color-text-muted` `#a1a1aa` → `#71717a` for placeholders/disabled
- `.status-version` opacity fixed to 1

### Navigation & Focus
- Skip link to `#main-content`
- `tabIndex={-1}` added to `<main id="main-content">` for programmatic focus
- Focus-visible ring preserved with `--shadow-focus`
- `.advanced-toggle:focus-visible` added with box-shadow ring
- Touch targets increased to min 44px for nav tabs, auth tabs, advanced toggle, `.btn-primary` and `.btn-ghost`

### Forms & ARIA
- `aria-required` on origin, destination, airline, date and time inputs
- `aria-live="assertive"` form errors with `aria-describedby`
- Airport combobox with `role="combobox"`, `aria-expanded`, `aria-controls`, `aria-autocomplete`
- `aria-label` on logout button

### Dynamic Announcements
- `StatusBadge` uses `role="status" aria-live="polite"`
- Form errors use `aria-live="assertive"`

### Non-color Encoding
- Severity badges include explicit text `Puntual / Retraso moderado / Retraso severo`
- `aria-label` on severity and disruption badges

## Evidence
- Screenshots: `C:\Users\Lenovo\AppData\Local\Temp\screenshot-post-fix-*.png`
- Axe-core report: `.omo/evidence/task-final-accessibility-wcag-aa-fix-web.json`
- Commits on `feat/data-archicture`: 47f5dbd, e519769, bde78b6, 0d1b609, 0e9c4f4

## No Breaking Changes
No API contract changes, no new dependencies, no visual rebrand.
