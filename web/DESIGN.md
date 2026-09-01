# AeroPredict — Design System

> TFM demo — Predicción de retrasos de vuelos.
> Direction: **calm, trustworthy, data-focused** — Linear/Stripe-grade clean SaaS.
> NOT toy aeronautics branding. No "aviation blue + sky" clichés.

## 1. Design Principles

1. **Calm over loud.** Restrained neutral canvas; color is reserved for the delay
   outcome and semantic states. The interface recedes so the data leads.
2. **Trustworthy.** Precise, tabular numerals, consistent alignment, explicit
   labels. Every number has a unit and a source.
3. **Data-first.** Metrics are the hero. Generous whitespace around numbers,
   tight typographic control, no decorative noise.
4. **Accessible by default.** WCAG AA contrast, visible focus rings, semantic
   labels, keyboard navigable selects.
5. **One accent, one meaning.** The accent is used only for the primary action
   and interactive focus. Severity (green/amber/red) is reserved exclusively for
   the delay outcome.

## 2. Palette

Light scheme (primary — chosen for the TFM demo; reads best for the delay
visualization on a projector/screen).

| Token | Value | Usage |
|---|---|---|
| `--color-bg` | `#fafafa` | App background (near-white, cool) |
| `--color-surface` | `#ffffff` | Cards, panels, inputs |
| `--color-surface-muted` | `#f4f4f5` | Subtle fills, hover, code |
| `--color-border` | `#e4e4e7` | Hairlines, dividers |
| `--color-border-strong` | `#d4d4d8` | Focused/active borders |
| `--color-text` | `#3f3f46` | Body text |
| `--color-text-secondary` | `#71717a` | Labels, captions, hints |
| `--color-text-muted` | `#a1a1aa` | Disabled, placeholders |
| `--color-text-strong` | `#18181b` | Headings, key metrics |
| `--color-accent` | `#4f46e5` | Primary action, focus (indigo) |
| `--color-accent-hover` | `#4338ca` | Primary action hover |
| `--color-accent-soft` | `#eef2ff` | Accent tint backgrounds |
| `--color-accent-text` | `#ffffff` | Text on accent |
| `--color-success` | `#16a34a` | On-time (green) |
| `--color-success-soft` | `#f0fdf4` | On-time tint |
| `--color-warning` | `#d97706` | Moderate delay (amber) |
| `--color-warning-soft` | `#fffbeb` | Moderate tint |
| `--color-danger` | `#dc2626` | Severe delay (red) |
| `--color-danger-soft` | `#fef2f2` | Severe tint |
| `--color-info` | `#2563eb` | Neutral info accents |

Severity scale (delay outcome only):
- **On-time** (`< 15 min`): `--color-success`
- **Moderate** (`15–60 min`): `--color-warning`
- **Severe** (`> 60 min`): `--color-danger`

## 3. Typography

System font stack (no webfont dependency — robust build, fast load).

| Token | Value | Usage |
|---|---|---|
| `--font-sans` | `Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif` | UI text |
| `--font-mono` | `'SF Mono', ui-monospace, 'Cascadia Code', Consolas, monospace` | ICAO codes, model version, tabular metrics |

Type scale (rem, 1rem = 16px):

| Token | Size / Weight / Line-height | Usage |
|---|---|---|
| `--text-display` | `1.875rem / 700 / 1.2` | Page title |
| `--text-title` | `1.25rem / 600 / 1.3` | Section titles |
| `--text-subtitle` | `1rem / 600 / 1.4` | Card titles, labels |
| `--text-body` | `0.9375rem / 400 / 1.55` | Body, form text |
| `--text-small` | `0.8125rem / 400 / 1.45` | Captions, hints |
| `--text-metric` | `2.5rem / 700 / 1.1` | Primary delay metric |
| `--text-metric-sm` | `1.5rem / 600 / 1.2` | Secondary metrics (ETA) |
| `--text-label` | `0.75rem / 600 / 1.3` | Field labels, uppercase-ish |

**Tabular figures**: all numeric metrics use `font-variant-numeric: tabular-nums`
so digits align and don't jitter.

## 4. Spacing

4px base scale.

| Token | Value |
|---|---|
| `--space-1` | `0.25rem` |
| `--space-2` | `0.5rem` |
| `--space-3` | `0.75rem` |
| `--space-4` | `1rem` |
| `--space-5` | `1.5rem` |
| `--space-6` | `2rem` |
| `--space-7` | `3rem` |
| `--space-8` | `4rem` |

Layout: page max-width `--layout-max: 72rem` (1152px), centered. Form/results
grid: 1 column mobile, 2 columns ≥ 900px.

## 5. Radii

| Token | Value | Usage |
|---|---|---|
| `--radius-sm` | `0.375rem` | Small chips, badges |
| `--radius-md` | `0.5rem` | Inputs, buttons |
| `--radius-lg` | `0.75rem` | Cards, panels |
| `--radius-full` | `9999px` | Pills, status dots |

## 6. Shadow & Elevation

| Token | Value | Usage |
|---|---|---|
| `--shadow-sm` | `0 1px 2px rgba(24,24,27,0.05)` | Subtle card edge |
| `--shadow-md` | `0 1px 3px rgba(24,24,27,0.08), 0 4px 12px rgba(24,24,27,0.06)` | Raised cards, dropdowns |
| `--shadow-focus` | `0 0 0 3px rgba(79,70,229,0.25)` | Focus ring |

## 7. Component Tokens

### Button (primary)
- bg `--color-accent`, text `--color-accent-text`, radius `--radius-md`
- padding `0.625rem 1.25rem`, font `--text-body` weight 600
- hover bg `--color-accent-hover`; focus `--shadow-focus`
- disabled: opacity 0.5, cursor not-allowed

### Input / Select
- bg `--color-surface`, border `--color-border`, radius `--radius-md`
- padding `0.625rem 0.75rem`, font `--text-body`
- focus: border `--color-accent` + `--shadow-focus`
- label: `--text-label`, color `--color-text-secondary`, margin-bottom `--space-2`

### Card / Panel
- bg `--color-surface`, border `--color-border`, radius `--radius-lg`
- padding `--space-5` (mobile) / `--space-6` (desktop)
- shadow `--shadow-sm`

### StatusBadge
- pill (`--radius-full`), padding `0.25rem 0.625rem`, font `--text-small` weight 600
- **Demo**: bg `--color-warning-soft`, text `--color-warning`, dot `--color-warning`
- **Connected**: bg `--color-success-soft`, text `--color-success`, dot `--color-success`

### Severity (delay outcome)
- **On-time**: text `--color-success`, bg `--color-success-soft`
- **Moderate**: text `--color-warning`, bg `--color-warning-soft`
- **Severe**: text `--color-danger`, bg `--color-danger-soft`

### FactorList item
- icon in `--color-accent-soft` rounded square (`--radius-sm`), title `--text-subtitle`,
  description `--text-small` color `--color-text-secondary`

### Disruption badge
- bg `--color-danger-soft`, text `--color-danger`, radius `--radius-full`, weight 600

### Advanced section (collapsible)
- toggle button: `--text-small` weight 600, color `--color-text-secondary`, chevron icon
- content: bordered container `--color-border`, radius `--radius-md`

### Empty / Loading / Error states
- centered, `--space-6` padding, icon `--color-text-muted`, title `--text-subtitle`,
  message `--text-small` `--color-text-secondary`
