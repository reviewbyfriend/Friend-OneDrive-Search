# Friend OneDrive Secure Portal v0.2

เว็บแฟลชไดรฟ์ออนไลน์ส่วนตัว เชื่อม OneDrive แบบ Read Only

## ฟีเจอร์
- เจ้าของระบบ Login แยก
- เชื่อม OneDrive ด้วย Microsoft Graph `Files.Read`
- ทำดัชนีข้อความจาก `.docx`, `.xlsx`, `.pdf`, `.txt`, `.csv`
- สร้างรหัสชั่วคราว 6 หลัก
- กำหนดอายุเป็นชั่วโมงหรือวัน
- จำกัดขอบเขต: ทั้งหมด / เฉพาะโฟลเดอร์ / เฉพาะไฟล์
- เลือกได้ว่าค้นหาได้หรือดาวน์โหลดได้
- จำกัดจำนวนดาวน์โหลด
- ปิดรหัสก่อนหมดอายุได้
- บันทึก login, search, download, IP และเวลา
- ผู้รับไม่ต้องมีบัญชี Microsoft

## ข้อจำกัด
- `.doc`, `.xls` และ PDF สแกนยังไม่รองรับ
- เวอร์ชันนี้เหมาะกับเจ้าของระบบ 1 คน
- เมื่อผู้รับดาวน์โหลดไฟล์แล้ว ระบบไม่สามารถลบไฟล์จากเครื่องผู้รับ
- ก่อนใช้กับเอกสารราชการจริง ควรตรวจสอบนโยบายหน่วยงานและจำกัดโฟลเดอร์ที่แชร์

## Railway
1. อัปไฟล์ขึ้น GitHub
2. Railway → Deploy from GitHub
3. เพิ่ม Volume mount `/data`
4. ตั้ง Variables:
   - `MICROSOFT_CLIENT_ID`
   - `MICROSOFT_CLIENT_SECRET`
   - `MICROSOFT_TENANT=consumers`
   - `REDIRECT_URI=https://โดเมนของคุณ/auth/callback`
   - `SESSION_SECRET=ข้อความสุ่มยาว`
   - `OWNER_USERNAME=friend`
   - `OWNER_PASSWORD=รหัสเจ้าของที่แข็งแรง`
   - `DATA_DIR=/data`
   - `PUBLIC_BASE_URL=https://โดเมนของคุณ`
5. Microsoft Entra App Registration:
   - Delegated permissions: `Files.Read`, `User.Read`, `offline_access`
   - Redirect URI ต้องตรงกับ Railway ทุกตัวอักษร
6. เปิดเว็บ → Login เจ้าของ → เชื่อม OneDrive → กดซิงก์

## การตั้งขอบเขต
- Folder: ใช้ path เช่น `/Friend Search/แบบฟอร์มกลาง`
- File: คัดลอก Item ID จากตารางไฟล์ในหน้า Admin
- แนะนำไม่ใช้ `all` สำหรับบุคคลภายนอก

## ความปลอดภัย
- Client Secret และ OWNER_PASSWORD ต้องใส่ใน Railway Variables เท่านั้น
- ห้าม commit `.env`
- รหัสผู้รับถูกเก็บเป็น hash
- ระบบใช้สิทธิ์อ่านอย่างเดียวกับ OneDrive
