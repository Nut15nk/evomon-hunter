# ===================================================================
#  bot.py — สมองของบอท (รันในเธรดแยก, คุยกับ UI ผ่านคิว)
#
#  flow:
#    SEARCH : หามอน (ป้าย Lv.) ในจอ
#       - ไม่เจอ -> หมุนกล้องกวาด + เดินนิด + ตั้งมุมกล้องใหม่เป็นพักๆ
#       - เจอ   -> อ่านสี (แปลก=shiny หยุด) -> SEEK เดินเข้าหา
#    SEEK   : หมุนกล้อง (yaw) เล็งไปที่มอนก่อน พอเล็งตรงแล้วกด "w" เดินเข้าหา
#             จนเจอ battle/catch
#    ENGAGE : เข้าสู้/หน้า catch แล้ว -> "รอเฉยๆ" ให้ Auto Skill / Auto Catch
#             ที่มีอยู่แล้วในเกมจัดการเอง (บอทไม่กดสกิล ไม่กด E)
#             ถ้าเจอ shiny/prismatic -> หยุดทันที + แจ้งเตือนให้คนจัดการเอง
#
#  หมายเหตุ: เธรดตรวจภาพยิงตรวจจับ Lv./ผู้เล่น/battle/panel "พร้อมกัน" ผ่าน
#  vision.detect_all() (multi-thread) แต่ถูกจำกัดอัตราไว้ที่ config.DETECT_FPS
#  (ดีฟอลต์ 10 ครั้ง/วิ) กันเธรดตรวจภาพยิงถี่จนซีพียูโหลดเกิน/ค้าง
#  ค่านี้คุมแค่ "ความถี่การตรวจจับ" เท่านั้น ไม่เกี่ยวกับตัวเลข FPS ที่โชว์บนจอ
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

# เกมมี Auto Skill / Auto Catch อยู่แล้ว -> บอทไม่ต้องกดสกิล/กด E เอง
# เธรดตรวจภาพถูกจำกัดอัตราไว้ที่ config.DETECT_FPS (ดีฟอลต์ 10 ครั้ง/วิ)

# ---- ค่าคุมการ "หันกล้องเข้าหาเป้าหมาย" ตอน SEEK (เดาไว้ก่อน ยังไม่มีใน config.py) ----
# SEEK_TURN_GAIN : พิกเซล dx บนจอ -> แปลงเป็นระยะลากเมาส์ yaw เท่าไหร่ต่อ 1 พิกเซล
# SEEK_TURN_MAX  : ลากเมาส์ yaw ต่อ 1 ครั้งได้มากสุดกี่พิกเซล (กันหันพรวดเดียวเลยเป้า)
SEEK_TURN_GAIN = getattr(config, "SEEK_TURN_GAIN", 0.06)
SEEK_TURN_MAX = getattr(config, "SEEK_TURN_MAX", 25)
# อัตราตรวจจับของเธรด detector (ครั้ง/วิ) -- getattr กันพังถ้า config.py ยังไม่มีค่านี้
DETECT_FPS = getattr(config, "DETECT_FPS", 10)
# ปุ่มบังคับ fullscreen ตอนเริ่ม -- getattr กันพัง ถ้า config.py เวอร์ชันเก่ายังไม่มีค่านี้
FULLSCREEN_KEY = getattr(config, "FULLSCREEN_KEY", "f11")


class Settings:
    def __init__(self):
        self.force_focus = config.FORCE_FOCUS
        self.camera_on_start = config.CAMERA_ON_START
        self.fullscreen_on_start = getattr(config, "FULLSCREEN_ON_START", True)


class Bot:
    def __init__(self):
        self.settings = Settings()
        self.q = queue.Queue()
        self.lv_tpl = None
        self.battle_tpl = None
        self.panel_tpl = None       # กล่อง "Obtain Rate" (gate ว่าอยู่หน้า Catch)
        self.player_tpl = None      # ป้ายชื่อตัวเอง (anchor) — ไม่มีไฟล์ = ใช้กลางจอแทน
        self._thread = None
        self._det_thread = None     # เธรดตรวจภาพ (ทำงานแยกจากเธรดควบคุม)
        self._running = threading.Event()
        self._stop = threading.Event()
        self._held = set()          # ปุ่มที่กดค้างอยู่ตอนนี้ (กันปุ่มค้าง)
        self.catch_count = 0        # ตัวนับ "เจอมอน" (เกม auto catch เอง บอทแค่นับ)
        self._rbx_ok = False
        self._rbx_last = 0.0
        self._skip_engage_until = 0.0  # หลังเจอตัว/จบ engage ข้าม engage ชั่วคราว (เดินออกไปหาตัวใหม่)
        # ---- ผลตรวจภาพล่าสุด (เธรด detector เขียน, เธรด control อ่าน) ----
        self._plock = threading.Lock()
        self._percept = self._blank_percept()
        # ---- เป้าที่ "ล็อก" อยู่ตอนนี้ (เธรด control เขียน, GUI อ่านไปวาด overlay) ----
        self._tlock = threading.Lock()
        self._locked_box = None

    @staticmethod
    def _blank_percept():
        return {"ts": 0.0, "W": 0, "H": 0, "engaged": False,
                "in_battle": False, "catch": False, "obtain": None,
                "lv_score": 0.0, "lv_box": None, "lv_targets": [],
                "player_score": 0.0, "player_box": None}

    def _snap(self):
        """อ่านสำเนาผลตรวจภาพล่าสุดแบบ thread-safe"""
        with self._plock:
            return dict(self._percept)

    def _set_locked(self, box):
        with self._tlock:
            self._locked_box = box

    def get_locked(self):
        """กล่องมอนที่บอท 'ล็อกเป้า' อยู่ตอนนี้ (None ถ้าไม่มี) -- ให้ GUI เอาไปวาด overlay"""
        with self._tlock:
            return self._locked_box

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
        if self.player_tpl is None:
            p = resource(config.PLAYER_TEMPLATE)
            if os.path.exists(p):
                self.player_tpl = cv2.imread(p, cv2.IMREAD_COLOR)
                self.log("[*] จับตำแหน่งตัวเองจากป้ายชื่อ (anchor เปิด)")
            else:
                self.log("[i] ไม่มี templates/player.png -> ยึดกลางจอแทน "
                         "(ครอปป้ายชื่อด้วย make_template.py ได้)")
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
        # เธรดตรวจภาพ: แคปจอ+ตรวจภาพวนที่ config.DETECT_FPS (ดีฟอลต์ 10 ครั้ง/วิ) อยู่เบื้องหลัง
        self._det_thread = threading.Thread(target=self._detector_loop, daemon=True)
        self._det_thread.start()
        # เธรดควบคุม: อ่านผลตรวจล่าสุดแล้วสั่งเดิน (ไม่กดสกิล/กด E เอง)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.emit("state", "RUNNING")
        self.log(f"[*] เริ่มทำงาน (ตรวจภาพหลายอย่างพร้อมกัน ~{DETECT_FPS} FPS, "
                 "หันกล้อง+เดินหาเอง / ปล่อย Auto Skill+Auto Catch ในเกมจัดการสู้-จับ)")

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

    # ---------- กล้อง ----------
    def _drag(self, dx, dy):
        pydirectinput.moveRel(dx, dy, relative=True)

    def setup_camera(self):
        """ซูมกล้องออกตอนเริ่มเท่านั้น -- ไม่ปรับมุมก้ม/เงย (pitch) อัตโนมัติแล้ว
        เพราะทำให้กล้องหันเงยขึ้นฟ้าเอง มุมกล้องปล่อยตามที่ผู้เล่นตั้งเอง"""
        if not self._inputs_allowed():
            if not roblox.focus(config.ROBLOX_PROCESS):
                self.log("[!] ตั้งกล้องไม่ได้ — เปิด/คลิกเข้า Roblox ก่อน")
                return
            time.sleep(0.15)
        self.log("[*] ซูมกล้องออก ...")
        try:
            for _ in range(config.CAMERA_ZOOM_PRESSES):
                self._press(config.CAMERA_ZOOM_KEY)
                time.sleep(0.03)
            self.log("[*] ตั้งกล้องเสร็จ")
        except _Paused:
            self.log("[!] ตั้งกล้องไม่สำเร็จ (Roblox ไม่โฟกัส)")

    def _turn_camera(self, dx_pixels):
        """หมุนกล้อง (yaw) นิดๆ เข้าหาเป้าหมาย โดยไม่แตะมุมก้ม/เงย (pitch เดิม)"""
        self._guard()
        pydirectinput.mouseDown(button="right")
        try:
            self._drag(int(dx_pixels), 0)
            time.sleep(0.02)
        finally:
            pydirectinput.mouseUp(button="right")

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
    #  เธรดตรวจภาพ — แคปจอ+ตรวจภาพ "พร้อมกันหลายอย่าง" (vision.detect_all,
    #  multi-thread) โดยจำกัดอัตราไว้ที่ config.DETECT_FPS (ดีฟอลต์ 10 ครั้ง/วิ)
    #  เธรดควบคุมแค่ "อ่านผล" ไปสั่งเดินได้ทันที ไม่ต้องรอตรวจภาพ
    #
    #  หมายเหตุ: DETECT_FPS คุมแค่ "ความถี่ที่เธรดนี้ตรวจจับ" เท่านั้น
    #  ไม่ใช่ค่าที่เอาไปโชว์บน overlay หน้าจอ
    # ===================================================================
    def _detector_loop(self):
        sct = mss.mss()
        prev_engaged = False   # เดาจากผลรอบก่อน กันหา Lv./ผู้เล่นทิ้งเปล่าตอน engaged
        period = 1.0 / max(DETECT_FPS, 1)
        while not self._stop.is_set():
            loop_start = time.time()

            if config.REQUIRE_ROBLOX and not self._roblox_running():
                time.sleep(0.1)
                continue
            try:
                frame = self._grab(sct)
            except Exception:
                time.sleep(0.05)
                continue

            H, W = frame.shape[:2]
            # ยิงตรวจจับ Lv./ผู้เล่น/battle/panel พร้อมกันในเฟรมเดียว (multi-thread)
            res = vision.detect_all(
                frame, self.lv_tpl, self.player_tpl,
                self.battle_tpl, self.panel_tpl,
                config.BATTLE_SCALES, config.PANEL_SCALES,
                need_target=not prev_engaged,
            )
            in_b = res["battle_score"] >= config.BATTLE_THRESHOLD
            catch = res["panel_score"] >= config.PANEL_THRESHOLD
            # OCR แผง shiny/prismatic เฉพาะตอนอยู่หน้า Catch (rec-only เร็ว ~200ms)
            obtain = ocr.read_obtain(frame) if catch else None
            engaged = in_b or catch or bool(obtain)
            prev_engaged = engaged

            lv_score, lv_box = res["lv_score"], res["lv_box"]
            pl_score, pl_box = res["player_score"], res["player_box"]
            if pl_score < config.PLAYER_THRESHOLD:
                pl_box = None

            with self._plock:
                self._percept = {
                    "ts": time.time(), "W": W, "H": H, "engaged": engaged,
                    "in_battle": in_b, "catch": catch, "obtain": obtain,
                    "lv_score": lv_score, "lv_box": lv_box,
                    "lv_targets": res.get("lv_targets", []),
                    "player_score": pl_score, "player_box": pl_box,
                }

            # ---- จำกัดอัตราไว้ที่ ~DETECT_FPS ครั้ง/วิ ----
            # ถ้ารอบนี้ (แคปจอ+ตรวจ+OCR) เร็วกว่า period ก็ sleep ส่วนที่เหลือ
            # ถ้าช้ากว่า (เครื่องหนืด/OCR ช้า) ก็ปล่อยผ่านทันที ไม่สะสม backlog
            elapsed = time.time() - loop_start
            remaining = period - elapsed
            if remaining > 0:
                time.sleep(remaining)

    # ===================================================================
    #  ลูปหลัก (เธรดควบคุม)
    # ===================================================================
    def _loop(self):
        empty = 0
        self.log(f"[*] ตรวจจับภาพด้วย: {vision.gpu_status_text()}")

        try:
            if self._wait_until_running():
                roblox.focus(config.ROBLOX_PROCESS)
                time.sleep(0.15)
                if config.MAXIMIZE_ON_START:
                    if roblox.maximize(config.ROBLOX_PROCESS):
                        self.log("[*] ตั้ง Roblox เป็น Full Window (maximize)")
                    time.sleep(0.3)
                if self.settings.fullscreen_on_start:
                    try:
                        self._press(FULLSCREEN_KEY)
                        self.log(f"[*] กด {FULLSCREEN_KEY.upper()} บังคับ Fullscreen "
                                 "(ถ้า Roblox fullscreen อยู่แล้วจะสลับกลับเป็น windowed แทน)")
                    except _Paused:
                        self.log("[!] กด Fullscreen ไม่สำเร็จ (Roblox ไม่โฟกัส)")
                    except Exception as e:
                        # กันพลาดจุดนี้แล้วลากทั้งเธรดตายไปด้วย (บอทจะไม่เดินหามอนเลย)
                        self.log(f"[!] Fullscreen error (ข้ามไป ไม่กระทบส่วนอื่น): {e}")
                    time.sleep(0.2)
                if self.settings.camera_on_start:
                    self.setup_camera()
        except _Stop:
            pass
        except Exception as e:
            # กันไม่ให้ error ตอน setup (maximize/fullscreen/camera) ทำให้ทั้งลูปหลัก
            # (หามอน/เดิน) ไม่ทำงานเลย -- log ไว้แล้วปล่อยให้ลูปหลักทำงานต่อ
            self.log(f"[!] Setup error (ดำเนินการต่อ): {e}")

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

        # หลังจบ engage -> ข้าม engage ชั่วคราว (เดินออกไปหาตัวใหม่ ไม่วนเข้าจอเดิม)
        in_cooldown = time.time() < self._skip_engage_until

        # อยู่หน้า battle/catch -> รอให้ Auto Skill/Auto Catch ในเกมจัดการเอง
        if p["engaged"] and not in_cooldown:
            self._handle_engage()
            return 0

        if p["lv_score"] >= config.MONSTER_THRESHOLD and not in_cooldown:
            # เจอมอน -> ค่อยๆ เดินเข้าหา
            self._seek_and_engage()
            return 0

        # ไม่เจอ Lv. (หรือช่วง cooldown) -> หมุนกล้องกวาดหา จนกว่าจะเจอ
        self.emit("state", "SEARCH")
        empty += 1
        self._scan_yaw(1)                           # หมุนกล้องหา Lv.
        if empty % config.SCAN_WALK_EVERY == 0:      # เดินหน้านิดๆ เป็นพักๆ
            self._tap(config.SCAN_MOVE_KEY, config.SCAN_MOVE_DUR)
        return empty

    # ---------- เดินเข้าหามอนตามตำแหน่งที่ตรวจ (overlay) ชี้ไว้ ----------
    # วิธี: "ตรวจจับก่อนเสมอ" แล้วค่อยขยับ -- แต่ละรอบอ่านตำแหน่งมอนล่าสุดก่อน
    # ถ้ายังไม่เล็งตรงพอ (dx เกิน deadzone) จะหันกล้องอย่างเดียวก่อน ยังไม่ก้าวเดิน
    # พอเล็งตรงแล้วค่อยก้าวเดิน 1 สเต็ป (แตะปุ่มค้างแล้วปล่อย = เดินแบบคน ไม่พุ่ง)
    # สเต็ปยาวกว่าค่าตั้งต้นเดิม (STEP_HOLD) เพื่อให้ไปได้ไกลขึ้นต่อก้าว
    #
    # แต่ละเฟรมอาจเจอมอนหลายตัวพร้อมกัน (lv_targets) -- ฟังก์ชันนี้ "ล็อกเป้า"
    # ไว้ตัวเดียวตั้งแต่ต้น (เลือกตัวที่ใกล้ตัวเราที่สุดก่อน) แล้วเดินเข้าหาตัวนั้น
    # จนจบ (เข้าสู้ / หลุดเป้า / timeout) ไม่สลับไปมอนตัวอื่นระหว่างทางแม้เฟรมนั้น
    # จะมีตัวอื่นคะแนนสูงกว่าก็ตาม -- พอจบ engage แล้วค่อยเลือกตัวถัดไปรอบใหม่
    #
    # หลุดเป้าแค่ "ชั่วคราว" (เฟรมสะดุด/โดนบังแป๊บเดียว) -- ยังไม่เลิกไล่ทันที แต่
    # จะ "หยุดเดินรอ" จนกว่าจะเจอเป้าอีกครั้ง (ไม่เดินมั่วตอนไม่รู้ตำแหน่ง) ต้องหลุด
    # ต่อเนื่องเกิน SEEK_LOST_MISS เฟรมถึงจะถือว่าหลุดจริงแล้วเลิกไล่
    def _seek_and_engage(self):
        self.emit("state", "SEEK")
        miss = 0
        last_ts = 0.0
        move_key = None
        near = False
        target = None    # (x,y,w,h) กล่องมอนที่ล็อกไว้ -- อัปเดตตำแหน่งได้ แต่ไม่สลับตัว
        self._set_locked(None)
        t_end = time.time() + config.SEEK_TIMEOUT
        try:
            while time.time() < t_end:
                self._guard()
                p = self._snap()

                if p["ts"] != last_ts:
                    last_ts = p["ts"]

                    if p["engaged"]:
                        self._handle_engage()
                        return

                    # มอนทั้งหมดที่เจอเฟรมนี้ (คะแนน >= threshold เท่านั้น)
                    targets = [(sc, box) for sc, box in (p.get("lv_targets") or [])
                               if box and sc >= config.MONSTER_THRESHOLD]
                    if not targets and p["lv_box"] and p["lv_score"] >= config.MONSTER_THRESHOLD:
                        targets = [(p["lv_score"], p["lv_box"])]   # เผื่อ lv_targets ว่าง (fallback)

                    W, H = p["W"], p["H"]
                    px, py = W // 2, H // 2
                    if p["player_box"]:
                        pb = p["player_box"]
                        px = pb[0] + pb[2] // 2
                        py = pb[1] + pb[3] // 2 + int(pb[2] * config.PLAYER_BODY_DY)

                    picked = None
                    if target is None:
                        # ยังไม่ได้ล็อกเป้า -> เลือกตัวที่ "ใกล้ตัวเราที่สุด" ก่อน
                        best_d = None
                        for sc, tb in targets:
                            tx, ty, tw, th = tb
                            ccx = tx + tw / 2.0
                            ccy = ty + th / 2.0 + int(tw * config.SEEK_BODY_DY)
                            d = (ccx - px) ** 2 + (ccy - py) ** 2
                            if best_d is None or d < best_d:
                                best_d, picked = d, tb
                    else:
                        # ล็อกเป้าไว้แล้ว -> หาให้ตรงกับ "ตัวเดิม" (จับคู่ตามตำแหน่งใกล้สุด)
                        # กันสลับไปมอนตัวอื่นที่คะแนนสูงกว่าระหว่างเดิน
                        tx0, ty0, tw0, th0 = target
                        ccx0, ccy0 = tx0 + tw0 / 2.0, ty0 + th0 / 2.0
                        best_d = None
                        for sc, tb in targets:
                            tx, ty, tw, th = tb
                            ccx, ccy = tx + tw / 2.0, ty + th / 2.0
                            d = (ccx - ccx0) ** 2 + (ccy - ccy0) ** 2
                            max_jump = max(tw0, tw, 20) * 2.5
                            if d < max_jump ** 2 and (best_d is None or d < best_d):
                                best_d, picked = d, tb

                    if picked is None:
                        miss += 1
                        if miss >= config.SEEK_LOST_MISS:
                            return   # หลุดเป้าจริง (ไม่เจอต่อเนื่องนานเกินไป) -> เลิกไล่
                        # หลุดแค่ชั่วคราว -> ยังไม่รู้ตำแหน่งใหม่ -> หยุดเดินรอก่อน
                        # (ไม่เดินมั่ว) จนกว่าจะตรวจเจอเป้าอีกครั้ง
                        move_key = None
                    else:
                        miss = 0
                        target = picked
                        self._set_locked(target)
                        tx, ty, tw, th = target
                        cx = tx + tw // 2
                        cy = ty + th // 2 + int(tw * config.SEEK_BODY_DY)
                        dx, dy = cx - px, cy - py
                        near = max(abs(dx), abs(dy)) < config.SEEK_NEAR_DIST

                        if abs(dx) > config.SEEK_DEADZONE:
                            # ยังไม่เล็งตรง -> หันกล้องเข้าหาเป้าหมายก่อน (ไม่เดินเฟรมนี้
                            # กันเดินเบี้ยวระหว่างกล้องกำลังหมุน)
                            turn = dx * SEEK_TURN_GAIN
                            turn = max(-SEEK_TURN_MAX, min(SEEK_TURN_MAX, turn))
                            self._turn_camera(turn)
                            move_key = None
                        else:
                            move_key = "w"     # เล็งตรงเป้าหมายแล้ว -> ก้าวเดินเข้าหา

                # ก้าวทีละสเต็ป (แตะปุ่มค้าง STEP_HOLD วิ แล้วปล่อย + เว้นจังหวะ)
                # = เดินแบบคน ไม่พุ่งรวด แต่สเต็ปยาวกว่าค่าตั้งต้นเดิม ไปได้ไกลขึ้นต่อก้าว
                if move_key:
                    hold = config.STEP_HOLD_NEAR if near else config.STEP_HOLD
                    self._tap(move_key, hold)
                    time.sleep(config.STEP_GAP)
                else:
                    time.sleep(0.03)
        finally:
            self._set_locked(None)

    def _end_engage(self):
        """จบ engage ปกติ (เกม auto catch เก็บให้แล้ว) -> นับ +1 แล้วเดินหาตัวต่อไป"""
        self.catch_count += 1
        self.emit("catch", self.catch_count)
        self._skip_engage_until = time.time() + config.SKIP_AFTER_NORMAL
        self.log("[•] จบ engage (Auto Catch เก็บให้แล้ว) -> เดินหาตัวต่อไป")

    # ---------- จัดการตอน "เข้าสู้/หน้า Catch": ไม่กดสกิล ไม่กด E เอง ----------
    def _handle_engage(self):
        """
        เกมมี Auto Skill / Auto Catch อยู่แล้ว -> บอทแค่ "รอ" เฉยๆ ไม่ยุ่งกับ
        การสู้/การจับ หน้าที่บอทตรงนี้มีแค่:
          - คอย OCR อ่านแผง Obtain Rate เผื่อเจอ shiny/prismatic -> หยุดทันที
            + แจ้งเตือน ให้ผู้เล่นมาจัดการเอง (จุดนี้ห้ามให้เกม auto จับไปเฉยๆ)
          - พอพ้นสถานะ battle/catch แล้ว (เกมสู้/จับเสร็จ) -> เดินหาตัวต่อไป
        ตรวจสถานะจากเธรด detector (เฟรมใหม่เท่านั้น) บอทไม่กดปุ่มอะไรเลยระหว่างนี้
        """
        self.emit("state", "BATTLE")
        self.log("[BATTLE] เจอมอน -> รอ Auto Skill/Auto Catch ในเกมจัดการเอง")
        last_event = time.time()
        last_ts = 0.0
        special_count = 0
        hard_cap = time.time() + config.FIGHT_HARD_CAP

        while time.time() < hard_cap:
            self._guard()
            now = time.time()
            p = self._snap()

            # ---- อัปเดตสถานะจาก "เฟรมตรวจใหม่" เท่านั้น ----
            if p["ts"] != last_ts:
                last_ts = p["ts"]
                status = p["obtain"]

                special = status in ("shiny", "prismatic")
                if status == "prismatic" and not config.STOP_ON_PRISMATIC:
                    special = False

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

                if p["engaged"]:
                    last_event = now
                    self.emit("state", "CATCH" if p["catch"] else "BATTLE")
                else:
                    # พ้นสถานะ engaged แล้ว -> เกม auto skill/auto catch จัดการจบแล้ว
                    self._end_engage()
                    return

            # ไม่มีเฟรมใหม่นานเกินไป -> เผื่อเกมค้าง/หลุดสถานะ ตัดจบไปหาตัวต่อไป
            if now - last_event > config.FIGHT_IDLE_END:
                break
            time.sleep(0.05)

        self.log("[BATTLE] เกิน timeout -> หาตัวต่อไป")


class _Stop(Exception):
    pass


class _Paused(Exception):
    pass