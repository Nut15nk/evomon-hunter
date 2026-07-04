# ===================================================================
#  gui.py — หน้าต่างควบคุมบอท Evomon (CustomTkinter — ธีมมืดโมเดิร์น)
#  รัน:  py -3.12 gui.py   (เปิดผ่าน start.bat จะขอสิทธิ์ Admin ให้เอง)
# ===================================================================

import json
import os
import sys
import time
import threading
import tkinter as tk

try:
    import customtkinter as ctk
except ImportError:
    print("[!] ยังไม่มี customtkinter — รัน: py -3.12 -m pip install -r requirements.txt")
    sys.exit(1)

from bot import Bot
import roblox
import config
from respath import userfile, resource

HISTORY_FILE = userfile("history.json")

# ---- โทนสี ----
GREEN = "#2fbf5f"
GREEN_HOVER = "#27a552"
RED = "#e5484d"
RED_HOVER = "#c93a3f"
GOLD = "#f5c518"
PURPLE = "#b18aff"
BLUE = "#4f8df7"
SUB = "#8b93a7"

OVERLAY_TRANS = "#010203"     # สีที่ถูกทำให้โปร่งใส (อย่าใช้สีนี้วาดกรอบ)


class Overlay:
    """หน้าต่างโปร่งใสคลุมจอ ตีกรอบว่าบอทจับตัวเรา/มอนอยู่ตรงไหน
    ถูกซ่อนจากการแคปจอของบอทด้วย WDA_EXCLUDEFROMCAPTURE (กรอบไม่รบกวนการตรวจ)"""

    def __init__(self, master):
        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.config(bg=OVERLAY_TRANS)
        try:
            self.top.attributes("-transparentcolor", OVERLAY_TRANS)
        except tk.TclError:
            pass
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        self.top.geometry(f"{sw}x{sh}+0+0")
        self.canvas = tk.Canvas(self.top, bg=OVERLAY_TRANS, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.top.update_idletasks()
        self.capture_safe = self._exclude_from_capture()
        self.visible = False
        self.top.withdraw()

    def _exclude_from_capture(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetAncestor(self.canvas.winfo_id(), 2)
            return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11))
        except Exception:
            return False

    def update_boxes(self, p):
        self.canvas.delete("all")
        drew = False
        if p:
            pb = p.get("player_box")
            ax = ay = None                     # จุดยึดตัวเอง (ไว้ลากเส้นไปหามอน)
            if pb:
                x, y, w, h = pb
                self.canvas.create_rectangle(x - 4, y - 4, x + w + 4, y + h + 4,
                                             outline="#00e05a", width=2)
                self.canvas.create_text(x, y - 12, text="ตัวเรา ✓", fill="#00e05a",
                                        anchor="w", font=("Segoe UI", 11, "bold"))
                ax = x + w // 2
                ay = y + h // 2 + int(w * config.PLAYER_BODY_DY)
                drew = True
            lb = p.get("lv_box")
            if lb and p.get("lv_score", 0) >= config.MONSTER_THRESHOLD:
                x, y, w, h = lb
                cx = x + w // 2
                cy = y + h // 2 + int(w * config.SEEK_BODY_DY)
                self.canvas.create_rectangle(x - 4, y - 4, x + w + 4, y + h + 4,
                                             outline="#ffc832", width=2)
                self.canvas.create_text(x, y - 12, text="มอน", fill="#ffc832",
                                        anchor="w", font=("Segoe UI", 11, "bold"))
                if ax is not None:             # เส้นทิศที่บอทกำลังเดิน
                    self.canvas.create_line(ax, ay, cx, cy, fill="#00e05a",
                                            width=1, dash=(6, 4))
                drew = True
        if drew and not self.visible:
            self.top.deiconify()
            self.visible = True
        elif not drew and self.visible:
            self.top.withdraw()
            self.visible = False

    def hide(self):
        self.canvas.delete("all")
        if self.visible:
            self.top.withdraw()
            self.visible = False


STATE_TH = {
    "STOPPED": ("หยุดอยู่", SUB),
    "RUNNING": ("เริ่มแล้ว", GREEN),
    "SEARCH": ("กำลังหามอน...", BLUE),
    "SEEK": ("เดินเข้าหามอน", BLUE),
    "BATTLE": ("กำลังสู้", "#ff9f43"),
    "CATCH": ("หน้า Catch", PURPLE),
    "SHINY!": ("★ เจอ SHINY!", GOLD),
    "PRISMATIC!": ("✦ เจอ PRISMATIC!", PURPLE),
}


def app_version():
    try:
        return open(resource("VERSION"), encoding="utf-8-sig").read().strip()
    except OSError:
        return "?"


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            return json.load(open(HISTORY_FILE, encoding="utf-8"))
        except Exception:
            return []
    return []


def save_history(items):
    try:
        json.dump(items, open(HISTORY_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception:
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.bot = Bot()
        self.history = load_history()
        self.running = False

        root.title("Evomon Auto-Hunter")
        root.geometry("780x900")
        root.minsize(700, 780)

        self.f_title = ctk.CTkFont("Segoe UI", 22, "bold")
        self.f_head = ctk.CTkFont("Segoe UI", 14, "bold")
        self.f_body = ctk.CTkFont("Segoe UI", 13)
        self.f_small = ctk.CTkFont("Segoe UI", 11)
        self.f_stat = ctk.CTkFont("Segoe UI", 24, "bold")
        self.f_mono = ctk.CTkFont("Consolas", 12)

        self._build()
        self._refresh_history()
        self.overlay = Overlay(root)
        self.root.after(80, self._poll)
        self.root.after(200, self._check_roblox)
        self.root.after(300, self._tick_overlay)

    # ================= layout =================
    def _build(self):
        # ---------- Header ----------
        self.header = ctk.CTkFrame(self.root, fg_color="transparent")
        self.header.pack(fill="x", padx=16, pady=(14, 6))

        left = ctk.CTkFrame(self.header, fg_color="transparent")
        left.pack(side="left")
        ctk.CTkLabel(left, text="Evomon Auto-Hunter", font=self.f_title).pack(anchor="w")
        ctk.CTkLabel(left, text=f"v{app_version()} • หา Shiny/Prismatic อัตโนมัติ",
                     font=self.f_small, text_color=SUB).pack(anchor="w")

        self.lbl_rbx = ctk.CTkLabel(self.header, text="● Roblox: ...", font=self.f_small,
                                    text_color=SUB, fg_color=("gray85", "gray17"),
                                    corner_radius=14, padx=12, pady=6)
        self.lbl_rbx.pack(side="right")

        # ---------- ปุ่มใหญ่ + สถานะ ----------
        ctrl = ctk.CTkFrame(self.root, corner_radius=16)
        ctrl.pack(fill="x", padx=16, pady=6)

        self.btn_main = ctk.CTkButton(ctrl, text="▶   เริ่มบอท", font=self.f_head,
                                      height=52, width=200, corner_radius=12,
                                      fg_color=GREEN, hover_color=GREEN_HOVER,
                                      command=self.on_toggle)
        self.btn_main.pack(side="left", padx=14, pady=14)

        st = ctk.CTkFrame(ctrl, fg_color="transparent")
        st.pack(side="left", padx=6)
        ctk.CTkLabel(st, text="สถานะ", font=self.f_small,
                     text_color=SUB).pack(anchor="w")
        self.lbl_state = ctk.CTkLabel(st, text="หยุดอยู่", font=self.f_head,
                                      text_color=SUB)
        self.lbl_state.pack(anchor="w")

        ctk.CTkLabel(ctrl, text="F8 = หยุดฉุกเฉิน\n(กดได้แม้อยู่ในเกม)",
                     font=self.f_small, text_color=SUB,
                     justify="right").pack(side="right", padx=14)

        # ---------- แถบแจ้งเตือนเจอ shiny/prismatic (ซ่อนไว้) ----------
        self.alert = ctk.CTkFrame(self.root, corner_radius=14,
                                  border_width=2, border_color=GOLD)
        self.lbl_alert = ctk.CTkLabel(self.alert, text="", font=self.f_head,
                                      text_color=GOLD, justify="left")
        self.lbl_alert.pack(anchor="w", padx=14, pady=(12, 4))
        arow = ctk.CTkFrame(self.alert, fg_color="transparent")
        arow.pack(anchor="w", padx=14, pady=(0, 12))
        ctk.CTkButton(arow, text="▶ ล่าต่อ (จัดการในเกมแล้ว)", font=self.f_body,
                      corner_radius=10, command=lambda: self.resolve("continue")
                      ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(arow, text="ปิดแจ้งเตือน", font=self.f_body, corner_radius=10,
                      fg_color="transparent", border_width=1, border_color=SUB,
                      text_color=SUB, hover_color=("gray80", "gray25"),
                      command=self._hide_alert).pack(side="left")

        # ---------- การ์ดตัวเลข ----------
        stats = ctk.CTkFrame(self.root, fg_color="transparent")
        stats.pack(fill="x", padx=16, pady=6)
        stats.grid_columnconfigure((0, 1, 2), weight=1, uniform="s")
        self.stat_mob = self._stat_card(stats, 0, "เจอมอนรอบนี้", "0", GREEN)
        self.stat_shiny = self._stat_card(stats, 1, "★ Shiny สะสม", "0", GOLD)
        self.stat_pris = self._stat_card(stats, 2, "✦ Prismatic สะสม", "0", PURPLE)

        # ---------- แท็บ ----------
        self.tabs = ctk.CTkTabview(self.root, corner_radius=16,
                                   segmented_button_selected_color=BLUE)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(6, 14))
        t_set = self.tabs.add("  ตั้งค่า  ")
        t_hist = self.tabs.add("  ประวัติ  ")
        t_log = self.tabs.add("  Log  ")
        self._build_settings(t_set)
        self._build_hist(t_hist)
        self._build_log(t_log)

    def _stat_card(self, parent, col, title, value, color):
        c = ctk.CTkFrame(parent, corner_radius=14)
        c.grid(row=0, column=col, sticky="nsew", padx=4)
        ctk.CTkLabel(c, text=title, font=self.f_small,
                     text_color=SUB).pack(anchor="w", padx=14, pady=(10, 0))
        lbl = ctk.CTkLabel(c, text=value, font=self.f_stat, text_color=color)
        lbl.pack(anchor="w", padx=14, pady=(0, 10))
        return lbl

    def _card(self, parent, title):
        c = ctk.CTkFrame(parent, corner_radius=14)
        c.pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(c, text=title, font=self.f_head).pack(anchor="w",
                                                           padx=14, pady=(10, 2))
        return c

    def _build_settings(self, p):
        # --- กล้อง & หน้าต่าง ---
        cam = self._card(p, "กล้อง & หน้าต่าง")
        self.sw_cam = ctk.CTkSwitch(cam, text="ตั้งมุมกล้องอัตโนมัติ + ล็อกมุม (ตัวอยู่กลางจอ)",
                                    font=self.f_body, command=self.on_settings)
        if self.bot.settings.camera_on_start:
            self.sw_cam.select()
        self.sw_cam.pack(anchor="w", padx=14, pady=2)
        self.sw_focus = ctk.CTkSwitch(cam, text="บังคับให้อยู่หน้าต่าง Roblox ตลอด (สลับจอแล้วดึงกลับ)",
                                      font=self.f_body, command=self.on_settings)
        if self.bot.settings.force_focus:
            self.sw_focus.select()
        self.sw_focus.pack(anchor="w", padx=14, pady=2)
        ctk.CTkButton(cam, text="🎥  ตั้งมุมกล้องเดี๋ยวนี้", font=self.f_body,
                      corner_radius=10, fg_color="transparent", border_width=1,
                      border_color=BLUE, text_color=BLUE,
                      hover_color=("gray80", "gray25"),
                      command=self.on_camera).pack(anchor="w", padx=14, pady=(8, 12))

        # --- overlay ---
        ov = self._card(p, "กรอบบนจอ (Overlay)")
        self.sw_overlay = ctk.CTkSwitch(
            ov, text="ตีกรอบบนจอ: ตัวเรา (เขียว) / มอนเป้าหมาย (เหลือง) ให้เห็นว่าบอทจับอยู่",
            font=self.f_body)
        if config.OVERLAY_DEFAULT:
            self.sw_overlay.select()
        self.sw_overlay.pack(anchor="w", padx=14, pady=(2, 12))

        # --- วิธีทำงาน ---
        info = self._card(p, "บอททำอะไรให้บ้าง")
        ctk.CTkLabel(
            info, font=self.f_small, text_color=SUB, justify="left", wraplength=640,
            text="เดินหามอน → สู้อัตโนมัติ → ไม่จับเอง (ไม่กด E) → หาตัวต่อไปวนไปเรื่อยๆ\n"
                 "เจอ Shiny/Prismatic เมื่อไร: หยุดทันที + เสียงเตือน + เด้งแจ้งเตือน "
                 "ให้คุณเข้าไปจัดการในเกมเอง").pack(anchor="w", padx=14, pady=(0, 12))

    def _build_hist(self, p):
        self.hist_frame = ctk.CTkScrollableFrame(p, fg_color="transparent")
        self.hist_frame.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_log(self, p):
        self.log = ctk.CTkTextbox(p, font=self.f_mono, corner_radius=10,
                                  state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

    # ================= actions =================
    def on_settings(self):
        s = self.bot.settings
        s.camera_on_start = bool(self.sw_cam.get())
        s.force_focus = bool(self.sw_focus.get())

    def on_toggle(self):
        if self.running:
            self.bot.stop()
        else:
            self.on_settings()
            self._hide_alert()
            self.bot.start()

    def on_camera(self):
        self.on_settings()
        threading.Thread(target=self.bot.setup_camera, daemon=True).start()

    def resolve(self, choice):
        if choice == "continue":
            self._hide_alert()
            self.bot.start()

    # ================= helpers =================
    def _set_running(self, running):
        self.running = running
        if running:
            self.btn_main.configure(text="■   หยุดบอท", fg_color=RED,
                                    hover_color=RED_HOVER)
        else:
            self.btn_main.configure(text="▶   เริ่มบอท", fg_color=GREEN,
                                    hover_color=GREEN_HOVER)

    def _set_state(self, raw):
        th, color = STATE_TH.get(raw, (raw, SUB))
        self.lbl_state.configure(text=th, text_color=color)
        if raw == "RUNNING":
            self._set_running(True)
        elif raw == "STOPPED":
            self._set_running(False)

    def _show_found(self, ftype):
        """เจอ shiny/prismatic -> บันทึกประวัติอัตโนมัติ + เด้งแจ้งเตือน (บอทหยุดแล้ว)"""
        self.history.insert(0, {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "type": ftype})
        save_history(self.history)
        self._refresh_history()
        self._append_log(f"[★] เจอ {ftype.upper()} -> บันทึกประวัติแล้ว, บอทหยุด")
        label = "★ SHINY!" if ftype == "shiny" else "✦ PRISMATIC!"
        color = GOLD if ftype == "shiny" else PURPLE
        self.lbl_alert.configure(
            text=f"{label}  เจอแล้ว — บอทหยุด + ไม่กด E\n"
                 f"ไปจัดการในเกมได้เลย (บันทึกประวัติให้แล้ว)", text_color=color)
        self.alert.configure(border_color=color)
        self.alert.pack(fill="x", padx=16, pady=6, after=self.header)
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.attributes("-topmost", False)

    def _hide_alert(self):
        self.alert.pack_forget()

    def _refresh_history(self):
        for w in self.hist_frame.winfo_children():
            w.destroy()
        shiny = sum(1 for it in self.history if it["type"] == "shiny")
        pris = sum(1 for it in self.history if it["type"] == "prismatic")
        self.stat_shiny.configure(text=str(shiny))
        self.stat_pris.configure(text=str(pris))

        if not self.history:
            ctk.CTkLabel(self.hist_frame, text="ยังไม่เจอ Shiny/Prismatic เลย — สู้ๆ!",
                         font=self.f_body, text_color=SUB).pack(pady=20)
            return
        for it in self.history:
            row = ctk.CTkFrame(self.hist_frame, corner_radius=10)
            row.pack(fill="x", pady=3, padx=4)
            is_shiny = it["type"] == "shiny"
            ctk.CTkLabel(row, text="★ SHINY" if is_shiny else "✦ PRISMATIC",
                         font=self.f_head, width=130, anchor="w",
                         text_color=GOLD if is_shiny else PURPLE
                         ).pack(side="left", padx=(12, 6), pady=8)
            ctk.CTkLabel(row, text=it["time"], font=self.f_mono,
                         text_color=SUB).pack(side="left")

    def _append_log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ================= polling =================
    def _tick_overlay(self):
        try:
            if self.running and self.sw_overlay.get():
                p = self.bot._snap()
                fresh = p["ts"] and (time.time() - p["ts"] < 2.0)
                self.overlay.update_boxes(p if fresh else None)
            else:
                self.overlay.hide()
        except Exception:
            pass
        self.root.after(150, self._tick_overlay)

    def _check_roblox(self):
        try:
            if roblox.is_running(config.ROBLOX_PROCESS):
                if roblox.is_foreground(config.ROBLOX_PROCESS):
                    self.lbl_rbx.configure(text="●  Roblox: พร้อม", text_color=GREEN)
                else:
                    self.lbl_rbx.configure(text="●  Roblox: เปิดอยู่ (ยังไม่โฟกัส)",
                                           text_color=GOLD)
            else:
                self.lbl_rbx.configure(text="●  Roblox: ยังไม่เปิด", text_color=RED)
        except Exception:
            pass
        self.root.after(1500, self._check_roblox)

    def _poll(self):
        try:
            while True:
                kind, data = self.bot.q.get_nowait()
                if kind == "log":
                    self._append_log(data)
                elif kind == "state":
                    self._set_state(data)
                elif kind == "catch":
                    self.stat_mob.configure(text=str(data))
                elif kind == "found":
                    self._show_found(data)
        except Exception:
            pass
        self.root.after(80, self._poll)


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()