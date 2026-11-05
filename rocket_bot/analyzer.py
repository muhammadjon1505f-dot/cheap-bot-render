import statistics
from dataclasses import dataclass
from typing import Optional
from database import get_recent_multipliers, save_signal

@dataclass
class Signal:
    signal_type: str        # 'SAFE_ENTRY', 'HIGH_POTENTIAL', 'DANGER_SKIP'
    target_multiplier: str  # masalan "1.25x - 1.35x"
    risk_level: str         # 'PAST (LOW)', 'O\'RTA (MEDIUM)', 'YUQORI (HIGH)'
    confidence_percent: int # masalan 85%
    reason: str             # Tushuntirish

class RocketAnalyzer:
    def __init__(self, low_threshold: float = 1.35, high_threshold: float = 3.0):
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold

    def analyze_round(self, current_multiplier: float) -> Optional[Signal]:
        history = get_recent_multipliers(limit=40)
        if len(history) < 5:
            return None  # Tahlil uchun kamida 5 ta raund kerak

        last_3 = history[-3:]
        last_5 = history[-5:]
        avg_last_10 = statistics.mean(history[-10:]) if len(history) >= 10 else statistics.mean(history)
        median_last_20 = statistics.median(history[-20:]) if len(history) >= 20 else statistics.median(history)

        # 1. STRATEGIYA: Ketma-ket qizil (past) seriya (Rebound / Tiklanish)
        # Agar ketma-ket 4 ta raund 1.30 dan past tushgan bo'lsa
        if len(last_5) >= 4 and all(x < 1.30 for x in history[-4:]):
            sig = Signal(
                signal_type="SAFE_ENTRY",
                target_multiplier="1.20x - 1.30x",
                risk_level="PAST (LOW)",
                confidence_percent=88,
                reason="Ketma-ket 4 ta past koeffitsiyent tushdi. Ehtimollik bo'yicha tiklanish kutilmoqda."
            )
            save_signal(sig.signal_type, sig.target_multiplier, sig.risk_level, f"{sig.confidence_percent}%", sig.reason)
            return sig

        # 2. STRATEGIYA: O'rtacha tiklanish (3 ta ketma-ket < 1.40x)
        if len(last_3) == 3 and all(x < 1.40 for x in last_3):
            sig = Signal(
                signal_type="SAFE_ENTRY",
                target_multiplier="1.25x - 1.35x",
                risk_level="O'RTA (MEDIUM)",
                confidence_percent=80,
                reason="Oxirgi 3 raund 1.40x dan past yopildi. Xavfsiz oraliqda avto-chiqish tavsiya etiladi."
            )
            save_signal(sig.signal_type, sig.target_multiplier, sig.risk_level, f"{sig.confidence_percent}%", sig.reason)
            return sig

        # 3. STRATEGIYA: Katta koeffitsiyent kutilishi (Long Dry Spell)
        # Agar oxirgi 12 ta raund ichida biror marta ham 2.5x dan yuqori bo'lmagan bo'lsa
        if len(history) >= 12 and all(x < 2.50 for x in history[-12:]):
            sig = Signal(
                signal_type="HIGH_POTENTIAL",
                target_multiplier="2.00x - 2.80x",
                risk_level="YUQORI (HIGH)",
                confidence_percent=72,
                reason="Oxirgi 12 raundda 2.5x dan yuqori natija bo'lmadi. Yuqori multiplikator kutilmoqda."
            )
            save_signal(sig.signal_type, sig.target_multiplier, sig.risk_level, f"{sig.confidence_percent}%", sig.reason)
            return sig

        # 4. STRATEGIYA: Xavfli zona (ketma-ket juda katta sakrashlardan keyin)
        if current_multiplier > 15.0 or (len(last_3) >= 2 and all(x > 5.0 for x in history[-2:])):
            sig = Signal(
                signal_type="DANGER_SKIP",
                target_multiplier="O'TKAZIB YUBORISH (SKIP)",
                risk_level="JUDA YUQORI (DANGER)",
                confidence_percent=92,
                reason=f"Katta koeffitsiyent ({current_multiplier}x) dan so'ng tizim odatda darhol past koeffitsiyent beradi. Tikmaslik tavsiya qilinadi."
            )
            save_signal(sig.signal_type, sig.target_multiplier, sig.risk_level, f"{sig.confidence_percent}%", sig.reason)
            return sig

        return None
