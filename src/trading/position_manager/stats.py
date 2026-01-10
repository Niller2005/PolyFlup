"""Position statistics and reporting"""

from src.data.db_connection import db_connection
from src.utils.logger import log

def get_exit_plan_stats():
    """Get statistics on exit plan performance"""
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT 
                    COUNT(CASE WHEN final_outcome = 'EXIT_PLAN_FILLED' THEN 1 END) as exit_plan_successes,
                    COUNT(CASE WHEN final_outcome = 'RESOLVED' AND exited_early = 0 THEN 1 END) as natural_settlements,
                    COUNT(CASE WHEN final_outcome = 'LIMIT_SELL_099' THEN 1 END) as legacy_limit_sells,
                    AVG(CASE WHEN final_outcome = 'EXIT_PLAN_FILLED' THEN roi_pct END) as avg_exit_plan_roi,
                    AVG(CASE WHEN final_outcome = 'RESOLVED' AND exited_early = 0 THEN roi_pct END) as avg_natural_roi
                FROM trades 
                WHERE settled = 1 
                AND datetime(timestamp) >= datetime('now', '-7 days')
            """)
            stats = c.fetchone()
            if stats and any(stats):
                (
                    exit_successes,
                    natural_settlements,
                    legacy_sells,
                    avg_exit_roi,
                    avg_natural_roi,
                ) = stats
                total_exits = exit_successes + natural_settlements + legacy_sells
                if total_exits > 0:
                    exit_rate = (exit_successes / total_exits) * 100
                    log(
                        f"📊 EXIT PLAN STATS (7d): {exit_successes} successful exits ({exit_rate:.1f}%), {natural_settlements} natural settlements | Avg ROI: Exit {avg_exit_roi or 0:.1f}%, Natural {avg_natural_roi or 0:.1f}%"
                    )
                    return {
                        "exit_plan_successes": exit_successes,
                        "natural_settlements": natural_settlements,
                        "legacy_limit_sells": legacy_sells,
                        "exit_success_rate": exit_rate,
                        "avg_exit_plan_roi": avg_exit_roi or 0,
                        "avg_natural_roi": avg_natural_roi or 0,
                    }
            return None
    except Exception as e:
        log(f"⚠️  Error getting exit plan stats: {e}")
        return None
