# ===================================================================
#  vision.py — ระบบตรวจจับด้วยภาพ
#    - หามอนจากป้าย "Lv." (multi-scale, ไม่ขึ้นกับสี)
#    - อ่านสีตัวมอน (กรองพื้นหลังออก)
#    - จำสีปกติ + จับตัวที่สีแปลก (= shiny)
# ===================================================================

import os
import json
import cv2
import numpy as np

import config
from respath import userfile


# ---------- "ความขาว" ของพิกเซล (ตัวอักษร Lv สีขาว = สูง, พื้นส้ม = ต่ำ) ----------
def whiteness(img):
    b, g, r = cv2.split(img)
    return cv2.min(cv2.min(b, g), r)


# ---------- หาป้าย Lv. แบบหลายสเกล (เน้นตัวอักษรขาว ตัดพื้นส้มทิ้ง) ----------
# รับ template ตัวเดียวหรือเป็น list (เรียนเพิ่มได้จาก templates/lv/ ด้วย learn_mobs.py)
def locate_lv(frame, lv_templates):
    if not isinstance(lv_templates, (list, tuple)):
        lv_templates = [lv_templates]
    wf = whiteness(frame)

    best = (0.0, None, 1.0)   # (score, (x,y,w,h), scale)
    for tpl in lv_templates:
        if tpl is None:
            continue
        wt = whiteness(tpl)
        th0, tw0 = wt.shape[:2]
        for scale in config.LV_SCALES:
            tw, th = int(tw0 * scale), int(th0 * scale)
            if tw < 8 or th < 8 or tw > wf.shape[1] or th > wf.shape[0]:
                continue
            t = cv2.resize(wt, (tw, th))
            res = cv2.matchTemplate(wf, t, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            if mv > best[0]:
                best = (mv, (ml[0], ml[1], tw, th), scale)
        # เจอชัดแล้ว -> ไม่ต้องลอง template ที่เหลือ (ประหยัดเวลาต่อเฟรมมาก)
        if best[0] >= config.LV_EARLY_EXIT:
            break
    return best


# ---------- คะแนน match สูงสุดแบบหลายสเกล (ใช้ตรวจป้าย TIME) ----------
def best_score(frame, template, scales):
    if template is None:
        return 0.0
    gf = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gt = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    th0, tw0 = gt.shape[:2]
    best = 0.0
    for s in scales:
        tw, th = int(tw0 * s), int(th0 * s)
        if tw < 8 or th < 8 or tw > gf.shape[1] or th > gf.shape[0]:
            continue
        t = cv2.resize(gt, (tw, th))
        res = cv2.matchTemplate(gf, t, cv2.TM_CCOEFF_NORMED)
        _, mv, _, _ = cv2.minMaxLoc(res)
        if mv > best:
            best = mv
    return best


# ---------- อ่านสี hue เด่นของตัวมอน (กรองพื้นหลัง) ----------
def monster_hue(frame, lv_box):
    x, y, w, h = lv_box
    # ตัวมอนอยู่ "ใต้" ป้าย Lv. — กำหนดกรอบ body แบบสัมพันธ์กับขนาดป้าย
    cx = x + int(w * config.BODY_OFFSET_X)
    cy = y + int(w * config.BODY_OFFSET_Y)
    half = int(w * config.BODY_SIZE)
    x0, x1 = max(cx - half, 0), min(cx + half, frame.shape[1])
    y0, y1 = max(cy - half, 0), min(cy + half, frame.shape[0])
    if x1 <= x0 or y1 <= y0:
        return None, (x0, y0, x1, y1)

    roi = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3)

    # เก็บเฉพาะพิกเซลที่ "มีสีจริง" (sat/val พอ) และไม่ใช่สีพื้นหลัง
    sat, val, hue = hsv[:, 1], hsv[:, 2], hsv[:, 0]
    keep = (sat > config.MIN_SAT) & (val > config.MIN_VAL)
    lo, hi = config.BG_HUE_BAND
    keep &= ~((hue >= lo) & (hue <= hi))     # ตัดสีพื้นดิน
    h_keep = hue[keep]
    if len(h_keep) < config.MIN_BODY_PIXELS:
        return None, (x0, y0, x1, y1)

    hist = np.bincount(h_keep, minlength=180)
    # เฉลี่ยรอบ bin เด่นเพื่อความนิ่ง
    dom = int(hist.argmax())
    return dom, (x0, y0, x1, y1)


# ---------- ระยะห่าง hue แบบวงกลม (0-179) ----------
def hue_dist(a, b):
    d = abs(int(a) - int(b))
    return min(d, 180 - d)


# ---------- ความจำสีปกติ (เซฟลงไฟล์) ----------
class NormalColors:
    def __init__(self, path=None):
        self.path = path or userfile("normals.json")
        self.hues = []
        if os.path.exists(self.path):
            try:
                self.hues = json.load(open(self.path))
            except Exception:
                self.hues = []

    def save(self):
        try:
            json.dump(self.hues, open(self.path, "w"))
        except Exception:
            pass

    def is_known_normal(self, hue):
        return any(hue_dist(hue, k) <= config.NORMAL_MERGE_DIST for k in self.hues)

    def learn(self, hue):
        """ถ้าสีนี้ใกล้สีปกติเดิม = ไม่ทำอะไร, ถ้าใหม่แต่ 'น่าจะปกติ' = เพิ่ม"""
        if not self.is_known_normal(hue):
            self.hues.append(int(hue))
            self.save()
            return True
        return False

    def is_shiny(self, hue):
        """สีห่างจากสีปกติทุกตัวเกิน threshold = น่าจะ shiny"""
        if not self.hues:
            return False     # ยังไม่รู้จักสีปกติเลย -> อย่าเพิ่งฟันธง
        return all(hue_dist(hue, k) > config.SHINY_HUE_DIST for k in self.hues)
