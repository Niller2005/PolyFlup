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

### API Integration
- Fetch from `http://${hostname}:3001/api/stats`
- Poll every 5 seconds for real-time updates
- Handle loading and error states

### shadcn-svelte Usage
- Use the shadcn-svelte collection for accessible components.
- Customize look and feel using CSS Variables (Theming).
