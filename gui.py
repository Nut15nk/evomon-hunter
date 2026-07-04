# ===================================================================
#  gui.py — หน้าต่างควบคุมบอท Evomon (Tkinter)
#  รัน:  py -3.12 gui.py   (เปิด PowerShell แบบ Administrator ก่อน)
# ===================================================================

import json
import os
import time
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

from bot import Bot
import roblox
import config
from respath import userfile

HISTORY_FILE = userfile("history.json")

# ---- ธีมสี ----
BG = "#15151f"
PANEL = "#20202e"
CARD = "#2a2a3c"
TXT = "#e6e6f0"
SUB = "#9aa0b4"
ACCENT = "#7aa2f7"
GREEN = "#39d353"
RED = "#f85149"
GOLD = "#ffd700"
PURPLE = "#c792ea"


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
        self.found_type = None

        root.title("Evomon Auto-Hunter")
        root.geometry("760x880")
        root.minsize(700, 800)
        root.configure(bg=BG)

        self._style()
        self._build()
        self._refresh_history()
        self.root.after(80, self._poll)
        self.root.after(200, self._check_roblox)

    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=SUB,
                     padding=(18, 8), font=("Segoe UI", 10, "bold"))
        st.map("TNotebook.Tab", background=[("selected", CARD)],
               foreground=[("selected", TXT)])

    # ================= layout =================
    def _build(self):
        # ---------- Header (เห็นตลอด) ----------
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=12, pady=(12, 6))

        self.btn_start = tk.Button(head, text="▶  เริ่ม", bg=GREEN, fg="black",
                                   font=("Segoe UI", 12, "bold"), relief="flat",
                                   width=10, command=self.on_start, cursor="hand2")
        self.btn_start.pack(side="left")
        self.btn_stop = tk.Button(head, text="■  หยุด", bg=RED, fg="white",
                                  font=("Segoe UI", 12, "bold"), relief="flat",
                                  width=10, command=self.on_stop, cursor="hand2")
        self.btn_stop.pack(side="left", padx=8)

        rs = tk.Frame(head, bg=BG)
        rs.pack(side="right")
        self.lbl_state = tk.Label(rs, text="STOPPED", bg=BG, fg=GOLD,
                                  font=("Consolas", 14, "bold"))
        self.lbl_state.pack(anchor="e")
        self.lbl_rbx = tk.Label(rs, text="● Roblox: ...", bg=BG, fg=SUB,
                                font=("Segoe UI", 9))
        self.lbl_rbx.pack(anchor="e")

        # ---------- แถบแจ้งเตือนเจอ shiny/prismatic (ซ่อนไว้) ----------
        self.shiny_box = tk.Frame(self.root, bg="#3a2e10",
                                  highlightbackground=GOLD, highlightthickness=2)
        self.lbl_shiny = tk.Label(self.shiny_box, bg="#3a2e10", fg=TXT,
                                  font=("Segoe UI", 12, "bold"), justify="left")
        self.lbl_shiny.pack(anchor="w", padx=10, pady=(8, 2))
        bf = tk.Frame(self.shiny_box, bg="#3a2e10")
        bf.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(bf, text="▶ เริ่มต่อ (ไปจัดการในเกมแล้ว)", bg=ACCENT, fg="black",
                  relief="flat", font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=lambda: self.resolve("continue")).pack(side="left", padx=3)
        tk.Button(bf, text="ปิดแจ้งเตือน", bg=SUB, fg="black", relief="flat",
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=self._hide_shiny).pack(side="left", padx=3)

        # ---------- Notebook ----------
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=12, pady=8)
        self.tab_ctrl = tk.Frame(nb, bg=BG)
        self.tab_hist = tk.Frame(nb, bg=BG)
        self.tab_log = tk.Frame(nb, bg=BG)
        nb.add(self.tab_ctrl, text="ตั้งค่า")
        nb.add(self.tab_hist, text="ประวัติ")
        nb.add(self.tab_log, text="Log")

        self._build_ctrl(self.tab_ctrl)
        self._build_hist(self.tab_hist)
        self._build_log(self.tab_log)

        tk.Label(self.root, text="F8 = หยุดฉุกเฉิน (กดได้แม้อยู่ในเกม)",
                 bg=BG, fg="#666").pack(pady=(0, 6))

    def _card(self, parent, title):
        c = tk.LabelFrame(parent, text="  " + title + "  ", bg=CARD, fg=ACCENT,
                          font=("Segoe UI", 10, "bold"), relief="flat", bd=0)
        c.pack(fill="x", padx=10, pady=7, ipady=2)
        return c

    def _build_ctrl(self, p):
        # --- สกิล ---
        sk = self._card(p, "สกิล (กดเป็นตัวเลข ใช้ได้ทุกตัวละคร)")
        row = tk.Frame(sk, bg=CARD)
        row.pack(anchor="w", padx=8, pady=6)
        tk.Label(row, text="สกิลหลักที่วนกด:", bg=CARD, fg=TXT).pack(side="left")
        self.var_skill = tk.StringVar(value=self.bot.settings.primary_skill)
        for s in ("1", "2", "3"):
            tk.Radiobutton(row, text=s, variable=self.var_skill, value=s,
                           bg=CARD, fg=TXT, selectcolor="#3a3a50",
                           activebackground=CARD, font=("Segoe UI", 10, "bold"),
                           command=self.on_settings).pack(side="left", padx=6)

        self.var_s4 = tk.BooleanVar(value=self.bot.settings.use_skill4)
        tk.Checkbutton(sk, text="กดสกิล 4 อัตโนมัติด้วย (เมื่อพร้อม)",
                       variable=self.var_s4, bg=CARD, fg=TXT, selectcolor="#3a3a50",
                       activebackground=CARD, command=self.on_settings).pack(
            anchor="w", padx=8, pady=(0, 6))

        # --- กล้อง/หน้าต่าง ---
        cam = self._card(p, "กล้อง & หน้าต่าง")
        self.var_cam = tk.BooleanVar(value=self.bot.settings.camera_on_start)
        tk.Checkbutton(cam, text="ตั้งมุมกล้องอัตโนมัติ + ล็อกมุม (ตัวอยู่กลางจอ)",
                       variable=self.var_cam, bg=CARD, fg=TXT, selectcolor="#3a3a50",
                       activebackground=CARD, command=self.on_settings).pack(
            anchor="w", padx=8, pady=(4, 0))
        self.var_focus = tk.BooleanVar(value=self.bot.settings.force_focus)
        tk.Checkbutton(cam, text="บังคับให้อยู่หน้าต่าง Roblox ตลอด (สับจอแล้วดึงกลับ)",
                       variable=self.var_focus, bg=CARD, fg=TXT, selectcolor="#3a3a50",
                       activebackground=CARD, command=self.on_settings).pack(
            anchor="w", padx=8)
        tk.Button(cam, text="🎥 ตั้งมุมกล้องเดี๋ยวนี้", bg=ACCENT, fg="black",
                  relief="flat", font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self.on_camera).pack(anchor="w", padx=8, pady=8)

        # --- สถานะ ---
        info = self._card(p, "สถานะ")
        self.lbl_catch = tk.Label(info, text="เจอมอน: 0 ตัว", bg=CARD, fg=GREEN,
                                  font=("Segoe UI", 10))
        self.lbl_catch.pack(anchor="w", padx=8, pady=(4, 0))
        tk.Label(info, text="ไม่จับเอง (ไม่กด E) — สู้เสร็จก็เดินหาตัวต่อไป\n"
                            "ตรวจ shiny/prismatic จากแผง Obtain Rate (ซ้ายล่าง) เจอแล้วหยุด + แจ้งเตือน",
                 bg=CARD, fg=SUB, font=("Segoe UI", 9), justify="left",
                 wraplength=680).pack(anchor="w", padx=8, pady=(0, 6))

    def _build_hist(self, p):
        tk.Label(p, text="ตัวที่ได้ (Shiny / Prismatic)", bg=BG, fg=SUB,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        self.hist_list = tk.Listbox(p, bg="#12121c", fg=TXT, font=("Consolas", 10),
                                    relief="flat", highlightthickness=0,
                                    selectbackground=CARD)
        self.hist_list.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _build_log(self, p):
        self.log = scrolledtext.ScrolledText(p, bg="#0e0e16", fg="#cdd6f4",
                                             font=("Consolas", 9), relief="flat",
                                             highlightthickness=0)
        self.log.pack(fill="both", expand=True, padx=12, pady=12)

    # ================= actions =================
    def on_settings(self):
        s = self.bot.settings
        s.primary_skill = self.var_skill.get()
        s.use_skill4 = self.var_s4.get()
        s.camera_on_start = self.var_cam.get()
        s.force_focus = self.var_focus.get()

    def on_start(self):
        self.on_settings()
        self._hide_shiny()
        self.bot.start()

    def on_stop(self):
        self.bot.stop()

    def on_camera(self):
        self.on_settings()
        threading.Thread(target=self.bot.setup_camera, daemon=True).start()

    def resolve(self, choice):
        if choice == "continue":
            self._hide_shiny()
            self.bot.start()

    # ================= helpers =================
    def _show_found(self, ftype):
        """เจอ shiny/prismatic -> บันทึกประวัติอัตโนมัติ + เด้งแจ้งเตือน (บอทหยุดแล้ว)"""
        self.found_type = ftype
        self.history.insert(0, {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "type": ftype})
        save_history(self.history)
        self._refresh_history()
        self._append_log(f"[★] เจอ {ftype.upper()} -> บันทึกประวัติแล้ว, บอทหยุด")
        label = "★ SHINY!" if ftype == "shiny" else "✦ PRISMATIC!"
        self.lbl_shiny.config(
            text=f"{label}  เจอแล้ว — บอทหยุด + ไม่กด E\n"
                 f"ไปจัดการในเกมได้เลย (บันทึกประวัติให้แล้ว)")
        self.shiny_box.pack(fill="x", padx=12, pady=4, after=self.root.winfo_children()[0])

    def _hide_shiny(self):
        self.found_type = None
        self.shiny_box.pack_forget()

    def _refresh_history(self):
        self.hist_list.delete(0, "end")
        if not self.history:
            self.hist_list.insert("end", "  (ยังไม่มี)")
        for it in self.history:
            self.hist_list.insert(
                "end", f"  {it['type'].upper():10s} {it['time']}")

    def _append_log(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    # ================= polling =================
    def _check_roblox(self):
        try:
            if roblox.is_running(config.ROBLOX_PROCESS):
                if roblox.is_foreground(config.ROBLOX_PROCESS):
                    self.lbl_rbx.config(text="● Roblox: พร้อม", fg=GREEN)
                else:
                    self.lbl_rbx.config(text="● Roblox: เปิดอยู่ (ยังไม่โฟกัส)", fg=GOLD)
            else:
                self.lbl_rbx.config(text="● Roblox: ยังไม่เปิด", fg=RED)
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
                    self.lbl_state.config(text=data)
                elif kind == "catch":
                    self.lbl_catch.config(text=f"เจอมอน: {data} ตัว")
                elif kind == "found":
                    self._show_found(data)
        except Exception:
            pass
        self.root.after(80, self._poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
