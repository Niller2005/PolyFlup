# PolyFlup Dashboard

Real-time trading dashboard for the PolyFlup bot built with Svelte 5, Vite, and Tailwind CSS.

## Features

- **Live Statistics**: Real-time P&L, ROI, and win rate
- **Position Monitoring**: Active positions with current prices and unrealized P&L
- **Trade History**: Complete trade log with filtering and sorting
- **Performance Charts**: Visual performance tracking over time
- **WebSocket Updates**: Near-instant updates via shared SQLite database

## Tech Stack

- **Frontend**: Svelte 5 + Vite
- **Styling**: Tailwind CSS 4
- **Charts**: LayerChart + D3
- **Backend**: Express.js API server
- **Database**: Better-SQLite3 (read-only access to `trades.db`)

## Installation

```bash
cd ui
npm install
```

## Development

```bash
# Start both API server and dev server
npm run dev

# Or start individually
node server.js    # API server on port 3001
npm run build     # Build for production
```

The dashboard will be available at [http://localhost:5173](http://localhost:5173) (dev) or [http://localhost:3001](http://localhost:3001) (production).

## API Endpoints

The Express server (`server.js`) provides:

- `GET /api/stats` - Overall statistics (total P&L, ROI, win rate)
- `GET /api/positions` - Active open positions
- `GET /api/trades` - Complete trade history
- `GET /api/performance` - Daily performance data for charts

## Docker Deployment

The dashboard is included in the Docker Compose setup:

```bash
# From project root
docker compose up -d

# Dashboard available at http://localhost:3001
```

## Configuration

The dashboard automatically reads from the `trades.db` SQLite database. No additional configuration required.

### Environment Variables

- `NODE_ENV` - Set to `production` in Docker
- `DB_PATH` - Path to trades.db (default: `../trades.db` in dev, `/trades.db` in Docker)

## Project Structure

```
ui/
├── server.js              # Express API server
├── src/
│   ├── App.svelte         # Main app component
│   ├── lib/               # Reusable components
│   └── main.js            # Entry point
├── public/                # Static assets
├── package.json
└── vite.config.js
```

## Development Notes

- **Database Access**: Read-only access to prevent corruption
- **Real-Time Updates**: Poll API every 5 seconds for live data
- **Responsive Design**: Mobile-friendly layout with Tailwind
- **Type Safety**: JavaScript with JSDoc type hints

## Troubleshooting

### Dashboard shows no data
- Ensure `trades.db` exists in the parent directory
- Check that the bot has created at least one trade
- Verify server.js is running on port 3001

### API not responding
- Check if port 3001 is available
- Look for errors in server.js console output
- Ensure database file has read permissions

## Contributing

When adding new features:
1. Follow Svelte 5 runes syntax (`$state`, `$derived`, `$effect`)
2. Use Tailwind utility classes for styling
3. Add new API endpoints to `server.js` as needed
4. Test both dev and production builds
