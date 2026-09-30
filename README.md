# 🤖 Cheap.py — Klon (Fake / Test) Server

Ushbu papka `cheap.py` botining alohida ajratilgan klon (fake/staging) varianti.  
Asl Termux versiyasi o'z joyida xavfsiz qolgan.

---

## ⚠️ MUHIM QOIDA (Telegram cheklovi):
Telegram bitta `BOT_TOKEN`ni bir vaqtda 2 ta serverda ishlatishga ruxsat bermaydi.  
Shuning uchun ushbu fake variant uchun `@BotFather`dan 1 daqiqada yangi test bot ochib, uning tokenini kiriting.

---

## 🌐 1-USUL: Hugging Face Spaces (TAVSIYA QILINADI — 100% TEKIN & 24/7)
1. **huggingface.co** saytiga kiring va ro'yxatdan o'ting (karta so'ramaydi!).
2. **New Space** tugmasini bosing:
   - Space name: `cheap-fake-bot`
   - License: `mit`
   - Space SDK: **Docker** -> **Blank**
   - Visibility: **Public** yoki **Private**
3. Ushbu papkadagi fayllarni (`cheap.py`, `Dockerfile`, `requirements.txt`, `smm_bot.db`) yuklang (yoki Git orqali push qiling).
4. **Settings -> Variables and secrets** bo'limiga kiring:
   - `BOT_TOKEN` = sizning yangi test bot tokeningiz
   - `ADMIN_ID` = `8191930658`
   - `GROQ_API_KEY` = Groq API kaliti
5. Space avtomatik Docker build qiladi va **24/7 bepul, hech qachon o'chmasdan** ishlaydi!

---

## 🚀 2-USUL: Koyeb (24/7 Bepul)
1. **koyeb.com** ga kiring (bepul ro'yxatdan o'ting).
2. **Create App** -> **GitHub** yoki **Docker**.
3. Environment variables qo'shing va ishga tushiring.

---

## 💻 3-USUL: Ushbu VPS Serverning O'zida Ishlatish (PM2)
Agar boshqa saytga kirmasdan, shu serverning o'zida ikkinchi bot qilib ishlatmoqchi bo'lsangiz:
```bash
cd /data/data/com.termux/files/home/smm-bot-fake
cp .env.example .env
nano .env   # Yangi BOT_TOKEN ni kiriting
PM2_HOME=/data/data/com.termux/files/home/.pm2 pm2 start cheap.py --name "cheap_fake"
```
Bu holda ikkala bot ham bitta serverda bir vaqtda mustaqil ishlayveradi!
