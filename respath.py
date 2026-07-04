# ===================================================================
#  respath.py — หา path ของไฟล์ให้ถูก ทั้งตอนรันเป็น .py และเป็น .exe
#
#  resource(name) : ไฟล์อ่านอย่างเดียว (เช่น monster_lv.png)
#                   - ถ้ามีไฟล์วางข้างๆ exe จะใช้ตัวนั้นก่อน (ผู้ใช้แทนได้)
#                   - ไม่งั้นใช้ตัวที่ฝังมาใน exe
#  userfile(name) : ไฟล์ที่ต้องเขียนได้ (normals.json, history.json)
#                   - อยู่ข้างๆ exe เสมอ เพื่อให้เก็บค่าถาวร
# ===================================================================

import os
import sys


def app_dir():
    """โฟลเดอร์ของ exe (ตอน frozen) หรือของโปรเจกต์ (ตอนรัน .py)"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _bundle_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", app_dir())
    return app_dir()


def resource(name):
    ext = os.path.join(app_dir(), name)
    if os.path.exists(ext):
        return ext
    return os.path.join(_bundle_dir(), name)


def userfile(name):
    return os.path.join(app_dir(), name)
