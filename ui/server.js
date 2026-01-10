import express from 'express';
import cors from 'cors';
import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dbPath = process.env.DB_PATH || path.join(__dirname, '..', 'trades.db');

const app = express();
const port = 3001;

app.use(cors());
app.use(express.json());

// Serve static files from the dist directory
app.use(express.static(path.join(__dirname, 'dist')));

app.get('/api/stats', (req, res) => {
    try {
        if (!fs.existsSync(dbPath)) {
            return res.json({
                summary: { total: 0, settled: 0, wins: 0, invested: 0, total_pnl: 0, avg_roi: 0 },
                per_symbol: [],
                recent_trades: [],
                pnl_history: []
            });
        }
        
        // Create a new connection for each request to avoid caching
        const db = new Database(dbPath, { readonly: true, fileMustExist: false });
        
        // Summary statistics
        const summary = db.prepare(`
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN settled = 1 THEN 1 ELSE 0 END) as settled,
                SUM(CASE WHEN settled = 1 AND pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                SUM(bet_usd) as invested,
                SUM(CASE WHEN settled = 1 THEN pnl_usd ELSE 0 END) as total_pnl,
                AVG(CASE WHEN settled = 1 THEN roi_pct ELSE NULL END) as avg_roi,
                SUM(CASE WHEN final_outcome = 'STOP_LOSS' THEN 1 ELSE 0 END) as stop_losses,
                SUM(CASE WHEN final_outcome = 'TAKE_PROFIT' THEN 1 ELSE 0 END) as take_profits,
                SUM(CASE WHEN final_outcome = 'STOP_LOSS' AND exited_early = 1 THEN 1 ELSE 0 END) as reversed
            FROM trades
        `).get();

        // Per symbol statistics
        const per_symbol = db.prepare(`
            SELECT symbol,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl_usd) as pnl,
                AVG(roi_pct) as avg_roi
            FROM trades
            WHERE settled = 1
            GROUP BY symbol
            ORDER BY pnl DESC
        `).all();

        // Recent trades
        const recent_trades = db.prepare(`
            SELECT id, timestamp, symbol, side, edge, entry_price,
                   pnl_usd, roi_pct, settled, order_status, final_outcome, exited_early
            FROM trades
            ORDER BY id DESC
            LIMIT 50
        `).all();

        // PnL history
        const pnl_history = db.prepare(`
            SELECT timestamp, pnl_usd
            FROM trades
            WHERE settled = 1
            ORDER BY timestamp ASC
        `).all();

        res.json({
            summary,
            per_symbol,
            recent_trades,
            pnl_history
        });
        
        // Close the connection after use
        db.close();
    } catch (error) {
        console.error(error);
        res.status(500).json({ error: error.message });
    }
});

// Serve the Svelte app for all non-API routes
app.use((req, res) => {
    res.sendFile(path.join(__dirname, 'dist', 'index.html'));
});

app.listen(port, () => {
    console.log(`Server running at http://localhost:${port}`);
});
