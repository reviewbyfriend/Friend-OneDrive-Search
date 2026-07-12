# เริ่มจากไฟล์นี้ — Railway

เวอร์ชันนี้แก้เรื่อง 502 โดยให้แอปฟังพอร์ตที่ Railway ส่งมา (`$PORT`) และมี fallback เป็น 8080

1. ลบไฟล์เก่าใน GitHub repository แล้วอัปโหลด **ไฟล์ด้านใน ZIP นี้ทั้งหมด** ไปไว้หน้าแรกของ repo
2. ที่หน้าแรก GitHub ต้องเห็น `app`, `Dockerfile`, `railway.json`, `requirements.txt`
3. Railway จะ redeploy อัตโนมัติ
4. Settings → Networking → แก้ Target Port เป็น `8080`
5. เปิด `https://โดเมนของเธอ/health` ก่อน ต้องเห็น `{"ok":true,...}`
6. จากนั้นใส่ Variables:
   - MICROSOFT_CLIENT_ID
   - MICROSOFT_CLIENT_SECRET
   - MICROSOFT_TENANT=consumers
   - REDIRECT_URI=https://โดเมนของเธอ/auth/callback
   - SESSION_SECRET=ข้อความสุ่มยาว
   - DATA_DIR=/data
7. Microsoft Entra → Authentication → Web → เพิ่ม Redirect URI เดียวกัน
8. เพิ่ม Railway Volume mount `/data`

หมายเหตุ: อย่าตั้ง Custom Start Command ใน Railway เพราะ Dockerfile จัดการให้แล้ว
