# ===================================================================
#  update.py — อัพเดตบอทเป็นเวอร์ชันล่าสุดจาก GitHub อัตโนมัติ
#
#  start.bat เรียกให้เองทุกครั้งที่เปิด (ออฟไลน์/เน็ตช้า = ข้ามเงียบๆ)
#  หรือรันเองก็ได้:  py -3.12 update.py
#
#  วิธีทำงาน:
#    1) เทียบไฟล์ VERSION ในเครื่อง กับบน GitHub
#    2) ถ้าไม่ตรงกัน -> โหลด zip ของ branch ล่าสุดมาทับไฟล์ในเครื่อง
#    3) ไฟล์ส่วนตัวไม่ถูกแตะ: history.json, normals.json, config_user.py
#
#  ฝั่งคนแจก: แก้โค้ดเสร็จ -> ขยับเลขใน VERSION -> git push
#  เพื่อนทุกคนได้เวอร์ชันใหม่เองตอนเปิดบอทครั้งถัดไป ไม่ต้องส่งไฟล์ให้
# ===================================================================

import io
import os
import sys
import zipfile
import urllib.request

# คอนโซล Windows บางเครื่องเป็น cp1252 พิมพ์ไทยไม่ได้
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = "OWNER/REPO"          # <-- แก้เป็น repo ของคุณ เช่น "Nut15nk/evomon-hunter"
BRANCH = "main"
TIMEOUT = 6                  # วินาที — เน็ตช้า/ออฟไลน์ = ยอมแพ้เร็วๆ ไม่ให้บอทเปิดช้า

# ไฟล์ส่วนตัวของแต่ละเครื่อง — ห้ามทับตอนอัพเดต
PROTECTED = {"history.json", "normals.json", "config_user.py"}

APP = os.path.dirname(os.path.abspath(__file__))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "evomon-updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def local_version():
    try:
        return open(os.path.join(APP, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return "0"


def main():
    if REPO == "OWNER/REPO":
        print("[i] ยังไม่ได้ตั้ง repo สำหรับอัพเดต (แก้ REPO ใน update.py) — ข้าม")
        return

    cur = local_version()
    try:
        remote = fetch(f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/VERSION").decode().strip()
    except Exception:
        print("[i] เช็คอัพเดตไม่ได้ (ออฟไลน์?) — ใช้เวอร์ชันเดิม " + cur)
        return

    if remote == cur:
        print(f"[✓] เป็นเวอร์ชันล่าสุดแล้ว ({cur})")
        return

    print(f"[*] พบเวอร์ชันใหม่ {cur} -> {remote} กำลังโหลด...")
    try:
        data = fetch(f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}")
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:
        print(f"[!] โหลดอัพเดตไม่สำเร็จ ({e}) — ใช้เวอร์ชันเดิมไปก่อน")
        return

    root = zf.namelist()[0]                    # โฟลเดอร์ชั้นนอกใน zip เช่น "repo-main/"
    old_req = ""
    req_path = os.path.join(APP, "requirements.txt")
    if os.path.exists(req_path):
        old_req = open(req_path, encoding="utf-8", errors="ignore").read()

    changed = 0
    for info in zf.infolist():
        rel = info.filename[len(root):]
        if not rel or info.is_dir():
            continue
        base = os.path.basename(rel)
        if base in PROTECTED:
            continue
        target = os.path.join(APP, rel.replace("/", os.sep))
        content = zf.read(info)

        # start.bat กำลังรันอยู่ ทับตรงๆ ไม่ได้ -> วางเป็น .new ให้ start.bat สลับเอง
        if base.lower() == "start.bat" and os.path.exists(target):
            if open(target, "rb").read() != content:
                open(target + ".new", "wb").write(content)
                print("    start.bat มีเวอร์ชันใหม่ -> จะสลับให้ตอนเปิดครั้งถัดไป")
            continue

        if os.path.dirname(rel):
            os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(content)
        changed += 1

    # dependency เปลี่ยน -> ลงเพิ่มให้เลย
    new_req = ""
    if os.path.exists(req_path):
        new_req = open(req_path, encoding="utf-8", errors="ignore").read()
    if new_req != old_req:
        print("[*] requirements.txt เปลี่ยน — กำลังลง dependency เพิ่ม...")
        os.system(f'"{sys.executable}" -m pip install -r "{req_path}"')

    print(f"[✓] อัพเดตเป็นเวอร์ชัน {remote} แล้ว ({changed} ไฟล์)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                     # ห้ามล้มจนบอทเปิดไม่ได้
        print(f"[!] ตัวอัพเดตมีปัญหา ({e}) — ข้าม ใช้เวอร์ชันเดิม")
