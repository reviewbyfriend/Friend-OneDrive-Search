# Friend OneDrive Search — MVP 0.1

เว็บค้นหาคำในเนื้อหาไฟล์ OneDrive โดยเก็บไฟล์จริงไว้ที่ OneDrive ตามเดิม  
ระบบใช้สิทธิ์ **อ่านอย่างเดียว (`Files.Read`)** ไม่แก้ ลบ ย้าย หรือเปลี่ยนชื่อไฟล์

## รองรับรอบแรก

- Word `.docx`
- Excel `.xlsx`
- PDF ที่มี text layer
- `.txt` และ `.csv`
- ค้นชื่อไฟล์ + ชื่อโฟลเดอร์ + เนื้อหา
- กดเปิดไฟล์ต้นฉบับใน OneDrive
- Delta Sync: หลังสแกนครั้งแรกจะอ่านเฉพาะรายการที่เปลี่ยน

ยังไม่รองรับ `.doc` รุ่นเก่า, `.xls` รุ่นเก่า และ PDF/รูปสแกนที่ต้อง OCR

---

## ขั้นที่ 1: สร้าง Microsoft App Registration

1. เข้า Microsoft Entra admin center: `https://entra.microsoft.com`
2. ไปที่ **App registrations → New registration**
3. Name: `Friend OneDrive Search`
4. Supported account types เลือก  
   **Personal Microsoft accounts only**  
   (ถ้าจะรองรับทั้งบัญชีงานและส่วนตัว ให้เลือกบัญชีทุกองค์กรและ personal)
5. ยังไม่ต้องกรอก Redirect URI แล้วกด Register
6. จด **Application (client) ID**
7. ไปที่ **Certificates & secrets → New client secret**
8. จดค่าในช่อง **Value** ทันที
9. ไปที่ **API permissions → Add a permission → Microsoft Graph → Delegated permissions**
10. เพิ่ม:
    - `Files.Read`
    - `User.Read`
    - `offline_access`

ห้ามเพิ่ม `Files.ReadWrite` เพราะระบบนี้ต้อง Read Only

---

## ขั้นที่ 2: ทดสอบในเครื่อง (ถ้ามีเครื่องที่รัน Python ได้)

คัดลอก `.env.example` เป็น `.env` แล้วใส่:

```env
MICROSOFT_CLIENT_ID=...
MICROSOFT_CLIENT_SECRET=...
MICROSOFT_TENANT=consumers
REDIRECT_URI=http://localhost:8000/auth/callback
SESSION_SECRET=ข้อความสุ่มยาวๆ
DATA_DIR=./data
```

เพิ่ม Redirect URI ใน Entra:

`http://localhost:8000/auth/callback`

แล้วรัน:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

เปิด `http://localhost:8000`

---

## ขั้นที่ 3: ขึ้น Railway

1. สร้าง GitHub repository แล้วอัปโหลดไฟล์ทั้งหมดใน ZIP นี้
2. Railway → New Project → Deploy from GitHub
3. เพิ่ม Volume และ mount ที่ `/data`
4. เพิ่ม Variables:
   - `MICROSOFT_CLIENT_ID`
   - `MICROSOFT_CLIENT_SECRET`
   - `MICROSOFT_TENANT=consumers`
   - `SESSION_SECRET` เป็นข้อความสุ่มยาว
   - `DATA_DIR=/data`
   - `MAX_FILE_MB=30`
5. Generate Domain ใน Railway เช่น  
   `https://friend-search-production.up.railway.app`
6. ตั้ง Variable:
   `REDIRECT_URI=https://friend-search-production.up.railway.app/auth/callback`
7. กลับไป Entra → Authentication → Add a platform → Web
8. เพิ่ม Redirect URI เดียวกันแบบตรงตัวทุกตัวอักษร
9. เปิดเว็บ → กด **เชื่อมบัญชี Microsoft**
10. กด **ซิงก์ไฟล์ที่เปลี่ยน** ครั้งแรกจะเป็น Full Scan

---

## ความปลอดภัยสำคัญ

- อย่าใส่ Client Secret ลง GitHub
- Railway ควรมี Volume `/data` ไม่เช่นนั้นฐานข้อมูลและ token จะหายเมื่อ redeploy
- เว็บรุ่นนี้เหมาะกับการใช้ส่วนตัว ควรเก็บ URL ไว้เฉพาะตัว
- ก่อนใช้กับเอกสารราชการจริง ควรเพิ่ม PIN/Login หน้าเว็บ และพิจารณานโยบายหน่วยงานเรื่องการส่งข้อมูลขึ้น cloud
- ไฟล์ถูกดาวน์โหลดชั่วคราวในหน่วยความจำเพื่อแยกข้อความ แต่ไม่ได้เก็บสำเนาไฟล์ถาวร
- ฐานข้อมูล `/data/search.db` จะเก็บข้อความที่สกัดจากเอกสารเพื่อใช้ค้นหา

## เวอร์ชันต่อไป

- ตั้งเวลาซิงก์อัตโนมัติ
- PIN หรือ Login ป้องกันหน้าค้นหา
- OCR PDF/ภาพสแกน
- รองรับ `.doc` และ `.xls`
- กรองตามปี/ชนิดไฟล์/โฟลเดอร์
- ไฮไลต์หลายจุดและแบ่งหน้า
- เสียบเป็นเมนูใน Friend AI Agent
