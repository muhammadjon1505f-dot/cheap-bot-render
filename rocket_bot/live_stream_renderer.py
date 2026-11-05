import math
import time
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont

class IOS26FrameRenderer:
    def __init__(self, width=1280, height=720):
        self.width = width
        self.height = height
        
        # DejaVu shriftlarini yuklash
        try:
            self.font_hero = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 72)
            self.font_xlarge = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 40)
            self.font_large = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 26)
            self.font_mid_bold = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 19)
            self.font_mid = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 17)
            self.font_small_bold = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 13)
            self.font_small = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 12)
            self.font_tiny = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 10)
        except Exception:
            self.font_hero = self.font_xlarge = self.font_large = self.font_mid_bold = self.font_mid = self.font_small_bold = self.font_small = self.font_tiny = ImageFont.load_default()

    def draw_glass_card(self, draw, box, fill=(16, 23, 36), outline=(32, 48, 72), radius=20, width=1):
        """Ultra-zamonaviy neomorfik kiber-shisha karta chizish"""
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

    def draw_pill(self, draw, box, fill=(24, 34, 52), outline=None, text="", text_color=(255, 255, 255), font=None):
        """Zamonaviy kapsula (status pill) nishonini chizish"""
        draw.rounded_rectangle(box, radius=12, fill=fill, outline=outline, width=1)
        if text and font:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            bx = box[0][0] + (box[1][0] - box[0][0] - tw) // 2
            by = box[0][1] + (box[1][1] - box[0][1] - th) // 2 - 1
            draw.text((bx, by), text, fill=text_color, font=font)

    def render_frame(self, state: dict) -> Image.Image:
        status = state.get("status", "waiting") # "waiting", "flying", "crashed"
        current_mult = state.get("current_mult", 1.00)
        crashed_mult = state.get("crashed_mult", 1.00)
        round_id = state.get("round_id", "---")
        history = state.get("history", [1.5, 2.3, 1.1, 4.5, 2.0, 1.2, 8.4, 2.1, 1.3, 3.2, 1.4, 2.8, 1.1, 5.2, 2.1, 1.8, 12.0, 1.4, 2.5, 3.0])
        signal = state.get("signal", {})
        stats = state.get("stats", {})

        # To'q kiber-kosmik fon (Deep Obsidian)
        img = Image.new("RGB", (self.width, self.height), color=(8, 11, 19))
        draw = ImageDraw.Draw(img)

        # Ambient kiber-nuqtalar
        for gx in range(40, self.width, 60):
            for gy in range(40, self.height, 60):
                draw.point((gx, gy), fill=(18, 25, 40))

        # ---------------- 1. DYNAMIC ISLAND / YUQORI PANEL (O'zbekcha) ----------------
        di_box = [(470, 14), (810, 52)]
        draw.rounded_rectangle(di_box, radius=19, fill=(18, 26, 44), outline=(0, 229, 255), width=1)
        
        pulse = int(time.time() * 2) % 2
        dot_col = (0, 245, 155) if pulse else (0, 180, 110)
        draw.ellipse([(490, 27), (502, 39)], fill=dot_col)
        draw.text((512, 23), "EPIC AI  •  24/7 JONLI EFIR", fill=(255, 255, 255), font=self.font_small_bold)

        # Chap brending
        draw.text((30, 20), "⚡️ EPIC ROCKET", fill=(0, 229, 255), font=self.font_large)
        self.draw_pill(draw, [(275, 22), (380, 50)], fill=(18, 28, 44), outline=(0, 229, 255), text="VIP PRO v3", text_color=(0, 229, 255), font=self.font_tiny)

        # O'ng tomondagi vaqt (Toshkent vaqti UTC+5)
        tashkent_time = datetime.now(timezone(timedelta(hours=5))).strftime("%H:%M:%S")
        time_text = f"Toshkent: {tashkent_time}"
        self.draw_pill(draw, [(1080, 18), (1250, 48)], fill=(16, 23, 36), outline=(35, 50, 75), text=time_text, text_color=(180, 205, 235), font=self.font_small_bold)

        # ---------------- 2. MARKAZIY PARVOZ MAYDONI (Hero Flight Card) ----------------
        hero_box = [(30, 70), (860, 340)]
        
        if status == "flying":
            card_fill = (10, 28, 38)
            card_border = (0, 245, 155)
            badge_text = "🚀 UCHMOQDA"
            badge_color = (0, 245, 155)
            badge_bg = (12, 48, 32)
            mult_str = f"{current_mult:.2f}x"
            mult_col = (0, 245, 155)
            profit = (current_mult - 1.0) * 100
            sub_badge = f"+{profit:.0f}% FOYDA"
            sub_col = (0, 245, 155)
        elif status == "crashed":
            card_fill = (28, 12, 18)
            card_border = (255, 59, 105)
            badge_text = "💥 YAKUNLANDI"
            badge_color = (255, 59, 105)
            badge_bg = (52, 16, 26)
            mult_str = f"{crashed_mult:.2f}x"
            mult_col = (255, 59, 105)
            sub_badge = "TO'XTADI"
            sub_col = (255, 80, 110)
        else: # waiting
            card_fill = (14, 22, 36)
            card_border = (0, 229, 255)
            badge_text = "⏳ KUTILMOQDA"
            badge_color = (0, 229, 255)
            badge_bg = (16, 40, 60)
            mult_str = "1.00x"
            mult_col = (220, 235, 255)
            sub_badge = "TAYYORGARLIK"
            sub_col = (0, 229, 255)

        self.draw_glass_card(draw, hero_box, fill=card_fill, outline=card_border, radius=24, width=2)

        # Raund va Holat nishonlari
        self.draw_pill(draw, [(50, 90), (220, 122)], fill=(20, 30, 48), text=f"RAUND #{round_id}", text_color=(170, 200, 235), font=self.font_small_bold)
        self.draw_pill(draw, [(670, 90), (840, 122)], fill=badge_bg, outline=badge_color, text=badge_text, text_color=badge_color, font=self.font_small_bold)

        # Katta Asosiy Multiplikator
        draw.text((60, 135), mult_str, fill=mult_col, font=self.font_hero)

        # Foyda nishoni
        self.draw_pill(draw, [(440, 150), (660, 204)], fill=card_fill, outline=sub_col, text=sub_badge, text_color=sub_col, font=self.font_large)

        # Tezkor ko'rsatkichlar (Uzbek)
        acc_val = stats.get("accuracy", 95)
        streak_val = stats.get("streak", 6)
        rsi_val = stats.get("rsi", 56.4)

        self.draw_pill(draw, [(50, 275), (195, 315)], fill=(18, 28, 44), text=f"🎯 ANIQ: {acc_val}%", text_color=(0, 245, 155), font=self.font_small_bold)
        self.draw_pill(draw, [(210, 275), (360, 315)], fill=(18, 28, 44), text=f"🔥 SERIYA: {streak_val} TA", text_color=(255, 204, 0), font=self.font_small_bold)
        self.draw_pill(draw, [(375, 275), (510, 315)], fill=(18, 28, 44), text=f"📊 RSI: {rsi_val:.1f}", text_color=(0, 229, 255), font=self.font_small_bold)
        self.draw_pill(draw, [(525, 275), (675, 315)], fill=(18, 28, 44), text="📥 KIRISH: 1.05x", text_color=(180, 205, 235), font=self.font_small_bold)
        self.draw_pill(draw, [(690, 275), (840, 315)], fill=(18, 28, 44), text="🛡 CHIQISH: TP1", text_color=(0, 245, 155), font=self.font_small_bold)

        # ---------------- 3. PASTKI GRAFIK: SO'NGGI 20 TA RAUND STATISTIKASI ----------------
        chart_box = [(30, 360), (860, 695)]
        self.draw_glass_card(draw, chart_box, fill=(14, 20, 32), outline=(28, 42, 65), radius=24, width=1)

        draw.text((50, 378), "📈 SO'NGGI 20 TA RAUND STATISTIKASI", fill=(210, 230, 255), font=self.font_mid_bold)
        self.draw_pill(draw, [(680, 374), (840, 404)], fill=(255, 204, 0, 40), outline=(255, 204, 0), text="NISHON: 2.00x", text_color=(255, 204, 0), font=self.font_tiny)

        chart_base_y = 650
        chart_max_h = 210
        chart_start_x = 55
        bar_w = 28
        bar_gap = 12

        # 2.0x Nishon chizig'i
        target_y = chart_base_y - int(math.log10(2.0) / math.log10(15.0) * chart_max_h)
        for gx in range(chart_start_x, chart_start_x + 20 * (bar_w + bar_gap), 8):
            draw.line([(gx, target_y), (gx + 4, target_y)], fill=(255, 204, 0), width=1)

        recent_20 = history[-20:] if len(history) >= 20 else history
        poly_points = []
        green_count = 0
        red_count = 0
        
        for i, val in enumerate(recent_20):
            bx = chart_start_x + i * (bar_w + bar_gap)
            log_v = max(math.log10(max(val, 1.0)), 0.04)
            log_max = math.log10(15.0)
            h = int((log_v / log_max) * chart_max_h)
            h = max(min(h, chart_max_h), 12)
            by = chart_base_y - h
            
            is_win = val >= 2.0
            is_huge = val >= 10.0
            
            if is_win:
                green_count += 1
            else:
                red_count += 1
            
            if is_huge:
                bar_fill = (255, 215, 0)
            elif is_win:
                bar_fill = (0, 245, 155)
            else:
                bar_fill = (255, 59, 105)
                
            draw.rounded_rectangle([(bx, by), (bx + bar_w, chart_base_y)], radius=6, fill=bar_fill)
            
            lbl = f"{val:.1f}x" if val < 100 else f"{int(val)}x"
            draw.text((bx - 1, by - 16), lbl, fill=(230, 240, 255) if is_win else (180, 180, 190), font=self.font_tiny)
            poly_points.append((bx + bar_w // 2, by))

        # Neon Cyan Trend Chizig'i
        if len(poly_points) > 1:
            draw.line(poly_points, fill=(0, 229, 255), width=2)
            for pt in poly_points:
                draw.ellipse([(pt[0] - 3, pt[1] - 3), (pt[0] + 3, pt[1] + 3)], fill=(0, 229, 255))

        # Pastki xulosa satri
        tot_cnt = len(recent_20) or 1
        green_pct = int((green_count / tot_cnt) * 100)
        avg_val = sum(recent_20) / tot_cnt
        max_val = max(recent_20) if recent_20 else 2.0
        strip_text = f"🟢 Yashil: {green_count} ({green_pct}%)  •  🔴 Qizil: {red_count}  •  ⚡️ O'rtacha: {avg_val:.2f}x  •  💎 Rekord: {max_val:.2f}x"
        draw.text((55, 665), strip_text, fill=(140, 165, 195), font=self.font_small)

        # ---------------- 4. O'NG PANEL: AI TRADING SIGNALLARI (Dock Widgets) ----------------
        dock_box = [(880, 70), (1250, 695)]
        self.draw_glass_card(draw, dock_box, fill=(14, 20, 32), outline=(0, 229, 255), radius=24, width=2)

        draw.text((905, 92), "🤖 AI TRADING SIGNALI", fill=(0, 229, 255), font=self.font_mid_bold)

        # 1-Vidjet: Buyruq Kartasi
        sig_type = signal.get("type", "HUNTER_2X")
        is_skip = "SKIP" in sig_type or "DANGER" in sig_type

        order_fill = (38, 14, 22) if is_skip else (12, 38, 26)
        order_border = (255, 59, 105) if is_skip else (0, 245, 155)
        order_text = "⛔️ KUTISH (SKIP)" if is_skip else "🟢 KIRISH (LONG BUY)"
        order_col = (255, 80, 110) if is_skip else (0, 245, 155)

        self.draw_glass_card(draw, [(905, 130), (1225, 205)], fill=order_fill, outline=order_border, radius=16, width=1)
        draw.text((925, 145), order_text, fill=order_col, font=self.font_mid_bold)
        
        conf = signal.get("confidence", 95)
        sub_info = "Xavfli zona | Depozitni saqlang" if is_skip else f"Ishonch: {conf}% | Tezkor kirish"
        draw.text((925, 175), sub_info, fill=(180, 205, 235), font=self.font_small)

        # 2-Vidjet: Foyda Nishonlari (Take Profit)
        draw.text((905, 222), "🎯 FOYDA NISHONLARI (TP)", fill=(140, 165, 195), font=self.font_small_bold)
        
        tp1 = signal.get("tp1", "1.38x — 1.55x 🛡")
        tp2 = signal.get("tp2", "2.00x — 2.60x 💎")
        tp3 = signal.get("tp3", "4.50x+ 🌌")

        # TP 1 Nishon
        self.draw_glass_card(draw, [(905, 245), (1225, 295)], fill=(18, 26, 42), radius=14)
        draw.text((920, 260), "TP 1 (Xavfsiz):", fill=(160, 190, 225), font=self.font_small_bold)
        draw.text((1055, 260), str(tp1), fill=(0, 245, 155), font=self.font_small_bold)

        # TP 2 Nishon
        self.draw_glass_card(draw, [(905, 305), (1225, 355)], fill=(18, 26, 42), radius=14)
        draw.text((920, 320), "TP 2 (Asosiy):", fill=(160, 190, 225), font=self.font_small_bold)
        draw.text((1055, 320), str(tp2), fill=(0, 229, 255), font=self.font_small_bold)

        # TP 3 Nishon
        self.draw_glass_card(draw, [(905, 365), (1225, 415)], fill=(18, 26, 42), radius=14)
        draw.text((920, 380), "TP 3 (Moon):", fill=(160, 190, 225), font=self.font_small_bold)
        draw.text((1055, 380), str(tp3), fill=(255, 204, 0), font=self.font_small_bold)

        # 3-Vidjet: Bozor Pulsi va Dinamikasi
        draw.text((905, 435), "📊 BOZOR PULSI VA DINAMIKASI", fill=(140, 165, 195), font=self.font_small_bold)
        
        self.draw_glass_card(draw, [(905, 458), (1225, 580)], fill=(18, 26, 42), radius=16)
        
        raw_regime = stats.get("regime", "🟢 Yashil To'lqin")
        regime_clean = raw_regime.replace("🟢", "").replace("🔴", "").replace("🟡", "").split("(")[0].strip()
        phase_col = (0, 245, 155) if "Yashil" in raw_regime or "Bullish" in raw_regime else ((255, 59, 105) if "Sovuq" in raw_regime or "Cold" in raw_regime else (255, 204, 0))
        
        draw.text((925, 475), "Faza:", fill=(160, 180, 210), font=self.font_small)
        draw.ellipse([(1000, 481), (1010, 491)], fill=phase_col)
        draw.text((1018, 475), regime_clean, fill=(255, 255, 255), font=self.font_small_bold)
        
        draw.text((925, 503), "G'alaba darajasi:", fill=(160, 180, 210), font=self.font_small)
        draw.text((1060, 503), f"{acc_val}%", fill=(0, 245, 155), font=self.font_small_bold)

        draw.text((925, 530), f"RSI ko'rsatkichi: {rsi_val:.1f}", fill=(160, 180, 210), font=self.font_small)
        # RSI indikator shkalasi
        meter_x1, meter_x2 = 925, 1205
        draw.rounded_rectangle([(meter_x1, 552), (meter_x2, 562)], radius=5, fill=(30, 42, 65))
        rsi_fill_w = int((min(max(rsi_val, 0), 100) / 100.0) * (meter_x2 - meter_x1))
        meter_col = (0, 245, 155) if 40 <= rsi_val <= 70 else (255, 59, 105)
        draw.rounded_rectangle([(meter_x1, 552), (meter_x1 + rsi_fill_w, 562)], radius=5, fill=meter_col)

        # 4-Vidjet: Xavfsizlik Qoidasi
        self.draw_glass_card(draw, [(905, 598), (1225, 675)], fill=(14, 20, 32), outline=(32, 48, 72), radius=14)
        draw.text((920, 612), "💡 XAVFSIZLIK QOIDASI", fill=(0, 229, 255), font=self.font_small_bold)
        draw.text((920, 634), "TP1 da asosiy foydani yeching.", fill=(160, 190, 225), font=self.font_tiny)
        draw.text((920, 650), "Depozitdan 1-3% tavsiya etiladi.", fill=(140, 165, 195), font=self.font_tiny)

        return img

# Moslashuvchanlik uchun taxallus (TerminalFrameRenderer alias)
TerminalFrameRenderer = IOS26FrameRenderer
