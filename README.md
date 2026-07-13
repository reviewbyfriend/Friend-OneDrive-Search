# Friend OneDrive Search v2 — Hybrid Live Search

ระบบค้นหาแบบ 2 ชั้น:

1. ค้นสดผ่าน Microsoft Graph Search API (`POST /search/query`) จาก OneDrive/SharePoint
2. รวมผลกับฐาน SQLite/OCR เดิม เพื่อช่วยค้น PDF สแกนหรือไฟล์ที่เคยอ่านข้อความไว้

ไม่ต้องรอให้ระบบดาวน์โหลดและทำดัชนีครบ 30,000 ไฟล์ก่อนใช้งาน

## Microsoft Entra delegated permissions

- User.Read
- Files.Read.All
- Sites.Read.All
- offline_access (MSAL จัดการให้)

หลังอัปเดตโค้ด ให้เปิด `/login` และยอมรับสิทธิ์ใหม่หนึ่งครั้ง

## Railway variables

ใช้ค่าเดิม:

- MICROSOFT_CLIENT_ID
- MICROSOFT_CLIENT_SECRET
- MICROSOFT_TENANT (`consumers` สำหรับบัญชี Hotmail ส่วนตัว)
- REDIRECT_URI
- SESSION_SECRET
- DATA_DIR=/data

ตัวเลือก:

- `ENABLE_BACKGROUND_INDEX=false` (ค่าเริ่มต้น แนะนำ)
- ตั้งเป็น `true` เฉพาะเมื่ออยากให้ระบบเดิมดาวน์โหลด/OCR ไฟล์เพิ่มเบื้องหลัง

## หมายเหตุ

Microsoft Search จะพบเฉพาะเนื้อหาที่ Microsoft ทำดัชนีได้ ส่วนรูปภาพ/PDF สแกนที่ไม่มี text ต้องอาศัย OCR ของฐานเดิมหรือระบบ OCR เฉพาะไฟล์ในอนาคต
