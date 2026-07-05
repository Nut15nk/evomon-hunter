# ===================================================================
#  vision.py — ระบบตรวจจับด้วยภาพ
#    - หามอนจากป้าย "Lv." (multi-scale, ไม่ขึ้นกับสี)
#    - อ่านสีตัวมอน (กรองพื้นหลังออก)
#    - จำสีปกติ + จับตัวที่สีแปลก (= shiny)
#    - detect_all(): รันตรวจจับหลายอย่าง "พร้อมกัน" (multi-thread) ต่อเฟรม
# ===================================================================

import os
import json
import threading
import concurrent.futures

import cv2
import numpy as np

import config
from respath import userfile


# เธรดพูลกลาง ใช้รันการตรวจจับหลายชนิดพร้อมกันต่อ 1 เฟรม
# (cv2.matchTemplate ปล่อย GIL ระหว่างคำนวณ -> รันขนานได้จริง ไม่ใช่แค่สลับคิว)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4,
                                                   thread_name_prefix="vision")

# ===================================================================
#  GPU accel ผ่าน OpenCL (T-API / cv2.UMat)
#  -------------------------------------------------------------------
#  หมายเหตุสำคัญ: opencv-python ที่ติดตั้งผ่าน pip ปกติ "ไม่มี" CUDA เลย
#  ต่อให้เครื่องมี GPU NVIDIA ก็ตาม (ต้อง build opencv เองจาก source ถึงจะได้
#  CUDA ซึ่งยุ่งยากมาก) แต่ opencv-python ที่ติดตั้งปกตินี้ "มี" OpenCL support
#  ในตัวอยู่แล้ว (ผ่าน Transparent API / cv2.UMat) ซึ่งใช้ได้กับ GPU ที่มีอยู่
#  แล้วในเครื่อง (NVIDIA/AMD/Intel iGPU) โดยอาศัย driver เดิมที่ลงไว้แล้ว
#  ไม่ต้องติดตั้งอะไรเพิ่ม -- ตรงนี้คือส่วนที่เอา matchTemplate (จุดที่หนักสุด)
#  ไปรันผ่าน OpenCL ให้อัตโนมัติถ้าเครื่องรองรับ ถ้าไม่รองรับ/พังก็ fallback
#  กลับไปรันบน CPU เหมือนเดิมเงียบๆ ไม่กระทบการทำงาน
# ===================================================================
_OCL_WANTED = getattr(config, "USE_OPENCL", True)
GPU_ENABLED = False
GPU_DEVICE_NAME = None
if _OCL_WANTED:
    try:
        if cv2.ocl.haveOpenCL():
            cv2.ocl.setUseOpenCL(True)
            GPU_ENABLED = bool(cv2.ocl.useOpenCL())
            if GPU_ENABLED:
                try:
                    dev = cv2.ocl.Device_getDefault()
                    GPU_DEVICE_NAME = dev.name()
                except Exception:
                    GPU_DEVICE_NAME = "OpenCL device"
    except Exception:
        GPU_ENABLED = False


def gpu_status_text():
    """ข้อความสถานะ GPU สำหรับ log ตอนเริ่มบอท"""
    if GPU_ENABLED:
        return f"GPU (OpenCL: {GPU_DEVICE_NAME})"
    return "CPU (ไม่พบ/ไม่ได้เปิดใช้ OpenCL -- ยังทำงานได้ปกติแค่ช้ากว่า)"


def _um(a):
    """ห่อภาพเป็น UMat เพื่อให้ opencv ส่งไปรันบน GPU ผ่าน OpenCL อัตโนมัติ
    (เฉพาะตอน GPU_ENABLED เท่านั้น ไม่งั้นคืนของเดิมเป็น numpy array ปกติ)
    ถ้าเป็น UMat อยู่แล้วคืนตัวเดิมเลย -- กัน upload ซ้ำเวลาเรียกซ้ำในลูปสเกล"""
    if GPU_ENABLED:
        if isinstance(a, cv2.UMat):
            return a
        try:
            return cv2.UMat(a)
        except Exception:
            return a
    return a


def _to_np(a):
    """แปลงกลับเป็น numpy array ปกติ (เผื่อเป็น UMat) -- ใช้ก่อนทำ numpy indexing"""
    if isinstance(a, cv2.UMat):
        return a.get()
    return a


# detect_all() ยิง locate_lv / locate_lv_multi / locate_player / best_score
# พร้อมกันคนละเธรด (ThreadPoolExecutor) -- แต่ cv2.UMat/OpenCL "ไม่ปลอดภัย" ที่จะ
# ให้หลายเธรดยิงเข้า GPU queue เดียวกันพร้อมกัน (ผลลัพธ์เพี้ยน/throw exception ได้
# ซึ่งถ้าไม่ดักไว้จะทำให้ detect_all() ทั้งก้อน error แล้วเธรดตรวจจับตายทั้งเธรด
# = ไม่สแกน LV ต่อเลย) -- ใช้ lock ตัวเดียวคั่นเฉพาะช่วง matchTemplate บน GPU
# กันชนกัน ส่วนพาธ CPU (ไม่มี lock) ยังรันขนานกันได้เต็มที่เหมือนเดิม
_gpu_lock = threading.Lock()


def _match_minmax(a, b):
    """matchTemplate + minMaxLoc เป็นก้อนเดียว ปลอดภัยเมื่อรันพร้อมกันหลายเธรด
    (ทั้ง matchTemplate และ minMaxLoc คุยกับ GPU queue เดียวกัน ต้อง lock คู่กัน
    ไม่งั้นชนกันได้ถ้าอีกเธรดแทรกเข้ามาระหว่างสองคำสั่งนี้)"""
    if GPU_ENABLED:
        with _gpu_lock:
            res = cv2.matchTemplate(_um(a), _um(b), cv2.TM_CCOEFF_NORMED)
            return cv2.minMaxLoc(res)
    res = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
    return cv2.minMaxLoc(res)


def _match_array(a, b):
    """matchTemplate แล้วคืนเป็น numpy array เต็มตาราง (ปลอดภัยเมื่อรันพร้อมกันหลายเธรด)
    ใช้ตอนต้องอ่านทุกจุดที่ผ่าน threshold (เช่น locate_lv_multi) ไม่ใช่แค่จุดที่ดีที่สุด"""
    if GPU_ENABLED:
        with _gpu_lock:
            res = cv2.matchTemplate(_um(a), _um(b), cv2.TM_CCOEFF_NORMED)
            return _to_np(res)
    return cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)


# ---------- "ความขาว" ของพิกเซล (ตัวอักษร Lv สีขาว = สูง, พื้นส้ม = ต่ำ) ----------
def whiteness(img):
    b, g, r = cv2.split(img)
    return cv2.min(cv2.min(b, g), r)


# ---------- หาป้าย Lv. แบบหลายสเกล (เน้นตัวอักษรขาว ตัดพื้นส้มทิ้ง) ----------
# รับ template ตัวเดียวหรือเป็น list (เรียนเพิ่มได้จาก templates/lv/ ด้วย learn_mobs.py)
def locate_lv(frame, lv_templates):
    if lv_templates is None:
        return 0.0, None, 1.0
    if not isinstance(lv_templates, (list, tuple)):
        lv_templates = [lv_templates]

    # ย่อภาพค้นหาลงก่อน (เร็วขึ้นมาก) แล้วห่อเป็น UMat ครั้งเดียว (ใช้ซ้ำได้ทุกสเกล/
    # ทุก template ด้านล่าง) -- ถ้าเครื่องมี GPU รองรับ OpenCL จะรันบน GPU อัตโนมัติ
    ds = getattr(config, "DETECT_SCALE", 1.0) or 1.0
    search = cv2.resize(frame, None, fx=ds, fy=ds,
                         interpolation=cv2.INTER_AREA) if ds < 1.0 else frame
    wf = whiteness(search)
    wf_u = _um(wf)

    best = (0.0, None, 1.0)   # (score, (x,y,w,h), scale) -- พิกัด/ขนาดเป็นสเกลเต็มเสมอ
    for tpl in lv_templates:
        if tpl is None:
            continue
        wt = whiteness(tpl)
        th0, tw0 = wt.shape[:2]
        for scale in config.LV_SCALES:
            tw, th = int(tw0 * scale * ds), int(th0 * scale * ds)
            if tw < 8 or th < 8 or tw > wf.shape[1] or th > wf.shape[0]:
                continue
            t = cv2.resize(wt, (tw, th))
            _, mv, _, ml = _match_minmax(wf_u, t)
            if mv > best[0]:
                # แปลงพิกัด/ขนาดกลับเป็นสเกลเต็ม (เผื่อ ds < 1.0)
                box = (int(ml[0] / ds), int(ml[1] / ds), int(tw / ds), int(th / ds))
                best = (mv, box, scale)
        # เจอชัดแล้ว -> ไม่ต้องลอง template ที่เหลือ (ประหยัดเวลาต่อเฟรมมาก)
        if best[0] >= config.LV_EARLY_EXIT:
            break
    return best


# ---------- กันกล่องซ้อนทับ (non-max suppression) แบบง่าย ----------
# ใช้ IoU (intersection-over-union) ตัดกล่องที่ทับกันมาก เหลือแค่คะแนนสูงสุดในกลุ่ม
def _nms(candidates, iou_thresh=0.3):
    # candidates: list ของ (score, (x,y,w,h))
    candidates = sorted(candidates, key=lambda c: c[0], reverse=True)
    kept = []
    for score, box in candidates:
        x0, y0, w0, h0 = box
        a0 = w0 * h0
        overlap = False
        for _, kbox in kept:
            x1, y1, w1, h1 = kbox
            ix0, iy0 = max(x0, x1), max(y0, y1)
            ix1, iy1 = min(x0 + w0, x1 + w1), min(y0 + h0, y1 + h1)
            iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
            inter = iw * ih
            if inter <= 0:
                continue
            a1 = w1 * h1
            iou = inter / float(a0 + a1 - inter)
            if iou > iou_thresh:
                overlap = True
                break
        if not overlap:
            kept.append((score, box))
    return kept


# ---------- หาป้าย Lv. "ทุกอัน" ในเฟรม (รองรับมอนหลายตัวพร้อมกัน) ----------
# ต่างจาก locate_lv ตรงที่ไม่หยุดแค่ตัวคะแนนสูงสุด แต่เก็บทุกตำแหน่งที่ผ่าน
# threshold แล้วตัดตัวซ้อนทับออกด้วย NMS -> ใช้เลือก "เป้าหมาย" ที่จะล็อกได้
def locate_lv_multi(frame, lv_templates, threshold=None):
    if lv_templates is None:
        return []
    if not isinstance(lv_templates, (list, tuple)):
        lv_templates = [lv_templates]
    thresh = threshold if threshold is not None else config.MONSTER_THRESHOLD

    ds = getattr(config, "DETECT_SCALE", 1.0) or 1.0
    search = cv2.resize(frame, None, fx=ds, fy=ds,
                         interpolation=cv2.INTER_AREA) if ds < 1.0 else frame
    wf = whiteness(search)
    wf_u = _um(wf)

    candidates = []   # (score, (x,y,w,h)) พิกัด/ขนาดสเกลเต็มเสมอ
    for tpl in lv_templates:
        if tpl is None:
            continue
        wt = whiteness(tpl)
        th0, tw0 = wt.shape[:2]
        for scale in config.LV_SCALES:
            tw, th = int(tw0 * scale * ds), int(th0 * scale * ds)
            if tw < 8 or th < 8 or tw > wf.shape[1] or th > wf.shape[0]:
                continue
            t = cv2.resize(wt, (tw, th))
            res = _match_array(wf_u, t)
            ys, xs = np.where(res >= thresh)
            for x, y in zip(xs, ys):
                score = float(res[y, x])
                box = (int(x / ds), int(y / ds), int(tw / ds), int(th / ds))
                candidates.append((score, box))

    if not candidates:
        return []

    # จำกัดจำนวนก่อน NMS กันเคสมี match เกินเยอะ (ช้าลงถ้าไม่ตัด)
    candidates.sort(key=lambda c: c[0], reverse=True)
    candidates = candidates[:200]

    targets = _nms(candidates, iou_thresh=0.3)
    targets.sort(key=lambda c: c[0], reverse=True)

    max_targets = getattr(config, "MAX_TARGETS", 6)
    return targets[:max_targets]


# ---------- หาป้ายชื่อผู้เล่น (anchor ตำแหน่งตัวเอง) ----------
# ค้นเฉพาะบริเวณกลางจอ (PLAYER_SEARCH_FRAC) — เร็ว + ไม่จับป้ายชื่อผู้เล่นอื่นขอบจอ
def locate_player(frame, tpl):
    if tpl is None:
        return 0.0, None
    H, W = frame.shape[:2]
    fx0, fx1, fy0, fy1 = config.PLAYER_SEARCH_FRAC
    x0, x1 = int(W * fx0), int(W * fx1)
    y0, y1 = int(H * fy0), int(H * fy1)
    sub = frame[y0:y1, x0:x1]
    if sub.shape[0] < 20 or sub.shape[1] < 20:   # ภาพเล็ก (เช่นตอนเทส) -> ค้นทั้งภาพ
        sub, x0, y0 = frame, 0, 0

    ds = getattr(config, "DETECT_SCALE", 1.0) or 1.0
    search = cv2.resize(sub, None, fx=ds, fy=ds,
                        interpolation=cv2.INTER_AREA) if ds < 1.0 else sub
    wf = whiteness(search)
    wf_u = _um(wf)
    wt = whiteness(tpl)
    th0, tw0 = wt.shape[:2]
    best = (0.0, None)
    for s in config.PLAYER_SCALES:
        tw, th = int(tw0 * s * ds), int(th0 * s * ds)
        if tw < 8 or th < 8 or tw > wf.shape[1] or th > wf.shape[0]:
            continue
        t = cv2.resize(wt, (tw, th))
        _, mv, _, ml = _match_minmax(wf_u, t)
        if mv > best[0]:
            # แปลงพิกัด/ขนาดกลับเป็นสเกลเต็ม (เผื่อ ds < 1.0) แล้วบวก offset ของ sub กลับ
            bx = x0 + int(ml[0] / ds)
            by = y0 + int(ml[1] / ds)
            best = (mv, (bx, by, int(tw / ds), int(th / ds)))
    return best


# ---------- คะแนน match สูงสุดแบบหลายสเกล (ใช้ตรวจป้าย TIME / Obtain Rate) ----------
def best_score(frame, template, scales):
    if template is None:
        return 0.0
    ds = getattr(config, "DETECT_SCALE", 1.0) or 1.0
    search = cv2.resize(frame, None, fx=ds, fy=ds,
                        interpolation=cv2.INTER_AREA) if ds < 1.0 else frame
    gf = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    gt = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    gf_u = _um(gf)
    th0, tw0 = gt.shape[:2]
    best = 0.0
    for s in scales:
        tw, th = int(tw0 * s * ds), int(th0 * s * ds)
        if tw < 8 or th < 8 or tw > gf.shape[1] or th > gf.shape[0]:
            continue
        t = cv2.resize(gt, (tw, th))
        _, mv, _, _ = _match_minmax(gf_u, t)
        if mv > best:
            best = mv
    return best


# ---------- ตรวจจับทุกอย่างที่ต้องใช้ต่อ 1 เฟรม "พร้อมกัน" (multi-thread) ----------
def detect_all(frame, lv_templates, player_tpl, battle_tpl, panel_tpl,
               battle_scales, panel_scales, need_target=True):
    """
    ยิงงานตรวจจับทั้งหมดลง thread pool พร้อมกันในเฟรมเดียว:
      - ป้าย Lv.        (หามอน)      -- ข้ามได้ถ้า need_target=False (เช่นตอน engaged)
      - ป้ายชื่อผู้เล่น  (anchor)     -- ข้ามได้เหมือนกัน
      - ป้าย battle (TIME)
      - แผง Obtain Rate (catch)

    คืน dict:
      lv_score, lv_box, player_score, player_box, battle_score, panel_score
    """
    futures = {
        "battle": _executor.submit(best_score, frame, battle_tpl, battle_scales),
        "panel": _executor.submit(best_score, frame, panel_tpl, panel_scales),
    }
    if need_target:
        futures["lv"] = _executor.submit(locate_lv, frame, lv_templates)
        futures["lv_multi"] = _executor.submit(locate_lv_multi, frame, lv_templates)
        futures["player"] = _executor.submit(locate_player, frame, player_tpl)

    battle_score = futures["battle"].result()
    panel_score = futures["panel"].result()

    if need_target:
        lv_score, lv_box, _ = futures["lv"].result()
        lv_targets = futures["lv_multi"].result()   # [(score, (x,y,w,h)), ...] ทุกตัวที่เจอ
        pl_score, pl_box = futures["player"].result()
    else:
        lv_score, lv_box = 0.0, None
        lv_targets = []
        pl_score, pl_box = 0.0, None

    return {
        "lv_score": lv_score, "lv_box": lv_box, "lv_targets": lv_targets,
        "player_score": pl_score, "player_box": pl_box,
        "battle_score": battle_score, "panel_score": panel_score,
    }


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