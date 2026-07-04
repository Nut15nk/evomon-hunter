# ===================================================================
#  learn_mobs.py — "เรียนรู้" ป้าย Lv. จากภาพมอนจริง
#
#  วิธีใช้: เซฟภาพมอน (เห็นป้าย Lv. ชัดๆ) ไว้ในโฟลเดอร์นี้ แล้วรัน
#
#    py -3.12 learn_mobs.py                # เรียนจาก mob*.png ทั้งหมด
#    py -3.12 learn_mobs.py pig.png a.png  # หรือระบุไฟล์เอง
#
#  ทำอะไร:
#    - หาป้าย Lv. ในภาพด้วย template ที่มีอยู่
#    - ถ้า template เดิม "จำได้ดีอยู่แล้ว" (คะแนน >= 0.85) -> ข้าม ไม่เพิ่มซ้ำ
#    - ถ้าจำได้แค่พอเจอ -> ครอปป้ายจากภาพจริงเก็บเป็น template ใหม่
#      ใน templates/lv/ (บอทโหลดใช้เองอัตโนมัติ ไม่ต้องแก้โค้ด)
#    - ถ้าคะแนนต่ำจนหาตำแหน่งป้ายไม่เจอ -> บอกให้ครอปเองด้วย make_template.py
# ===================================================================

import glob
import os
import sys

# คอนโซล Windows บางเครื่องเป็น cp1252 พิมพ์ไทยไม่ได้
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2

import config
import vision
from respath import app_dir, resource

KNOWN_GOOD = 0.85     # คะแนนเท่านี้ขึ้นไป = จำได้ดีแล้ว ไม่ต้องเรียนเพิ่ม
MIN_LOCATE = 0.60     # ต่ำกว่านี้ = ไม่มั่นใจตำแหน่งป้าย ไม่กล้าครอปอัตโนมัติ

OUT_DIR = os.path.join(app_dir(), "templates", "lv")


def load_templates():
    tpls = []
    p = resource(config.MONSTER_TEMPLATE)
    if os.path.exists(p):
        tpls.append(("monster_lv.png", cv2.imread(p, cv2.IMREAD_COLOR)))
    if os.path.isdir(OUT_DIR):
        for f in sorted(os.listdir(OUT_DIR)):
            if f.lower().endswith(".png"):
                t = cv2.imread(os.path.join(OUT_DIR, f), cv2.IMREAD_COLOR)
                if t is not None:
                    tpls.append((f, t))
    return tpls


def next_name(existing):
    n = 1
    while f"lv{n}.png" in existing:
        n += 1
    return f"lv{n}.png"


def main():
    srcs = sys.argv[1:] or sorted(glob.glob(os.path.join(app_dir(), "mob*.png")))
    if not srcs:
        print("[!] ไม่พบภาพ — วางไฟล์ mob*.png ไว้ในโฟลเดอร์นี้ หรือระบุชื่อไฟล์เอง")
        return

    tpls = load_templates()
    if not tpls:
        print(f"[!] ไม่พบ {config.MONSTER_TEMPLATE} — ต้องมี template ตั้งต้นก่อน")
        return
    print(f"template ที่รู้จักตอนนี้: {len(tpls)} แบบ ({', '.join(n for n, _ in tpls)})")

    added = 0
    for src in srcs:
        img = cv2.imread(src, cv2.IMREAD_COLOR)
        name = os.path.basename(src)
        if img is None:
            print(f"  {name}: อ่านไฟล์ไม่ได้ — ข้าม")
            continue

        score, box, _ = vision.locate_lv(img, [t for _, t in tpls])
        if score >= KNOWN_GOOD:
            print(f"  {name}: จำได้ดีแล้ว (score {score:.2f}) — ไม่ต้องเรียนเพิ่ม")
            continue
        if score < MIN_LOCATE or box is None:
            print(f"  {name}: หาป้าย Lv. ไม่เจอ (score {score:.2f}) — "
                  f"ครอปเองด้วย: py -3.12 make_template.py {name} templates/lv/lv_new.png")
            continue

        x, y, w, h = box
        crop = img[y:y + h, x:x + w]
        os.makedirs(OUT_DIR, exist_ok=True)
        fname = next_name({n for n, _ in tpls})
        cv2.imwrite(os.path.join(OUT_DIR, fname), crop)
        tpls.append((fname, crop))
        added += 1
        print(f"  {name}: เรียนเพิ่ม (score {score:.2f}) -> templates/lv/{fname} ({w}x{h}px)")

    total = len(tpls)
    print(f"\n[✓] เสร็จ — เพิ่มใหม่ {added} แบบ, รวมทั้งหมด {total} แบบ")
    if total > config.LV_MAX_TEMPLATES:
        print(f"[!] เกินเพดาน LV_MAX_TEMPLATES={config.LV_MAX_TEMPLATES} "
              f"บอทจะใช้แค่ {config.LV_MAX_TEMPLATES} แบบแรก — ลบตัวที่ซ้ำใน templates/lv/ ออกได้")


if __name__ == "__main__":
    main()
