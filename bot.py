# ===================================================================
#  bot.py — สมองของบอท (รันในเธรดแยก, คุยกับ UI ผ่านคิว)
#
#  flow:
#    SEARCH : หามอน (ป้าย Lv.) ในจอ
#       - ไม่เจอ -> หมุนกล้องกวาด + เดินนิด + ตั้งมุมกล้องใหม่เป็นพักๆ
#       - เจอ   -> อ่านสี (แปลก=shiny หยุด) -> SEEK เดินเข้าหา
#    SEEK   : ปรับทิศ w/a/s/d เดินเข้าหามอน จนเจอ "E Catch" หรือเข้า battle
#    CATCH  : เจอปุ่ม "E Catch" -> กด E เอง
#    BATTLE : เจอป้าย TIME -> วนกดสกิล(เลข) จนจบ -> ลองจับ (E) อีกที
# ===================================================================

import time
import threading
import queue
import os

import cv2
import numpy as np
import mss
import pydirectinput
import keyboard

import config
import vision
import roblox
import ocr
from respath import resource

pydirectinput.PAUSE = 0
pydirectinput.FAILSAFE = False


class Settings:
    def __init__(self):
        self.primary_skill = config.PRIMARY_SKILL
        self.use_skill4 = config.USE_SKILL4
        self.skill_interval = config.SKILL_INTERVAL
        self.force_focus = config.FORCE_FOCUS
        self.camera_on_start = config.CAMERA_ON_START


class Bot:
    def __init__(self):
        self.settings = Settings()
        self.q = queue.Queue()
        self.lv_tpl = None
        self.battle_tpl = None
        self.panel_tpl = None       # กล่อง "Obtain Rate" (gate ว่าอยู่หน้า Catch)
        self._thread = None
        self._det_thread = None     # เธรดตรวจภาพ (ทำงานแยกจากเธรดควบคุม)
        self._running = threading.Event()
        self._stop = threading.Event()
        self._held = set()          # ปุ่มที่กดค้างอยู่ตอนนี้ (กันปุ่มค้าง)
        self.catch_count = 0        # ตัวนับ "เจอมอน" (ไม่ได้จับ แค่นับที่สู้/เจอ)
        self._rbx_ok = False
        self._rbx_last = 0.0
        self._last_cam_fix = 0.0    # เวลาที่ล็อกมุมกล้องครั้งล่าสุด
        self._skip_engage_until = 0.0  # หลังเจอตัวธรรมดา ข้าม engage ชั่วคราว (เดินออกไปหาตัวใหม่)
        # ---- ผลตรวจภาพล่าสุด (เธรด detector เขียน, เธรด control อ่าน) ----
        self._plock = threading.Lock()
        self._percept = self._blank_percept()

    @staticmethod
    def _blank_percept():
        return {"ts": 0.0, "W": 0, "H": 0, "engaged": False,
                "in_battle": False, "catch": False, "obtain": None,
                "lv_score": 0.0, "lv_box": None}

    def _snap(self):
        """อ่านสำเนาผลตรวจภาพล่าสุดแบบ thread-safe"""
        with self._plock:
            return dict(self._percept)

    def emit(self, kind, data=None):
        self.q.put((kind, data))

    def log(self, msg):
        self.emit("log", msg)

    # ---------- โหลด template ----------
    def _ensure_templates(self):
        if self.lv_tpl is None:
            p = resource(config.MONSTER_TEMPLATE)
            if not os.path.exists(p):
                self.log(f"[!] ไม่พบ {config.MONSTER_TEMPLATE}")
                return False
            tpls = [cv2.imread(p, cv2.IMREAD_COLOR)]
            # template ที่เรียนเพิ่มจากภาพมอนจริง (learn_mobs.py) — มี/ไม่มีก็ได้
            extra_dir = resource(config.LV_EXTRA_DIR)
            if os.path.isdir(extra_dir):
                for f in sorted(os.listdir(extra_dir)):
                    if f.lower().endswith(".png"):
                        t = cv2.imread(os.path.join(extra_dir, f), cv2.IMREAD_COLOR)
                        if t is not None:
                            tpls.append(t)
            if len(tpls) > config.LV_MAX_TEMPLATES:
                tpls = tpls[:config.LV_MAX_TEMPLATES]
            if len(tpls) > 1:
                self.log(f"[*] ใช้ template ป้าย Lv. {len(tpls)} แบบ")
            self.lv_tpl = tpls
        for attr, name in [("battle_tpl", config.BATTLE_TEMPLATE),
                           ("panel_tpl", config.OBTAIN_PANEL_TEMPLATE)]:
            if getattr(self, attr) is None:
                p = resource(name)
                if os.path.exists(p):
                    setattr(self, attr, cv2.imread(p, cv2.IMREAD_COLOR))
                else:
                    self.log(f"[!] ไม่พบ {name}")
        return True

    # ---------- เริ่ม/หยุด ----------
    def start(self):
        if self._running.is_set():
            return
        if not self._ensure_templates():
            return
        self._stop.clear()
        self._running.set()
        with self._plock:
            self._percept = self._blank_percept()
        # อุ่นเครื่อง OCR ล่วงหน้า (โหลดโมเดลครั้งแรกช้า) ไม่ให้สะดุดตอนถึงหน้า Catch
        threading.Thread(target=ocr.warmup, daemon=True).start()
        # เธรดตรวจภาพ: แคปจอ+ตรวจภาพวนรัวๆ อยู่เบื้องหลัง
        self._det_thread = threading.Thread(target=self._detector_loop, daemon=True)
        self._det_thread.start()
        # เธรดควบคุม: อ่านผลตรวจล่าสุดแล้วสั่งเดิน/สู้
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.emit("state", "RUNNING")
        self.log("[*] เริ่มทำงาน (ตรวจภาพ+ควบคุม แยกเธรด)")

    def stop(self):
        self._stop.set()
        self._running.clear()
        self._release_keys()
        self.emit("state", "STOPPED")

    def is_running(self):
        return self._running.is_set()

    # ---------- อินพุต/หน้าต่าง ----------
    def _key_down(self, k):
        """กดปุ่มค้าง + จำไว้ว่ากำลังค้างอยู่"""
        pydirectinput.keyDown(k)
        self._held.add(k)

    def _key_up(self, k):
        """ปล่อยปุ่ม + เอาออกจากชุดที่ค้าง (ปล่อยเสมอแม้พลาด)"""
        try:
            pydirectinput.keyUp(k)
        except Exception:
            pass
        self._held.discard(k)

    def _release_keys(self):
        # ปล่อยทั้งปุ่มที่จำไว้ + w/a/s/d เผื่อหลุด track -> ไม่มีปุ่มค้างแน่นอน
        for k in set(self._held) | {"w", "a", "s", "d"}:
            try:
                pydirectinput.keyUp(k)
            except Exception:
                pass
        self._held.clear()

    def _roblox_running(self):
        if not config.REQUIRE_ROBLOX:
            return True
        now = time.time()
        if now - self._rbx_last >= config.ROBLOX_CHECK_INTERVAL:
            self._rbx_last = now
            self._rbx_ok = roblox.is_running(config.ROBLOX_PROCESS)
        return self._rbx_ok

    def _inputs_allowed(self):
        if not config.REQUIRE_ROBLOX:
            return True
        if not self._roblox_running():
            return False
        if roblox.is_foreground(config.ROBLOX_PROCESS):
            return True
        if self.settings.force_focus:
            roblox.focus(config.ROBLOX_PROCESS)
            time.sleep(0.05)
            return roblox.is_foreground(config.ROBLOX_PROCESS)
        return False

    def _guard(self):
        if self._stop.is_set() or keyboard.is_pressed(config.STOP_KEY):
            raise _Stop
        if not self._inputs_allowed():
            raise _Paused

    # ทุกการกด "ต้อง" ผ่านตรงนี้ -> ส่งเข้าเฉพาะตอน Roblox โฟกัสเท่านั้น
    def _press(self, key):
        self._guard()
        pydirectinput.press(key)

    def _tap(self, key, seconds):
        """แตะปุ่ม 1 สเต็ป (กดสั้นๆ แล้วปล่อย) = ก้าวเดินแบบคน ไม่กดค้าง"""
        self._guard()                      # เช็คก่อนเริ่มกด
        self._key_down(key)
        t_end = time.time() + seconds
        try:
            while time.time() < t_end:
                self._guard()
                time.sleep(0.01)
        finally:
            self._key_up(key)

    def _grab(self, sct):
        if config.DETECT_REGION is None:
            mon = sct.monitors[1]
        else:
            l, t, w, h = config.DETECT_REGION
            mon = {"left": l, "top": t, "width": w, "height": h}
        img = np.array(sct.grab(mon))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def _beep(self):
        try:
            import winsound
            for _ in range(8):
                winsound.Beep(1300, 200)
                time.sleep(0.04)
        except Exception:
            pass

    # ---------- กล้อง (ล็อกมุมคงที่: top-down หรือ มุมปกติตัวอยู่กลางจอ) ----------
    def _drag(self, dx, dy):
        pydirectinput.moveRel(dx, dy, relative=True)

    def _tilt(self):
        """ตั้งมุมกล้อง: ดันลงสุด (clamp) แล้วเงยขึ้นเยอะ -> มุมปกติ ตัวอยู่กลางจอ
        (clamp ก่อนทุกครั้ง -> ได้องศาเดิมเป๊ะ ใช้ล็อกมุมกลับได้)"""
        self._guard()                      # เช็คก่อนกดเมาส์ขวา (กันหลุดไปจออื่น)
        s = config.CAMERA_TILT_SIGN
        pydirectinput.mouseDown(button="right")
        try:
            time.sleep(0.04)
            for _ in range(config.CAMERA_DOWN_STEPS):
                self._guard()
                self._drag(0, s * config.CAMERA_STEP_DY)
                time.sleep(0.012)
            for _ in range(config.CAMERA_UP_STEPS):
                self._guard()
                self._drag(0, -s * config.CAMERA_STEP_DY)
                time.sleep(0.012)
        finally:
            pydirectinput.mouseUp(button="right")
        self._last_cam_fix = time.time()

    def setup_camera(self):
        if not self._inputs_allowed():
            if not roblox.focus(config.ROBLOX_PROCESS):
                self.log("[!] ตั้งกล้องไม่ได้ — เปิด/คลิกเข้า Roblox ก่อน")
                return
            time.sleep(0.15)
        self.log("[*] ตั้งมุมกล้อง: มุมปกติ (ตัวอยู่กลางจอ) ...")
        try:
            for _ in range(config.CAMERA_ZOOM_PRESSES):
                self._press(config.CAMERA_ZOOM_KEY)
                time.sleep(0.03)
            self._tilt()
            self.log("[*] ตั้งกล้องเสร็จ")
        except _Paused:
            self.log("[!] ตั้งกล้องไม่สำเร็จ (Roblox ไม่โฟกัส)")

    def _reassert_camera(self):
        try:
            self._tilt()
        except _Paused:
            self._release_keys()

    def _fix_camera_if_due(self):
        """ล็อกมุมกล้องกลับองศาเดิมเป็นพักๆ (กันมุมเพี้ยน/ผู้เล่นหมุนกล้อง)
        ทำเฉพาะตอนเปิดให้บอทคุมกล้อง (camera_on_start) เท่านั้น"""
        if not self.settings.camera_on_start:
            return
        if time.time() - self._last_cam_fix >= config.CAMERA_FIX_EVERY:
            self._reassert_camera()

    def _scan_yaw(self, sign):
        self._guard()
        pydirectinput.mouseDown(button="right")
        try:
            self._drag(sign * config.SCAN_ROTATE_DX, 0)
            time.sleep(0.03)
        finally:
            pydirectinput.mouseUp(button="right")
        # เว้นจังหวะให้เกมออกจากโหมดหมุนกล้องก่อน ค่อยกดเดิน (ไม่งั้นเดินไม่ติด)
        time.sleep(config.AFTER_CAMERA_SETTLE)

    # ---------- ตรวจภาพ ----------
    def _in_battle(self, frame):
        if self.battle_tpl is None:
            return False
        return vision.best_score(frame, self.battle_tpl,
                                 config.BATTLE_SCALES) >= config.BATTLE_THRESHOLD

    def _on_catch_screen(self, frame):
        """อยู่หน้า Catch ไหม (เจอกล่อง 'Obtain Rate' ซ้ายล่าง)"""
        if self.panel_tpl is None:
            return False
        return vision.best_score(frame, self.panel_tpl,
                                 config.PANEL_SCALES) >= config.PANEL_THRESHOLD

    # ===================================================================
    #  เธรดตรวจภาพ — แคปจอ+ตรวจภาพวนรัวๆ เก็บผลล่าสุด (ไม่กดปุ่มเอง)
    #  เธรดควบคุมแค่ "อ่านผล" ไป สั่งเดิน/สู้ได้ทันที ไม่ต้องรอตรวจภาพ
    # ===================================================================
    def _detector_loop(self):
        sct = mss.mss()
        while not self._stop.is_set():
            if config.REQUIRE_ROBLOX and not self._roblox_running():
                time.sleep(0.1)
                continue
            try:
                frame = self._grab(sct)
            except Exception:
                time.sleep(0.05)
                continue

            H, W = frame.shape[:2]
            in_b = self._in_battle(frame)
            catch = self._on_catch_screen(frame)        # เจอกล่อง Obtain Rate
            # OCR แผง shiny/prismatic เฉพาะตอนอยู่หน้า Catch (rec-only เร็ว ~200ms)
            obtain = ocr.read_obtain(frame) if catch else None
            engaged = in_b or catch or bool(obtain)
            # อยู่ในสู้/จับแล้วไม่ต้องหาป้าย Lv. (ประหยัดเวลา ตรวจไวขึ้น)
            if engaged:
                lv_score, lv_box = 0.0, None
            else:
                lv_score, lv_box, _ = vision.locate_lv(frame, self.lv_tpl)

            with self._plock:
                self._percept = {
                    "ts": time.time(), "W": W, "H": H, "engaged": engaged,
                    "in_battle": in_b, "catch": catch, "obtain": obtain,
                    "lv_score": lv_score, "lv_box": lv_box,
                }

    # ===================================================================
    #  ลูปหลัก (เธรดควบคุม)
    # ===================================================================
    def _loop(self):
        empty = 0

        try:
            if self._wait_until_running():
                roblox.focus(config.ROBLOX_PROCESS)
                time.sleep(0.15)
                if config.MAXIMIZE_ON_START:
                    if roblox.maximize(config.ROBLOX_PROCESS):
                        self.log("[*] ตั้ง Roblox เป็น Full Window (maximize)")
                    time.sleep(0.3)
                if self.settings.camera_on_start:
                    self.setup_camera()
        except _Stop:
            pass

        try:
            while not self._stop.is_set():
                if keyboard.is_pressed(config.STOP_KEY):
                    raise _Stop
                if config.REQUIRE_ROBLOX and not self._roblox_running():
                    self._release_keys()
                    self.emit("state", "รอ Roblox...")
                    if not self._wait_until_running():
                        raise _Stop
                    continue
                try:
                    self._fix_camera_if_due()      # ล็อกมุมกล้องกลับองศาเดิมเป็นพักๆ
                    empty = self._iteration(empty)
                except _Paused:
                    self._release_keys()
                    self.emit("state", "ดึงโฟกัส Roblox...")
                    time.sleep(0.1)
        except _Stop:
            pass
        finally:
            self._release_keys()
            self._running.clear()
            self.emit("state", "STOPPED")
            self.log("[*] หยุดบอท")

    def _wait_until_running(self):
        told = False
        while config.REQUIRE_ROBLOX and not self._roblox_running():
            if self._stop.is_set() or keyboard.is_pressed(config.STOP_KEY):
                return False
            if not told:
                self.log("[*] รอ Roblox เปิด...")
                told = True
            time.sleep(0.4)
        return True

    # ---------- หนึ่งรอบ (อ่านผลตรวจล่าสุดจากเธรด detector) ----------
    def _iteration(self, empty):
        p = self._snap()
        if p["ts"] == 0.0:                 # ยังไม่มีผลตรวจรอบแรก -> รอแป๊บ
            time.sleep(0.05)
            return empty

        # หลังเจอตัวธรรมดา -> ข้าม engage ชั่วคราว (เดินออกไปหาตัวใหม่ ไม่วนเข้าจอ Catch เดิม)
        in_cooldown = time.time() < self._skip_engage_until

        # อยู่หน้า battle/catch -> จัดการสู้ (ไม่จับ; หยุดเฉพาะ shiny/prismatic)
        if p["engaged"] and not in_cooldown:
            self._fight_and_catch()
            return 0

        if p["lv_score"] >= config.MONSTER_THRESHOLD and not in_cooldown:
            # เจอมอน -> ค่อยๆ เดินเข้าหา
            self._seek_and_engage()
            return 0

        # ไม่เจอ Lv. (หรือช่วง cooldown) -> หมุนกล้องกวาดหา จนกว่าจะเจอ
        self.emit("state", "SEARCH")
        empty += 1
        self._fix_camera_if_due()                  # ล็อกมุมกล้องไว้องศาเดิม
        self._scan_yaw(1)                           # หมุนกล้องหา Lv.
        if empty % config.SCAN_WALK_EVERY == 0:      # เดินหน้านิดๆ เป็นพักๆ
            self._tap(config.SCAN_MOVE_KEY, config.SCAN_MOVE_DUR)
        return empty

    # ---------- ค่อยๆ เดินเข้าหามอน (แตะ wasd ทีละก้าวแบบคน; ทิศมาจากเธรด detector) ----------
    def _seek_and_engage(self):
        self.emit("state", "SEEK")
        miss = 0
        last_ts = 0.0
        key = None
        near = False
        t_end = time.time() + config.SEEK_TIMEOUT
        while time.time() < t_end:
            self._guard()
            p = self._snap()

            # ปรับทิศ/เช็คมอน "เฉพาะตอนมีผลตรวจเฟรมใหม่" (ไม่งั้นนับ miss มั่ว)
            if p["ts"] != last_ts:
                last_ts = p["ts"]

                if p["engaged"]:
                    self._fight_and_catch()
                    return

                if p["lv_score"] < config.MONSTER_THRESHOLD:
                    miss += 1
                    if miss >= config.SEEK_LOST_MISS:
                        return
                    key = None
                else:
                    miss = 0
                    box, W, H = p["lv_box"], p["W"], p["H"]
                    cx = box[0] + box[2] // 2
                    cy = box[1] + box[3] // 2 + int(box[2] * config.SEEK_BODY_DY)
                    dx, dy = cx - W // 2, cy - H // 2
                    near = max(abs(dx), abs(dy)) < config.SEEK_NEAR_DIST

                    if abs(dx) < config.SEEK_DEADZONE and abs(dy) < config.SEEK_DEADZONE:
                        key = "w"
                    elif abs(dy) >= abs(dx):
                        key = "w" if dy < 0 else "s"
                    else:
                        key = "d" if dx > 0 else "a"

            # ก้าวทีละสเต็ป (กดสั้นๆ แล้วปล่อย + เว้นจังหวะ) = เดินแบบคน ไม่พุ่ง
            if key:
                hold = config.STEP_HOLD_NEAR if near else config.STEP_HOLD
                self._tap(key, hold)
                time.sleep(config.STEP_GAP)
            else:
                time.sleep(0.03)

    def _end_normal(self):
        """เจอตัวธรรมดา -> ไม่จับ, นับ +1, เดินออกไปหาตัวต่อไป (ข้าม engage ชั่วคราว)"""
        self.catch_count += 1
        self.emit("catch", self.catch_count)
        self._skip_engage_until = time.time() + config.SKIP_AFTER_NORMAL
        self.log("[•] ตัวธรรมดา -> ไม่จับ, เดินหาตัวต่อไป")

    # ---------- จัดการ "สู้" (ไม่จับ; หยุดเฉพาะ shiny/prismatic) ----------
    def _fight_and_catch(self):
        """
        - อยู่ใน battle (TIME) -> วนกดสกิลซ้ำจนกว่าจะขึ้นหน้า Catch
        - หน้า Catch ตัวธรรมดา -> ไม่จับ (ไม่กด E) เดินหาตัวต่อไป
        - เจอ shiny/prismatic -> หยุดทันที + แจ้งเตือน (ให้ผู้เล่นจัดการเอง)
        ตรวจมาจากเธรด detector (เฟรมใหม่เท่านั้น) ส่วนการกดสกิลเดินตามเวลาเอง
        """
        s = self.settings
        self.emit("state", "BATTLE")
        self.log(f"[BATTLE] สู้ -> วนสกิล {s.primary_skill}"
                 + (" +4" if s.use_skill4 else ""))
        last_skill = 0.0
        last_event = time.time()
        last_ts = 0.0
        special_count = 0
        catch_since = 0.0        # เริ่มเห็นหน้า Catch เมื่อไร (ไว้ timeout เป็นธรรมดา)
        mode = "battle"          # battle / wait / idle
        hard_end = time.time() + config.FIGHT_HARD_CAP

        while time.time() < hard_end:
            self._guard()
            now = time.time()
            p = self._snap()

            # ---- อัปเดตสถานะจาก "เฟรมตรวจใหม่" เท่านั้น ----
            if p["ts"] != last_ts:
                last_ts = p["ts"]
                status = p["obtain"]
                catch_btn = p["catch"]

                special = status in ("shiny", "prismatic")
                if status == "prismatic" and not config.STOP_ON_PRISMATIC:
                    special = False
                    status = "normal"
                if special:
                    special_count += 1
                    last_event = now
                    if special_count >= config.OBTAIN_CONFIRM:
                        self._release_keys()
                        self._running.clear()
                        self.emit("state", status.upper() + "!")
                        self.log(f"[★★★] เจอ {status.upper()}! -> หยุด + แจ้งเตือน (ไปจัดการเอง)")
                        if config.BEEP_ON_SHINY:
                            self._beep()
                        self.emit("found", status)
                        self._stop.set()
                        return
                    time.sleep(0.05)
                    continue
                else:
                    special_count = 0

                if status == "normal":
                    self._end_normal()      # ตัวธรรมดาชัดเจน -> ไปต่อ
                    return
                elif p["in_battle"]:
                    mode = "battle"
                    last_event = now
                    catch_since = 0.0
                elif catch_btn:
                    mode = "wait"           # หน้า Catch แต่ยังอ่านแผงไม่ชัด -> รอ
                    last_event = now
                    if catch_since == 0.0:
                        catch_since = now
                else:
                    mode = "idle"

            # ---- ลงมือ (เดินตามเวลาเอง ไม่รอตรวจภาพ) ----
            if mode == "battle":
                self.emit("state", "BATTLE")
                if now - last_skill >= s.skill_interval:
                    self._press(s.primary_skill)
                    if s.use_skill4:
                        self._press("4")
                    last_skill = now
            elif mode == "wait":
                self.emit("state", "CATCH")
                # อยู่หน้า Catch นานเกินไปแต่อ่านแผงไม่ออก -> ถือว่าธรรมดา ไปต่อ
                if catch_since and now - catch_since > config.CATCH_SCREEN_TIMEOUT:
                    self._end_normal()
                    return
            else:
                if now - last_event > config.FIGHT_IDLE_END:
                    break
            time.sleep(0.05)

        self.log("[BATTLE] จบ -> หาตัวต่อไป")


class _Stop(Exception):
    pass


class _Paused(Exception):
    pass
