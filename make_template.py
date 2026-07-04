# ===================================================================
#  เครื่องมือ crop ภาพมาทำ template
#
#  วิธีใช้ (เซฟภาพหน้าจอเกมไว้ก่อน แล้วรันแบบนี้):
#
#    py -3.12 make_template.py shiny.png   monster_lv.png      # ป้าย "Lv." ของมอน
#    py -3.12 make_template.py battle.png  battle_ui.png       # UI ฉากต่อสู้
#    py -3.12 make_template.py shiny.png   shiny_template.png  # คำ "Shiny"
#
#    arg1 = ไฟล์ภาพต้นฉบับ   arg2 = ชื่อไฟล์ template ที่จะบันทึก
#
#  ในหน้าต่างที่เปิดขึ้น: ลากเมาส์คลุมส่วนที่ต้องการ -> กด ENTER (กด c = ยกเลิก)
# ===================================================================

import os
import sys
import cv2

if len(sys.argv) < 3:
    print("วิธีใช้: py -3.12 make_template.py <ภาพต้นฉบับ> <ชื่อ template ที่จะบันทึก>")
    print("ตัวอย่าง: py -3.12 make_template.py shiny.png shiny_template.png")
    sys.exit(1)

SRC, OUT = sys.argv[1], sys.argv[2]

if not os.path.exists(SRC):
    print(f"[!] ไม่พบไฟล์ {SRC}")
    sys.exit(1)

img = cv2.imread(SRC)
if img is None:
    print(f"[!] อ่านไฟล์ {SRC} ไม่ได้")
    sys.exit(1)

print(f"ต้นฉบับ: {SRC}  ->  จะบันทึกเป็น: {OUT}")
print("ลากเมาส์คลุมส่วนที่ต้องการ -> กด ENTER/SPACE ยืนยัน (c = ยกเลิก)")

# ย่อภาพถ้าใหญ่เกินจอ
h, w = img.shape[:2]
scale = 1.0
max_w = 1280
if w > max_w:
    scale = max_w / w
    disp = cv2.resize(img, (int(w * scale), int(h * scale)))
else:
    disp = img

roi = cv2.selectROI(f"เลือกพื้นที่ -> {OUT}", disp, showCrosshair=True)
cv2.destroyAllWindows()

x, y, rw, rh = roi
if rw == 0 or rh == 0:
    print("[!] ไม่ได้เลือกพื้นที่ — ยกเลิก")
    sys.exit(0)

x, y, rw, rh = [int(v / scale) for v in (x, y, rw, rh)]
crop = img[y:y + rh, x:x + rw]
cv2.imwrite(OUT, crop)
print(f"[✓] บันทึก -> {OUT}  ({rw}x{rh} px)")
print(f"    ตำแหน่งบนจอจริง: left={x}, top={y}, width={rw}, height={rh}")
