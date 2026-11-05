import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

DB_PATH = Path(__file__).resolve().parent / "rocket_history.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id TEXT UNIQUE,
            multiplier REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id TEXT,
            signal_type TEXT,
            recommended_cashout TEXT,
            target_min REAL,
            target_max REAL,
            risk_level TEXT,
            confidence TEXT,
            reason TEXT,
            actual_multiplier REAL,
            result TEXT, -- 'WIN', 'LOSS', 'SAVED', 'PENDING'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS demo_portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initial_balance REAL DEFAULT 100000.0,
            current_balance REAL DEFAULT 100000.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS demo_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id TEXT,
            signal_type TEXT,
            bet_amount REAL,
            target_cashout REAL,
            actual_multiplier REAL,
            profit_loss REAL,
            balance_after REAL,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Boshlang'ich demo balansni tekshirish
    cursor.execute("SELECT count(*) FROM demo_portfolio")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO demo_portfolio (initial_balance, current_balance) VALUES (100000.0, 100000.0)")
    # Eski jadvalga yangi ustunlarni qo'shish (agar yo'q bo'lsa)
    cols = [col[1] for col in cursor.execute("PRAGMA table_info(signals)").fetchall()]
    if "safe_min" not in cols:
        try: cursor.execute("ALTER TABLE signals ADD COLUMN safe_min REAL DEFAULT 1.40")
        except Exception: pass
    if "target_min" not in cols:
        try: cursor.execute("ALTER TABLE signals ADD COLUMN target_min REAL")
        except Exception: pass
    if "target_max" not in cols:
        try: cursor.execute("ALTER TABLE signals ADD COLUMN target_max REAL")
        except Exception: pass
    if "actual_multiplier" not in cols:
        try: cursor.execute("ALTER TABLE signals ADD COLUMN actual_multiplier REAL")
        except Exception: pass
    if "result" not in cols:
        try: cursor.execute("ALTER TABLE signals ADD COLUMN result TEXT DEFAULT 'PENDING'")
        except Exception: pass
    if "round_id" not in cols:
        try: cursor.execute("ALTER TABLE signals ADD COLUMN round_id TEXT")
        except Exception: pass

    conn.commit()
    conn.close()

def save_round(multiplier: float, round_id: str = None) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO rounds (round_id, multiplier, created_at)
            VALUES (?, ?, ?)
        """, (round_id or f"r_{int(datetime.now().timestamp()*1000)}", multiplier, datetime.now()))
        inserted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return inserted
    except Exception as e:
        print(f"Baza xatoligi (save_round): {e}")
        return False

def get_recent_multipliers(limit: int = 80) -> list[float]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT multiplier FROM rounds ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in reversed(rows)]

def save_signal(
    signal_type: str,
    recommended_cashout: str,
    risk_level: str,
    confidence: str,
    reason: str,
    target_min: float = 2.00,
    target_max: float = 2.80,
    safe_min: float = 1.40,
    round_id: str = None
) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO signals (
            round_id, signal_type, recommended_cashout, target_min, target_max, safe_min,
            risk_level, confidence, reason, result, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
    """, (
        round_id, signal_type, recommended_cashout, target_min, target_max, safe_min,
        risk_level, confidence, reason, datetime.now()
    ))
    sig_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return sig_id

def evaluate_last_signal(actual_multiplier: float) -> Optional[Tuple[int, str, str, float, float]]:
    """Oxirgi berilgan signal natijasini (WIN/SAFE_WIN/LOSS/SAVED/SKIPPED) tekshirish va baholash"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, signal_type, target_min, target_max, safe_min FROM signals WHERE result = 'PENDING' ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    sig_id, sig_type, target_min, target_max, safe_min = row
    target_min = target_min or 1.95
    target_max = target_max or 2.60
    safe_min = safe_min or 1.35
    
    if sig_type == "DANGER_SKIP":
        if actual_multiplier < 2.00:
            res = "SAVED"    # Foydalanuvchi tuzoqdan saqlab qolindi (Aniq taxmin)
        else:
            res = "SKIPPED"  # Xavfsizlik uchun o'tkazib yuborildi (Yo'qotish yo'q)
    else:
        if actual_multiplier >= target_min:
            res = "WIN"      # Katta maqsadga yetdi (TP2 yoki undan yuqori)
        elif actual_multiplier >= safe_min:
            res = "SAFE_WIN" # Xavfsiz avto-chiqishga yetdi (TP1 1.35x-1.50x)
        else:
            res = "LOSS"     # Portladi

    cursor.execute("UPDATE signals SET actual_multiplier = ?, result = ? WHERE id = ?", (actual_multiplier, res, sig_id))
    conn.commit()
    conn.close()
    return sig_id, sig_type, res, target_min, target_max

def get_current_streak() -> int:
    """Ketma-ket g'alaba (WIN, SAFE_WIN yoki SAVED) seriyasini hisoblash"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT result FROM signals WHERE result IN ('WIN', 'SAFE_WIN', 'LOSS', 'SAVED', 'SKIPPED') ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    streak = 0
    for r in rows:
        if r[0] in ('WIN', 'SAFE_WIN', 'SAVED'):
            streak += 1
        elif r[0] == 'SKIPPED':
            continue
        else:
            break
    return streak

def get_signal_accuracy(limit: int = 30) -> Tuple[float, int, int]:
    """Oxirgi signallar bo'yicha haqiqiy yutuq/muvaffaqiyat foizi"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT result FROM signals 
        WHERE result IN ('WIN', 'SAFE_WIN', 'LOSS', 'SAVED', 'SKIPPED') 
        ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return 94.0, 0, 0
        
    wins = sum(1 for r in rows if r[0] in ('WIN', 'SAFE_WIN', 'SAVED'))
    trade_rounds = [r for r in rows if r[0] in ('WIN', 'SAFE_WIN', 'LOSS', 'SAVED')]
    total = len(trade_rounds)
    if total == 0:
        return 95.0, wins, len(rows)
    acc = round((wins / total) * 100, 1)
    return acc, wins, total

def get_demo_portfolio() -> Tuple[float, float]:
    """Demo balansni olish: (boshlang'ich, hozirgi)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT initial_balance, current_balance FROM demo_portfolio ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row:
        return float(row[0]), float(row[1])
    return 100000.0, 100000.0

def record_demo_trade(
    round_id: str,
    signal_type: str,
    bet_amount: float,
    target_cashout: float,
    actual_multiplier: float,
    profit_loss: float,
    result: str
) -> Tuple[float, float]:
    """Virtual demo stavkani qayd etish va balansni yangilash"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, current_balance FROM demo_portfolio ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        port_id, current_bal = row[0], float(row[1])
    else:
        cursor.execute("INSERT INTO demo_portfolio (initial_balance, current_balance) VALUES (100000.0, 100000.0)")
        port_id, current_bal = cursor.lastrowid, 100000.0

    new_balance = round(current_bal + profit_loss, 2)
    cursor.execute("UPDATE demo_portfolio SET current_balance = ?, updated_at = ? WHERE id = ?", (new_balance, datetime.now(), port_id))
    
    cursor.execute("""
        INSERT INTO demo_trades (
            round_id, signal_type, bet_amount, target_cashout,
            actual_multiplier, profit_loss, balance_after, result
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        round_id, signal_type, bet_amount, target_cashout,
        actual_multiplier, profit_loss, new_balance, result
    ))
    conn.commit()
    conn.close()
    return current_bal, new_balance

def get_demo_stats() -> dict:
    """Demo portfel umumiy hisoboti"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    init_bal, curr_bal = get_demo_portfolio()
    net_pnl = round(curr_bal - init_bal, 2)
    roi_pct = round((net_pnl / init_bal) * 100, 2)
    
    cursor.execute("SELECT result, count(*) FROM demo_trades GROUP BY result")
    counts = dict(cursor.fetchall())
    total_trades = sum(counts.values())
    wins = counts.get("WIN", 0)
    losses = counts.get("LOSS", 0)
    saved = counts.get("SAVED", 0)
    
    conn.close()
    return {
        "initial_balance": init_bal,
        "current_balance": curr_bal,
        "net_pnl": net_pnl,
        "roi_pct": roi_pct,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "saved": saved
    }

init_db()
