# ===================================================================
#  main.py — รันบอทแบบ CLI (ไม่มีหน้าต่าง)
#  ถ้าอยากได้ UI ใช้:  py -3.12 gui.py
#
#  F8 = หยุด | Ctrl+C = ออก
# ===================================================================

import time
import sys

from bot import Bot


def main():
    b = Bot()
    print("=" * 50)
    print(" Evomon Auto-Hunter (CLI) — UI ใช้ py -3.12 gui.py")
    print(f" สกิลหลัก {b.settings.primary_skill} | สกิล4 {b.settings.use_skill4} | ไม่จับเอง (แจ้งเตือน shiny)")
    print(" F8 = หยุด | Ctrl+C = ออก")
    print("=" * 50)
    b.start()

    try:
        while True:
            try:
                kind, data = b.q.get(timeout=0.3)
                if kind == "log":
                    print(data)
                elif kind == "shiny":
                    print(f"\n[★★★] เจอตัวสีแปลก hue={data} — น่าจะ SHINY/PRISMATIC! หยุดแล้ว")
                    print("    ไปจัดการในเกมได้เลย (CLI ไม่บันทึกประวัติ — ใช้ gui.py ถ้าต้องการ list)")
            except Exception:
                pass
            if not b.is_running() and b._thread and not b._thread.is_alive():
                # บอทจบเอง (เช่นเจอ shiny) — รอผู้ใช้กด Ctrl+C
                time.sleep(0.2)
    except KeyboardInterrupt:
        b.stop()
        print("\n[*] ออกโปรแกรม")


if __name__ == "__main__":
    main()
