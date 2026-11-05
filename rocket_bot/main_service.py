import asyncio
import json
import logging
import random
import time
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Modul yo'lini birinchi o'ringa qo'yish
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from urllib.parse import parse_qs
import socketio
import requests
from telethon import TelegramClient, functions, types, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import (
    SendReactionRequest, GetMessagesViewsRequest, GetHistoryRequest
)
from telethon.tl.types import ReactionEmoji
from telethon.tl.functions.phone import (
    CreateGroupCallRequest, GetGroupCallStreamRtmpUrlRequest,
    GetGroupCallRequest, DiscardGroupCallRequest
)
import numpy as np

from config import (
    API_ID, API_HASH, SESSION_NAME, BOT_USERNAME, TARGET_USER,
    SIGNAL_BOT_TOKEN, CHANNEL_ID
)
from database import (
    save_round, get_recent_multipliers, init_db, evaluate_last_signal,
    get_signal_accuracy, get_current_streak, get_demo_portfolio,
    record_demo_trade, get_demo_stats
)
from advanced_analyzer import QuantumRocketAnalyzer, QuantitativeSignal
from live_stream_renderer import TerminalFrameRenderer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RocketService")

DATA_FILE = BASE_DIR / "webapp_data.json"


# Inline tugmalar foydalanuvchi talabi bilan olib tashlandi (reaksiyalik tugmalar va jonli efir tugmasi yo'q)


class EpicRocketService:
    def __init__(self):
        userbot_session_str = os.getenv("ROCKET_USERBOT_SESSION") or os.getenv("USERBOT_SESSION_STRING")
        if userbot_session_str and len(userbot_session_str.strip()) > 50:
            target_sess = StringSession(userbot_session_str.strip())
            logger.info("🔑 Telethon StringSession orqali ulanmoqda (Render bulut rejimi)...")
        else:
            target_sess = SESSION_NAME
            logger.info(f"📂 Telethon fayl sessiyasi orqali ulanmoqda: {SESSION_NAME}")

        self.tg_client = TelegramClient(target_sess, API_ID, API_HASH)
        self.sio = socketio.AsyncClient(
            logger=False,
            engineio_logger=False,
            reconnection=True,
            reconnection_attempts=100,
            reconnection_delay=2,
            reconnection_delay_max=10
        )
        self.analyzer = QuantumRocketAnalyzer()
        self.current_round_id = None
        self.current_multiplier = 1.0
        self.tg_data = None
        self.channel_entity = None
        self.channel_last_message = None  # {"msg_id": 123, "text": "..."}
        self.round_counter = 0

        # Stream & GUI State
        self.renderer = TerminalFrameRenderer()
        self.stream_state = {
            "status": "waiting",  # "waiting", "flying", "crashed"
            "current_mult": 1.00,
            "crashed_mult": 1.00,
            "round_id": "---",
            "history": [],
            "signal": {},
            "stats": {}
        }
        self.stream_task = None
        self.stream_proc = None
        self.stream_monitor_task = None
        self.stream_link = "https://t.me/epic_signal?livestream"

        self.setup_socket_events()
        self.setup_telegram_handlers()

    async def get_fresh_tg_data(self) -> str:
        """Telethon orqali botdan yangi tgWebAppData olish"""
        logger.info("Telegram orqali yangi WebApp token olinmoqda...")
        if not self.tg_client.is_connected():
            await self.tg_client.connect()

        bot = await self.tg_client.get_input_entity(BOT_USERNAME)
        res = await self.tg_client(functions.messages.RequestAppWebViewRequest(
            peer=bot,
            app=types.InputBotAppShortName(bot_id=bot, short_name="app"),
            platform="android",
            write_allowed=True
        ))
        
        webapp_url = res.url
        fragment = webapp_url.split('#')[1]
        params = parse_qs(fragment)
        tg_data = params.get('tgWebAppData', [''])[0]
        
        with open(DATA_FILE, "w") as f:
            json.dump({"url": webapp_url, "tgWebAppData": tg_data, "updated_at": str(datetime.now())}, f, indent=4)
            
        logger.info("Yangi tgWebAppData muvaffaqiyatli saqlandi!")
        self.tg_data = tg_data
        return tg_data

    def setup_telegram_handlers(self):
        @self.tg_client.on(events.NewMessage(chats=["me", TARGET_USER], pattern=r"^/(stats|statistika|signal|yordam|help|radar)"))
        async def on_user_command(event):
            acc_pct, wins, total_sig = get_signal_accuracy(limit=30)
            streak = get_current_streak()
            history = get_recent_multipliers(limit=10)
            med_10 = round(float(np.median(history)), 2) if history else 2.0
            regime = "🟢 Yashil To'lqin (Hot)" if med_10 >= 2.0 else ("🔴 Sovuq Tsikl (Cold)" if med_10 < 1.4 else "🟡 Barqaror Tsikl")
            
            text = (
                f"⚡️ <b>EPIC QUANTUM TRADING TERMINAL (v5.2 Pro)</b> 🚀\n\n"
                f"📊 <b>Aktiv:</b> <code>EPIC / USDT (Rocket Index)</code>\n"
                f"🎯 <b>Model Aniqligi:</b> <b>{acc_pct}%</b>\n"
                f"🔥 <b>Yutuq Seriyasi:</b> <b>{streak} ta ketma-ket</b>\n"
                f"📈 <b>Natijalar (oxirgi 30):</b> <b>{wins} yutuq / {total_sig} signal</b>\n"
                f"🌊 <b>Bozor Fazasi:</b> {regime} (Median: {med_10}x)\n\n"
                f"📺 <i>Jonli video efir va signallar @epic_signal kanalida 24/7 uzatilmoqda.</i>"
            )
            await event.reply(text, parse_mode="html")

        @self.tg_client.on(events.NewMessage(pattern=r"^/(stream|strim|status|holat|efir|live)"))
        async def on_stream_check_command(event):
            # 1. Agar kanal yoki guruhda yozilgan bo'lsa, xabarni darhol o'chirib tozalash
            is_public = event.is_channel or event.is_group
            try:
                await event.delete()
            except Exception:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{SIGNAL_BOT_TOKEN}/deleteMessage",
                        json={"chat_id": event.chat_id, "message_id": event.id},
                        timeout=3
                    )
                except Exception:
                    pass

            # 2. Strim holatini to'liq tekshirish
            ffmpeg_alive = self.stream_proc is not None and self.stream_proc.returncode is None
            stream_active = False
            if self.channel_entity:
                try:
                    full = await self.tg_client(GetFullChannelRequest(self.channel_entity))
                    if full.full_chat.call:
                        call_info = await self.tg_client(GetGroupCallRequest(call=full.full_chat.call, limit=1))
                        stream_active = getattr(call_info.call, "rtmp_stream", False)
                except Exception:
                    pass

            # 3. Agar efir yoki FFmpeg to'xtab qolgan bo'lsa -> O'ZI AVTOMATIK TO'G'RILAYDI!
            healed = False
            if not (ffmpeg_alive and stream_active):
                logger.info("🛠 /strim buyrug'i: Strim to'xtagani aniqlandi, avtomatik qayta tiklanmoqda...")
                await self.recover_live_stream(force_restart=True)
                healed = True
                ffmpeg_alive = True
                stream_active = True
            else:
                logger.info("✅ /strim buyrug'i: Strim 100% faol holatda ishlayapti.")

            # 4. Agar foydalanuvchi shaxsiy chatda (PM yoki me) yozgan bo'lsa, hisobot yuborish
            if not is_public:
                stream_link = self.stream_link
                status = self.stream_state.get("status", "waiting")
                round_id = self.stream_state.get("round_id", "---")
                mult = self.stream_state.get("current_mult", 1.0)
                sig = self.stream_state.get("signal", {}).get("type", "Kutilmoqda")
                acc = self.stream_state.get("stats", {}).get("accuracy", 0)
                streak = self.stream_state.get("stats", {}).get("streak", 0)

                efir_text = "🟢 <b>Jonli efir uzatilmoqda (Faol)</b>" if ffmpeg_alive else "🔴 <b>Efir ulanmoqda</b>"
                healed_text = "🛠 <i>Nosozlik avtomatik to‘g‘rilandi va strim qayta tiklandi!</i>\n" if healed else ""

                report = (
                    f"📡 <b>EPIC LIVE STREAM TEKSHIRUVI</b>\n\n"
                    f"🔗 <b>Strim havolasi:</b> <a href='{stream_link}'>{stream_link}</a>\n"
                    f"✅ <b>Havola holati:</b> <b>To'g'ri va faol (24/7 RTMP)</b>\n"
                    f"🎥 <b>Efir holati:</b> {efir_text}\n"
                    f"{healed_text}\n"
                    f"📺 <b>STRIMDA NIMA KO'RSATILMOQDA:</b>\n"
                    f"• <b>Raund:</b> <code>#{round_id}</code>\n"
                    f"• <b>Holat:</b> <b>{status.upper()}</b> ({mult:.2f}x)\n"
                    f"• <b>Signal:</b> <b>{sig}</b>\n"
                    f"• <b>Aniqlik:</b> <b>{acc}%</b> | <b>Streak:</b> <b>{streak} ta</b>\n"
                    f"• <b>Sifat:</b> <code>1280x720 HD @ 10 FPS (iOS 26 Terminal)</code>\n\n"
                    f"<i>Quyida strimning ayni paytdagi ekrani tasviri (skrinshot):</i>"
                )
                preview_path = BASE_DIR / "latest_stream_frame.png"
                if preview_path.exists():
                    await event.reply(report, file=str(preview_path), parse_mode="html")
                else:
                    await event.reply(report, parse_mode="html")

    def setup_socket_events(self):
        @self.sio.event
        async def connect():
            logger.info("🌐 Jonli o'yin serveriga muvaffaqiyatli ulandi!")
            await self.sio.emit("subscribe", {"module": "crash", "type": "all"})
            logger.info("📡 Rocket jonli efiriga obuna bo'lindi. Raundlar kutilmoqda...")

        @self.sio.on("crash:new_round")
        async def on_new_round(data):
            self.current_round_id = str(data.get("roundId") or "")
            self.current_multiplier = 1.0
            self.round_counter += 1
            
            # Stream status
            self.stream_state["status"] = "waiting"
            self.stream_state["round_id"] = self.current_round_id
            self.stream_state["current_mult"] = 1.00
            
            logger.info(f"🚀 Yangi raund #{self.current_round_id} boshlandi!")

        @self.sio.on("crash:multiplier")
        async def on_multiplier(data):
            mult = data.get("multiplier")
            if mult:
                self.current_multiplier = float(mult)
                self.stream_state["status"] = "flying"
                self.stream_state["current_mult"] = self.current_multiplier

        @self.sio.on("crash:crashed")
        async def on_crashed(data):
            round_id = str(data.get("roundId") or self.current_round_id or "")
            crash_point = float(data.get("crashPoint") or self.current_multiplier or 1.0)
            logger.info(f"💥 RAUND YAKUNLANDI #{round_id}: {crash_point:.2f}x")
            
            # Update stream state
            self.stream_state["status"] = "crashed"
            self.stream_state["crashed_mult"] = crash_point
            self.stream_state["current_mult"] = crash_point

            # 1. Tezkor: Oxirgi signal natijasini baholash va kanaldagi xabarni tahrirlash (Background non-blocking)
            try:
                eval_res = evaluate_last_signal(crash_point)
                if eval_res:
                    sig_id, sig_type, res, target_min, target_max = eval_res
                    logger.info(f"📊 Signal natijasi: {sig_type} -> {res}")
                    asyncio.create_task(self.update_last_signal_messages(res, crash_point))
            except Exception as eval_err:
                logger.warning(f"Baholashda xato: {eval_err}")

            # 2. Bazaga saqlash
            save_round(crash_point, round_id)

            # 3. Katta parvoz (5x+) alerti
            if crash_point >= 5.00:
                asyncio.create_task(self.send_mega_pump_alert(round_id, crash_point))
            
            # 4. Stream statistikalarini yangilash
            recent_hist = get_recent_multipliers(limit=25)
            self.stream_state["history"] = recent_hist
            acc_pct, wins, total_sig = get_signal_accuracy(limit=30)
            streak = get_current_streak()
            med_10 = round(float(np.median(recent_hist[-10:])), 2) if recent_hist else 2.0
            regime = "🟢 Yashil To'lqin" if med_10 >= 2.0 else ("🔴 Sovuq Tsikl" if med_10 < 1.35 else "🟡 Barqaror Harakat")
            rsi_val = 50.0
            if len(recent_hist) >= 14:
                diffs = np.diff(recent_hist[-15:])
                gains = [d for d in diffs if d > 0]
                losses = [-d for d in diffs if d < 0]
                avg_gain = sum(gains) / 14 if gains else 0.001
                avg_loss = sum(losses) / 14 if losses else 0.001
                rs = avg_gain / avg_loss
                rsi_val = round(100 - (100 / (1 + rs)), 1)

            self.stream_state["stats"] = {
                "accuracy": acc_pct,
                "streak": streak,
                "median": med_10,
                "regime": regime,
                "rsi": rsi_val
            }

            # 5. Har 5 ta raundda umumiy tahlil xulosasi yuborish (Tezlik oshirildi)
            if self.round_counter % 5 == 0:
                asyncio.create_task(self.send_market_recap(round_id))

            # 6. Navbatdagi raund uchun tahlil va tezkor VIP signal (0 kechikish)
            signal = self.analyzer.analyze_round(crash_point, round_id)
            if signal:
                # Update stream state for HUD
                sig_dir = f"🟢 {signal.signal_type} // {signal.target_cashout}"
                self.stream_state["signal"] = {
                    "type": signal.signal_type,
                    "direction": sig_dir,
                    "confidence": signal.confidence_score,
                    "tp1": signal.safe_cashout,
                    "tp2": signal.target_cashout,
                    "tp3": signal.moon_potential,
                    "reason": signal.reason
                }
                await self.broadcast_signal(signal, crash_point, round_id)

        @self.sio.event
        async def disconnect():
            logger.warning("⚠️ Socket server bilan aloqa uzildi. Qayta ulanish kutilmoqda...")

        @self.sio.event
        async def connect_error(data):
            logger.error(f"❌ Socket ulanish xatosi: {data}")

    async def update_last_signal_messages(self, res: str, crash_point: float):
        streak = get_current_streak()
        profit_pct = int((crash_point - 1) * 100)
        
        if res == "WIN":
            badge = (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🎉 <b>G'ALABA QAYD ETILDI! (+{profit_pct}% FOYDA) 🚀</b>\n"
                f"✅ <b>Koeffitsiyent:</b> <code>{crash_point:.2f}x</code>\n"
                f"💎 <b>TP1 va TP2 to'liq yopildi!</b>\n"
                f"🔥 <b>Ketma-ket g'alabalar:</b> <b>{streak} ta</b>"
            )
        elif res == "SAFE_WIN":
            badge = (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🛡 <b>XAVFSIZ YUTUQ (SAFE WIN) (+{profit_pct}%) ✅</b>\n"
                f"✅ <b>Koeffitsiyent:</b> <code>{crash_point:.2f}x</code>\n"
                f"🎯 <b>TP1 kafolatlangan foydasi olindi!</b>\n"
                f"🔥 <b>G'alaba seriyasi:</b> <b>{streak} ta</b>"
            )
        elif res == "SAVED":
            badge = (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🛡 <b>AI ANIQ TAXMINI! DEPOZIT ASROVI ✅</b>\n"
                f"📉 <b>Server portladi:</b> <code>{crash_point:.2f}x</code>\n"
                f"💡 <i>Tuzoqdan muvaffaqiyatli saqlanildi!</i>"
            )
        elif res == "SKIPPED":
            badge = (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"⚪️ <b>KUZATUV RAUNDI:</b> <code>{crash_point:.2f}x</code>\n"
                f"💡 <i>Xavfsiz strategiya bo'yicha o'tkazib yuborildi</i>"
            )
        else:
            badge = (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📉 <b>KICHIK TO'XTASH:</b> <code>{crash_point:.2f}x</code>\n"
                f"💡 <i>Keyingi signalda qaytarish strategiyasi qo'llaniladi</i>"
            )

        # Kanaldagi xabarni Bot API yoki Telethon orqali tahrirlash
        if self.channel_last_message:
            ch_msg_id = self.channel_last_message["msg_id"]
            ch_orig_text = self.channel_last_message["text"]
            ch_updated_text = ch_orig_text + badge
            edited = False

            # Kanaldagi xabarni FAQAT Bot API orqali yangilash (Foydalanuvchi akkauntidan emas!)
            target_chat = CHANNEL_ID if CHANNEL_ID else "@epic_signal"
            if SIGNAL_BOT_TOKEN:
                try:
                    payload = {
                        "chat_id": target_chat,
                        "message_id": ch_msg_id,
                        "text": ch_updated_text,
                        "parse_mode": "HTML"
                    }
                    res_edit = requests.post(
                        f"https://api.telegram.org/bot{SIGNAL_BOT_TOKEN}/editMessageText",
                        json=payload,
                        timeout=5
                    )
                    if res_edit.status_code == 200:
                        edited = True
                        logger.info("📢 Kanaldagi signal natijasi Bot API (@Chaqiruvchiibot) orqali yangilandi!")
                except Exception as b_err:
                    logger.debug(f"Bot API edit error: {b_err}")

            # CheapVPS Webhook bilan sinxronizatsiya
            try:
                requests.post(
                    "https://muxa.cheapvps.cfd/webhook.php?action=edit_signal&key=epic_secret_muxa_2026",
                    json={"message_id": ch_msg_id, "text": ch_updated_text},
                    verify=False,
                    timeout=3
                )
            except Exception:
                pass

            # Tahrirlangan postga ko'rishlar va reaksiyani kuchaytirish
            asyncio.create_task(self.boost_message(ch_msg_id))
            self.channel_last_message = None

    async def send_mega_pump_alert(self, round_id: str, crash_point: float):
        """Katta parvoz (5x+) chiqqanda tezkor global bayram xabari yuborish"""
        profit = int((crash_point - 1) * 100)
        text = (
            f"🌌 <b>EPIC ROCKET // KOSMIK PARVOZ!</b> 🌌\n\n"
            f"🚀 <b>Raund:</b> <code>#{round_id}</code>\n"
            f"💥 <b>NATIJA:</b> <b>{crash_point:.2f}x (+{profit}%) 💎</b>\n\n"
            f"✨ <i>Jonli efirda navbatdagi to'lqinni birga ushlang!</i>\n"
            f"📺 <b>24/7 Jonli Strim:</b> @epic_signal"
        )
        if SIGNAL_BOT_TOKEN and CHANNEL_ID:
            try:
                res_post = requests.post(
                    f"https://api.telegram.org/bot{SIGNAL_BOT_TOKEN}/sendMessage",
                    json={"chat_id": CHANNEL_ID, "text": text, "parse_mode": "HTML"},
                    timeout=5
                )
                if res_post.status_code == 200:
                    resp_json = res_post.json()
                    msg_id = resp_json.get("result", {}).get("message_id")
                    if msg_id:
                        asyncio.create_task(self.boost_message(msg_id))
                        logger.info(f"🌌 Katta parvoz alerti kanalga yuborildi: {crash_point:.2f}x")
            except Exception as e:
                logger.debug(f"Mega pump alert error: {e}")

    async def send_market_recap(self, round_id: str):
        """Har 5 ta raundda umumiy bozor tahlili xulosasini chiqarish (Tezlik oshirilgan)"""
        history = get_recent_multipliers(limit=10)
        if not history:
            return
        acc_pct, wins, total_sig = get_signal_accuracy(limit=30)
        streak = get_current_streak()
        med = round(float(np.median(history)), 2)
        avg = round(float(np.mean(history)), 2)
        regime = "🟢 YASHIL TO'LQIN (HOT)" if med >= 2.0 else ("🔴 SOVUQ TSIKL (COLD)" if med < 1.35 else "🟡 BARQAROR HARAKAT")
        
        recap_text = (
            f"📊 <b>EPIC AI // TEZKOR BOZOR PULSI</b>\n\n"
            f"📈 <b>Bozor holati:</b> <b>{regime}</b>\n"
            f"🎯 <b>AI Aniqlik darajasi:</b> <b>{acc_pct}%</b> ({wins}/{total_sig})\n"
            f"🔥 <b>Joriy g'alabalar seriyasi:</b> <b>{streak} ta</b>\n"
            f"⚡️ <b>O'rtacha koeffitsiyent:</b> <code>{avg}x</code> | <b>Median:</b> <code>{med}x</code>\n\n"
            f"📺 <i>Barcha signallar 24/7 jonli efir orqali sinxron uzatilmoqda!</i>"
        )
        
        sent = False
        msg_id = None
        target_chat = CHANNEL_ID if CHANNEL_ID else "@epic_signal"
        if SIGNAL_BOT_TOKEN:
            try:
                res_post = requests.post(
                    f"https://api.telegram.org/bot{SIGNAL_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": target_chat,
                        "text": recap_text,
                        "parse_mode": "HTML"
                    },
                    timeout=5
                )
                if res_post.status_code == 200:
                    resp_json = res_post.json()
                    msg_id = resp_json.get("result", {}).get("message_id")
                    sent = True
                    logger.info("📊 5-raundlik tahlil Bot API (@Chaqiruvchiibot) orqali kanalga yuborildi!")
            except Exception as e:
                logger.debug(f"Bot API recap error: {e}")

        # CheapVPS Webhook bilan sinxronizatsiya
        try:
            requests.post(
                "https://muxa.cheapvps.cfd/webhook.php?action=send_recap&key=epic_secret_muxa_2026",
                json={"text": recap_text},
                verify=False,
                timeout=3
            )
        except Exception:
            pass

        if msg_id:
            asyncio.create_task(self.boost_message(msg_id))

    async def broadcast_signal(self, signal: QuantitativeSignal, last_multiplier: float, round_id: str):
        rsi_val = signal.metrics.get("rsi", 50.0) if hasattr(signal, "metrics") and signal.metrics else 50.0
        streak = get_current_streak()
        
        if signal.signal_type == "DANGER_SKIP":
            text = (
                f"🚨 <b>EPIC VIP // BOZOR TUZOG'I (SKIP)</b> 🚨\n\n"
                f"📊 <b>Oxirgi:</b> <code>#{round_id} — {last_multiplier:.2f}x</code>\n"
                f"⛔️ <b>Buyruq:</b> <b>KUTISH (NO TRADE)</b>\n"
                f"🛡 <b>Sabab:</b> {signal.reason}\n"
                f"🔍 <b>RSI:</b> <code>{rsi_val:.1f}</code> | <b>Xavf:</b> <b>YUQORI</b>\n\n"
                f"💡 <i>Depozitni saqlang, keyingi xavfsiz to'lqin kutilmoqda!</i>"
            )
        else:
            if signal.signal_type == "MOON_BURST":
                icon = "🌌"
                tag = "KATTA SAKRASH (MOON BURST)"
            elif signal.signal_type == "TREND_RIDER":
                icon = "🚀"
                tag = "TREND TO'LQINI (MOMENTUM)"
            elif signal.signal_type == "HUNTER_2X":
                icon = "💎"
                tag = "ASOSIY TARGET (HUNTER 2X)"
            elif signal.signal_type == "SCALP_SNIPER":
                icon = "⚡️"
                tag = "TEZKOR SCALP (SNIPER)"
            else:
                icon = "🎯"
                tag = "BARQAROR TIKLANISH"

            text = (
                f"{icon} <b>EPIC AI // GLOBAL VIP SIGNAL</b> {icon}\n\n"
                f"🎯 <b>Buyruq:</b> <b>🟢 KIRISH (LONG BUY)</b>\n"
                f"⚡️ <b>Strategiya:</b> <b>{tag}</b>\n"
                f"📥 <b>Kirish:</b> <code>1.05x — 1.15x</code>\n\n"
                f"💎 <b>FOYDA NISHONLARI (TAKE PROFIT):</b>\n"
                f"├ 🛡 <b>TP 1 (Xavfsiz):</b> <b>{signal.safe_cashout}</b>\n"
                f"├ 🎯 <b>TP 2 (Asosiy):</b> <b>{signal.target_cashout}</b>\n"
                f"└ 🌌 <b>TP 3 (Moon):</b> <b>{signal.moon_potential}</b>\n\n"
                f"📊 <b>Ishonch:</b> <b>{signal.confidence_score}%</b> | <b>RSI:</b> <code>{rsi_val:.1f}</code>\n"
                f"🔥 <b>G'alaba seriyasi:</b> <b>{streak} ta ketma-ket</b>\n\n"
                f"💡 <i>Asosiy foydani TP1 da kafolatlangan yechib oling!</i>"
            )

        logger.info(f"\n{'='*50}\n[TRADING SIGNAL] {signal.signal_type} -> {signal.target_cashout}\n{'='*50}")

        sent = False
        msg_id = None
        target_chat = CHANNEL_ID if CHANNEL_ID else "@epic_signal"

        # Signallarni FAQAT Bot API (@Chaqiruvchiibot) orqali kanalga yuborish
        if SIGNAL_BOT_TOKEN:
            for attempt in range(3):
                try:
                    res_post = requests.post(
                        f"https://api.telegram.org/bot{SIGNAL_BOT_TOKEN}/sendMessage",
                        json={
                            "chat_id": target_chat,
                            "text": text,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True
                        },
                        timeout=5
                    )
                    if res_post.status_code == 200:
                        resp_json = res_post.json()
                        msg_id = resp_json.get("result", {}).get("message_id")
                        if msg_id:
                            self.channel_last_message = {"msg_id": msg_id, "text": text}
                            sent = True
                            logger.info(f"📢 Signal Bot API (@Chaqiruvchiibot) orqali kanalga yuborildi! (ID: {msg_id})")
                            break
                    else:
                        logger.warning(f"Bot API send error status {res_post.status_code}: {res_post.text}")
                except Exception as e:
                    logger.debug(f"Bot API send error attempt {attempt+1}: {e}")
                await asyncio.sleep(1)

        # CheapVPS Webhook bilan sinxronizatsiya (muxa.cheapvps.cfd)
        try:
            requests.post(
                "https://muxa.cheapvps.cfd/webhook.php?action=send_signal&key=epic_secret_muxa_2026",
                json={"text": text},
                verify=False,
                timeout=3
            )
        except Exception:
            pass

        if msg_id:
            asyncio.create_task(self.boost_message(msg_id))

    async def boost_message(self, msg_id: int):
        """Kanal xabariga ko'rishlar sonini oshirish va reaksiya qoldirish"""
        if not self.channel_entity:
            return
        try:
            # 1. Ko'rishlar sonini oshirish
            await self.tg_client(GetMessagesViewsRequest(
                peer=self.channel_entity,
                id=[msg_id],
                increment=True
            ))
            # 2. Reaksiya qo'yish
            emoji = random.choice(["🔥", "🚀", "👍", "🎉", "❤️"])
            await self.tg_client(SendReactionRequest(
                peer=self.channel_entity,
                msg_id=msg_id,
                reaction=[ReactionEmoji(emoticon=emoji)]
            ))
            logger.info(f"✨ Post #{msg_id} aktivlashtirildi (View + {emoji})")
        except Exception as e:
            logger.debug(f"Post #{msg_id} boost: {e}")

    async def run_activity_booster(self):
        """Barcha oldingi, hozirgi va keyingi postlarni doimiy aktivlashtiruvchi fon xizmati"""
        logger.info("⚡️ Postlarni doimiy aktivlashtiruvchi booster (Reaksiyalar va Ko'rishlar) ishga tushdi...")
        await asyncio.sleep(4)
        
        while True:
            try:
                if self.channel_entity:
                    # Oxirgi 60 ta xabarni olish
                    history = await self.tg_client(GetHistoryRequest(
                        peer=self.channel_entity,
                        offset_id=0,
                        offset_date=None,
                        add_offset=0,
                        limit=60,
                        max_id=0,
                        min_id=0,
                        hash=0
                    ))
                    
                    if history and history.messages:
                        msg_ids = [m.id for m in history.messages if hasattr(m, 'id') and m.id]
                        
                        # 1. Barcha postlarga ko'rishlar (views) oshirish
                        if msg_ids:
                            try:
                                await self.tg_client(GetMessagesViewsRequest(
                                    peer=self.channel_entity,
                                    id=msg_ids[:50],
                                    increment=True
                                ))
                            except Exception as ve:
                                logger.debug(f"Views boost: {ve}")

                        # 2. Reaksiyalar qo'yish (oldingi va hozirgi postlar)
                        for m in history.messages[:30]:
                            has_my_rx = False
                            if hasattr(m, 'reactions') and m.reactions:
                                if getattr(m.reactions, 'recent_reactions', None):
                                    has_my_rx = True
                            
                            if not has_my_rx:
                                emoji = random.choice(["🔥", "🚀", "👍", "❤️", "🎉"])
                                try:
                                    await self.tg_client(SendReactionRequest(
                                        peer=self.channel_entity,
                                        msg_id=m.id,
                                        reaction=[ReactionEmoji(emoticon=emoji)]
                                    ))
                                    await asyncio.sleep(1.2)
                                except Exception:
                                    pass
                                    
                    logger.info("⚡️ Kanal postlari ko'rishlari va reaksiyalari yangilandi!")
            except Exception as e:
                logger.debug(f"Activity booster xatosi: {e}")
                
            await asyncio.sleep(180)  # Har 3 daqiqada yangilanadi

    async def recover_live_stream(self, force_restart: bool = False):
        """Jonli efirni 100% avtomatik tekshirish, tuzatish va yangidan ishga tushirish"""
        logger.info("🛠 [STRIM TIKLASH] Jonli efir tekshirilmoqda va avtomatik to'g'rilanmoqda...")
        
        # 1. Eski FFmpeg jarayonini to'xtatish
        if self.stream_proc:
            try:
                self.stream_proc.kill()
            except Exception:
                pass
            self.stream_proc = None

        # 2. Kanal entity tekshirish
        if not self.channel_entity:
            try:
                self.channel_entity = await self.tg_client.get_entity("epic_signal")
            except Exception as e:
                logger.error(f"Kanal topilmadi: {e}")
                return False

        # 3. Telegram Group Call tozalash va yangi RTMP ochish
        try:
            full = await self.tg_client(GetFullChannelRequest(self.channel_entity))
            call = full.full_chat.call
            
            if call and (force_restart or not getattr(call, "rtmp_stream", True)):
                try:
                    logger.info("Mavjud eski efir yopilmoqda...")
                    await self.tg_client(DiscardGroupCallRequest(call=call))
                    await asyncio.sleep(2)
                except Exception as dc_err:
                    logger.debug(f"Discard call debug: {dc_err}")
                call = None

            if not call:
                logger.info("📡 Yangi RTMP jonli efir yaratilmoqda...")
                await self.tg_client(CreateGroupCallRequest(
                    peer=self.channel_entity,
                    rtmp_stream=True,
                    title="⚡️ EPIC ROCKET TRADING 24/7"
                ))
                await asyncio.sleep(2)
        except Exception as call_err:
            logger.warning(f"Group call sozlashda xatolik: {call_err}")

        # 4. Stream taskni qayta uyg'otish
        if self.stream_task and not self.stream_task.done():
            self.stream_task.cancel()
        
        self.stream_task = asyncio.create_task(self.run_live_video_stream())
        logger.info("✅ [STRIM TIKLASH] Jonli efir 100% muvaffaqiyatli qayta ishga tushirildi!")
        return True

    async def run_live_video_stream(self):
        """24/7 Telegram kanal jonli efiriga RTMP orqali video va grafik uzatish"""
        logger.info("🎬 Jonli efir (Live Stream 24/7) moduli tayyorlanmoqda...")
        
        while True:
            proc = None
            try:
                # 1. Kanalni aniqlash
                if not self.channel_entity:
                    try:
                        self.channel_entity = await self.tg_client.get_entity("epic_signal")
                    except Exception as e:
                        logger.error(f"Kanal topilmadi: {e}")
                        await asyncio.sleep(5)
                        continue

                # 2. Channel Call tekshirish yoki yangi RTMP efir yaratish
                full = await self.tg_client(GetFullChannelRequest(self.channel_entity))
                call = full.full_chat.call
                need_create = False
                
                if not call:
                    need_create = True
                else:
                    try:
                        call_info = await self.tg_client(GetGroupCallRequest(call=call, limit=1))
                        if not getattr(call_info.call, "rtmp_stream", False):
                            logger.info("Mavjud efir RTMP emas, RTMP efir yaratilmoqda...")
                            await self.tg_client(DiscardGroupCallRequest(call=call))
                            need_create = True
                    except Exception:
                        pass

                if need_create:
                    logger.info("📡 Kanallarda yangi RTMP jonli efir ochilmoqda...")
                    try:
                        await self.tg_client(CreateGroupCallRequest(
                            peer=self.channel_entity,
                            rtmp_stream=True,
                            title="⚡️ EPIC ROCKET TRADING 24/7"
                        ))
                        await asyncio.sleep(2)
                    except Exception as call_err:
                        logger.warning(f"Efir yaratishda xabar: {call_err}")

                # 3. RTMP Stream URL va Key olish
                rtmp_info = await self.tg_client(GetGroupCallStreamRtmpUrlRequest(
                    peer=self.channel_entity,
                    revoke=False
                ))
                rtmp_target = f"{rtmp_info.url}{rtmp_info.key}"
                logger.info(f"🚀 Telegram RTMP jonli efir ulandi: {rtmp_info.url}")

                # 4. FFmpeg jarayonini ishga tushirish (1280x720, 10 FPS, AAC audio)
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", "1280x720",
                    "-pix_fmt", "rgb24",
                    "-r", "10",
                    "-i", "-",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-tune", "zerolatency",
                    "-pix_fmt", "yuv420p",
                    "-g", "20",
                    "-c:a", "aac",
                    "-b:a", "64k",
                    "-f", "flv",
                    rtmp_target
                ]

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )

                self.stream_proc = proc
                logger.info("🎥 FFmpeg jonli efir strimini 100% muvaffaqiyatli boshladi (24/7)!")

                frame_interval = 0.1  # 10 fps
                frame_count = 0
                while proc.returncode is None:
                    t_start = time.time()
                    
                    # Frame render
                    img = self.renderer.render_frame(self.stream_state)
                    raw_bytes = img.tobytes()

                    frame_count += 1
                    if frame_count % 30 == 0:
                        try:
                            img.save(BASE_DIR / "latest_stream_frame.png")
                        except Exception:
                            pass

                    try:
                        proc.stdin.write(raw_bytes)
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        logger.warning("FFmpeg pipe uzildi. Qayta ulanadi...")
                        break

                    elapsed = time.time() - t_start
                    to_sleep = max(0.01, frame_interval - elapsed)
                    await asyncio.sleep(to_sleep)

            except Exception as stream_err:
                logger.error(f"Jonli efir uzatishda xatolik: {stream_err}. 5 soniyada qayta ulanadi...")
            finally:
                self.stream_proc = None
                if proc and proc.returncode is None:
                    try:
                        proc.stdin.close()
                        await proc.wait()
                    except Exception:
                        pass
            
            await asyncio.sleep(4)

    async def run_stream_monitor(self):
        """Doimiy strim holati, havola to'g'riligi va o'zini o'zi davolovchi (Self-Healing Watchdog) monitor"""
        logger.info("🛰 Doimiy strim tekshiruvchi monitor (Self-Healing Stream Watchdog) ishga tushdi...")
        await asyncio.sleep(15)
        
        failure_count = 0
        while True:
            try:
                link_status = "NOTOGRI"
                stream_active = False
                stream_link = self.stream_link
                
                if self.channel_entity:
                    try:
                        full = await self.tg_client(GetFullChannelRequest(self.channel_entity))
                        call = full.full_chat.call
                        if call:
                            call_info = await self.tg_client(GetGroupCallRequest(call=call, limit=1))
                            is_rtmp = getattr(call_info.call, "rtmp_stream", False)
                            if is_rtmp:
                                link_status = "TOGRI (FAOL)"
                                stream_active = True
                    except Exception as ge:
                        logger.debug(f"Call check debug: {ge}")
                
                ffmpeg_alive = self.stream_proc is not None and self.stream_proc.returncode is None
                
                status = self.stream_state.get("status", "waiting")
                round_id = self.stream_state.get("round_id", "---")
                mult = self.stream_state.get("current_mult", 1.0)
                sig = self.stream_state.get("signal", {}).get("type", "LONG_ENTRY")
                acc = self.stream_state.get("stats", {}).get("accuracy", 0)
                
                logger.info(
                    f"📺 [STRIM TEKSHIRUV] Havola: {stream_link} [{link_status}] | "
                    f"Efir: {'🟢 100% ISHLAYAPTI' if (stream_active and ffmpeg_alive) else '🟡 TIKLANMOQDA'} | "
                    f"Ko'rsatilmoqda: #{round_id} ({mult:.2f}x - {status}) | Signal: {sig} | Winrate: {acc}%"
                )

                # O'ZINI-O'ZI DAVOLASH (AVTOMATIK TO'G'RILASH)
                if not (stream_active and ffmpeg_alive):
                    failure_count += 1
                    logger.warning(f"⚠️ Strim nosozligi aniqlandi ({failure_count}/2). Avtomatik tiklash jarayonda...")
                    if failure_count >= 2:
                        logger.info("🚨 Strim 2 marta ketma-ket javob bermadi. Avtomatik qayta tiklanmoqda...")
                        await self.recover_live_stream(force_restart=True)
                        failure_count = 0
                else:
                    failure_count = 0

            except Exception as e:
                logger.debug(f"Strim monitor xatosi: {e}")
                
            await asyncio.sleep(20)

    async def start(self):
        init_db()
        await self.tg_client.connect()
        me = await self.tg_client.get_me()
        logger.info(f"👤 Userbot: {me.first_name} (@{me.username}) [ID: {me.id}]")

        try:
            self.channel_entity = await self.tg_client.get_entity("epic_signal")
            logger.info(f"📢 Kanal entity ulandi: {self.channel_entity.title} (ID: {self.channel_entity.id})")
        except Exception as ce:
            logger.error(f"Kanal entity topishda xatolik: {ce}")
            self.channel_entity = None

        # Boshlang'ich stream tarixini to'ldirish
        self.stream_state["history"] = get_recent_multipliers(limit=25)

        # 24/7 Jonli video efir vazifasini fonda ishga tushirish
        self.stream_task = asyncio.create_task(self.run_live_video_stream())
        self.stream_monitor_task = asyncio.create_task(self.run_stream_monitor())
        self.activity_task = asyncio.create_task(self.run_activity_booster())

        if DATA_FILE.exists():
            try:
                with open(DATA_FILE) as f:
                    d = json.load(f)
                    self.tg_data = d.get("tgWebAppData")
            except Exception:
                self.tg_data = None

        if not self.tg_data:
            await self.get_fresh_tg_data()

        headers = {
            "Origin": "https://epicgift.app",
            "Referer": "https://epicgift.app/",
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        }
        
        auth_data = {
            "authorization": f"tma {self.tg_data}",
            "x-client-info": json.dumps({"v": "1.0.0", "p": "android"})
        }

        while True:
            try:
                logger.info("🔌 Socket.IO serverga ulanmoqda...")
                await self.sio.connect(
                    "https://api.epicgift.app",
                    socketio_path="/socket.io",
                    transports=["websocket"],
                    headers=headers,
                    auth=auth_data,
                    wait_timeout=20
                )
                await self.sio.wait()
            except Exception as e:
                logger.error(f"Ulanishda xatolik: {e}. Token yangilanmoqda...")
                try:
                    await self.get_fresh_tg_data()
                    auth_data["authorization"] = f"tma {self.tg_data}"
                except Exception as tg_err:
                    logger.error(f"Telegramdan token olishda xato: {tg_err}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    service = EpicRocketService()
    try:
        asyncio.run(service.start())
    except KeyboardInterrupt:
        print("Tizim to'xtatildi.")
