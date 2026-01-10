---
name: svelte-ui-standards
description: Coding standards and UI guidelines for the PolyFlup Svelte dashboard.
---

## Svelte & Frontend Standards

### Formatting (Biome)
- **Indentation**: 2 spaces (enforced by biome.json)
- **Style**: Space indent, recommended rules enabled
- Ignore CSS files from formatting

### File Structure
- Components in `ui/src/lib/components/`
- UI components in `ui/src/lib/components/ui/`
- Each component exports from `index.ts`
- Stores in `ui/src/lib/stores/`

### Naming
- **Components**: `PascalCase.svelte` (e.g., `App.svelte`)
- **Utilities**: `camelCase.js` (e.g., `theme.js`)
- **CSS classes**: Tailwind utility classes

### Svelte Patterns
- Use reactive declarations: `$: winRate = ...`
- Prefer `{#snippet}` for chart tooltips (Svelte 5 syntax)
- Use `onMount` for initialization and intervals
- Clean up intervals in return function
- Store pattern for global state (e.g., theme store)

### Component Library Stack
- **shadcn-svelte**: Accessible UI components with customizable styling
- **Lucide Icons**: Modern icon set (Activity, TrendingUp, Award, etc.)
- **LayerChart**: D3-based charting library (AreaChart, BarChart)
- **d3-scale**: Scaling functions for charts (scaleBand, etc.)

### Theme System
- Dark/Light mode toggle with persistent storage
- Uses CSS variables for theming (`var(--color-chart-1)`, etc.)
- Theme store pattern: `theme.init()`, `theme.toggle()`, `$theme`
- Tailwind dark mode classes: `dark:bg-slate-900`

### API Integration
- **Endpoint**: `http://${hostname}:3001/api/stats`
- **Polling**: Every 5 seconds for real-time updates
- **Error Handling**: Display error banner on connection failure
- **Loading States**: Show loading indicator while fetching initial data

### Data Visualization
- **Equity Curve**: AreaChart with cumulative PnL (last 50 trades)
- **Asset Breakdown**: BarChart with per-symbol PnL
- **Color Coding**: 
  - BTC: `rgb(255, 153, 0)` (orange)
  - ETH: `rgb(99, 127, 235)` (blue)
  - SOL: `rgb(153, 69, 255)` (purple)
  - XRP: `rgb(2, 140, 255)` (cyan)
- **Conditional Colors**: Green for profits, red for losses

### Table Display
- Recent trades table with live streaming data
- Columns: ID, Time (UTC), Market, Side, Edge, Price, PnL, Status
- Status badges: LIVE (amber), STOP LOSS (destructive), TAKE PROFIT (emerald), REVERSED (outline), SETTLED (secondary)
- Side badges with emojis: 📈 UP (default), 📉 DOWN (destructive)

### Stats Cards
- Grid layout: 1 col mobile, 2 cols tablet, 4 cols desktop
- Key metrics: Total PnL, Win Rate, Stop Losses, Take Profits
- Conditional colors: emerald-600 for positive, rose-600 for negative
- Uppercase tracking labels with icons
- Arrow indicators: ArrowUpRight (positive), ArrowDownRight (negative)
