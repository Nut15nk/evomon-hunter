# ===================================================================
#  ocr.py — อ่านตัวอักษรด้วย RapidOCR (เฉพาะ "อ่าน" rec-only ไม่ใช้ det)
#
#  ทำไม rec-only:
#    - det model (หากล่องข้อความทั้งจอ) บนเครื่องนี้ช้ามาก (~10-20 วิ/เฟรม)
#    - แต่ "อ่าน" บรรทัดที่ครอปมาให้แล้ว เร็ว ~30-300ms
#  เลยครอปแผง Obtain Rate (มุมซ้ายล่าง ตำแหน่งคงที่) เองแล้วส่งให้ rec อ่าน
#
#  ใช้ตรวจ shiny/prismatic:
#    - แถว Shiny ค่าเป็น "--"      -> SHINY
#    - แถว Prismatic ค่าเป็น "--"  -> PRISMATIC
#    - ค่าเป็นตัวเลขทั้งคู่          -> normal
# ===================================================================

import os
import threading

import config

_engine = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR(intra_op_num_threads=os.cpu_count())
    return _engine


def warmup():
    """โหลดโมเดล + รันรอบแรก (ช้าครั้งเดียว) — เรียกใน thread ตอน start จะได้ไม่สะดุด"""
    try:
        import numpy as np
        dummy = np.zeros((32, 120, 3), dtype=np.uint8)
        _rec(dummy)
    except Exception:
        pass


def _rec(im):
    """อ่านข้อความบรรทัดเดียว (rec-only) -> string (ว่างถ้าไม่เจอ)"""
    try:
        res, _ = _get_engine()(im, use_det=False, use_cls=False, use_rec=True)
        return res[0][0] if res else ""
    except Exception:
        return ""


def _is_dash(text):
    """ค่าของแถวเป็น '--' ไหม (= ตัวพิเศษ)"""
    s = text.lower().replace(" ", "")
    return ("--" in s) or ("—" in s) or ("−" in s) or s.count("-") >= 2


def read_obtain(frame):
    """
    ครอปแถว Shiny/Prismatic จากแผง Obtain Rate แล้วอ่านด้วย OCR
    คืน: "shiny" / "prismatic" / "normal" / None (ไม่ได้อยู่หน้าแผง)
    """
    H, W = frame.shape[:2]

    def crop(c):
        x0, x1, y0, y1 = c
        return frame[int(H * y0):int(H * y1), int(W * x0):int(W * x1)]

    st = _rec(crop(config.OCR_SHINY_FRAC))      # บรรทัด Shiny
    pt = _rec(crop(config.OCR_PRIS_FRAC))       # บรรทัด Prismatic
    sl, pl = st.lower(), pt.lower()

    # ยืนยันว่าอยู่หน้าแผงจริง (อ่านเจอคำ/เปอร์เซ็นต์/ขีด) ไม่งั้นถือว่าไม่ใช่
    looks_panel = ("hiny" in sl) or ("rismat" in pl) or ("%" in st) or ("--" in st)
    if not looks_panel:
        return None
    if _is_dash(st):
        return "shiny"
    if _is_dash(pt):
        return "prismatic"
    return "normal"
