# Friend OneDrive Search v1.0

ระบบค้นหาไฟล์ OneDrive แบบ Read Only

## ฟีเจอร์

- เชื่อม Microsoft ครั้งเดียว
- Auto Sync ทุก 10 นาที
- ใช้ Delta API อ่านเฉพาะไฟล์ที่เพิ่ม แก้ไข หรือลบ
- เก็บรายการทุกไฟล์ใน OneDrive
- ทุกไฟล์ค้นได้จากชื่อไฟล์และชื่อโฟลเดอร์
- `.docx`, `.xlsx`, `.pdf`, `.txt`, `.csv` ค้นข้อความภายในได้
- `.doc`, `.xls`, รูปภาพ, ZIP และไฟล์อื่น เปิดลิงก์ OneDrive ได้
- มีปุ่มตรวจไฟล์ทันทีและสแกนใหม่ทั้งหมด
- หน้า `/health` ใช้ตรวจ Railway healthcheck

## อัปโหลด GitHub

หน้าแรก repository ต้องเห็น:

```text
app/
Dockerfile
railway.json
requirements.txt
README.md
```

อย่าอัปโหลดให้มีโฟลเดอร์ซ้อนอีกชั้น

## Railway Variables

```text
MICROSOFT_CLIENT_ID=Application client ID
MICROSOFT_CLIENT_SECRET=Client secret VALUE
MICROSOFT_TENANT=consumers
REDIRECT_URI=https://friend-onedrive-search-production.up.railway.app/auth/callback
SESSION_SECRET=ข้อความสุ่มยาวจริง
DATA_DIR=/data
MAX_FILE_MB=30
AUTO_SYNC_MINUTES=10
SYNC_EXTENSIONS=.docx,.xlsx,.pdf,.txt,.csv
```

## Railway Volume

Mount path:

```text
/data
```

## Railway Networking

Generate Domain แล้วใช้ target port:

```text
8080
```

## Microsoft Entra

Authentication → Web Redirect URI:

```text
https://friend-onedrive-search-production.up.railway.app/auth/callback
```

Delegated permissions:

```text
Files.Read
User.Read
offline_access
```

## หลัง Deploy

1. เปิด `/health`
2. ต้องเห็น `"ok": true`
3. เปิดหน้าเว็บหลัก
4. กดเชื่อม Microsoft
5. ครั้งแรกกด “สแกนใหม่ทั้งหมด” หนึ่งครั้ง
6. หลังจากนั้นระบบตรวจอัตโนมัติ
