# -*- coding: utf-8 -*-
"""郑州工商学院 通识选修课 余量巡逻界面（桌面版，给同学自己用）。

跑法：  python zzgs_gui.py     （需要 requests；见 使用说明.md）
所有网络请求都还是 zzgs_grab.Kingo 那一套：默认 2.4 秒间隔、单实例。
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zzgs_grab as z

if getattr(sys, "frozen", False):
    # 打包成 exe 后 __file__ 指向临时解包目录，配置必须落在 exe 自己旁边
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
COLS = ("线上", "课程", "班号", "教师", "限", "已选", "可选", "地点", "结果")

APP_VERSION = "1.3"
# 公告/版本的远程 JSON 地址。留空 = 只读同目录的 公告.txt，不联网
META_URL = ""


class App(object):
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.stop = threading.Event()
        self.worker = None
        self.patrol = None
        self.opts = {}
        root.title("郑州工商 选课余量巡逻  v" + APP_VERSION)
        root.geometry("980x640")
        self._build()
        self._prefill()
        threading.Thread(target=self._check_meta, daemon=True).start()
        self.root.after(200, self._drain)
        self.root.after(1000, self._tick)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 界面 ----------
    def _build(self):
        top = ttk.LabelFrame(self.root, text=" 1. 账号（只用你自己的） ")
        top.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Label(top, text="学号").grid(row=0, column=0, padx=(8, 2), pady=6)
        self.e_user = ttk.Entry(top, width=16)
        self.e_user.grid(row=0, column=1, padx=2)
        ttk.Label(top, text="密码").grid(row=0, column=2, padx=(10, 2))
        self.e_pwd = ttk.Entry(top, width=20, show="*")
        self.e_pwd.grid(row=0, column=3, padx=2)
        self.v_remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="记住账号密码（写进本机 secret.txt；公共电脑别勾）",
                        variable=self.v_remember).grid(row=0, column=4, padx=12)
        self.btn_login = ttk.Button(top, text="测试登录", command=self.on_login)
        self.btn_login.grid(row=0, column=5, padx=(0, 8))

        self.v_notice = tk.StringVar(value="")
        self.notice = tk.Label(self.root, textvariable=self.v_notice, anchor="w", justify="left",
                               background="#fff3cd", foreground="#6b5200", wraplength=920,
                               padx=10, pady=5, cursor="hand2")
        self.notice.bind("<Button-1>", self._dismiss_notice)

        opt = ttk.LabelFrame(self.root, text=" 2. 盯什么 ")
        self.opt = opt
        opt.pack(fill="x", padx=10, pady=4)
        self.v_online = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="只要线上教学课（上课地点含「线上」，或教师是「网络教师」）",
                        variable=self.v_online).grid(row=0, column=0, columnspan=4, sticky="w", padx=8)
        self.v_commit = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(opt, text="发现余量后自动提交选课（会真改你的学籍数据！）",
                             variable=self.v_commit, command=self._confirm_commit)
        cb.grid(row=1, column=0, columnspan=4, sticky="w", padx=8)
        ttk.Label(opt, text="请求间隔(秒)").grid(row=2, column=0, sticky="e", padx=(8, 2))
        self.v_int = tk.DoubleVar(value=z.CFG["interval"])
        ttk.Spinbox(opt, from_=1.5, to=6.0, increment=0.1, width=6,
                    textvariable=self.v_int).grid(row=2, column=1, sticky="w")
        ttk.Label(opt, text="最多轮数(0=一直盯)").grid(row=2, column=2, sticky="e", padx=(12, 2))
        self.v_rounds = tk.IntVar(value=0)
        ttk.Spinbox(opt, from_=0, to=9999, increment=1, width=8,
                    textvariable=self.v_rounds).grid(row=2, column=3, sticky="w")

        ctl = ttk.Frame(self.root)
        ctl.pack(fill="x", padx=10, pady=4)
        self.btn_start = ttk.Button(ctl, text="开始巡逻", command=self.on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(ctl, text="停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.btn_list = ttk.Button(ctl, text="列出现在有余量的课（只读，约 6 分钟）", command=self.on_list)
        self.btn_list.pack(side="left", padx=12)
        ttk.Button(ctl, text="清空下面两栏", command=self._clear).pack(side="right")

        body = ttk.Panedwindow(self.root, orient="vertical")
        body.pack(fill="both", expand=True, padx=10, pady=4)
        tf = ttk.Labelframe(body, text=" 命中 / 有余量 ")
        self.tree = ttk.Treeview(tf, columns=COLS, show="headings", height=8)
        widths = (56, 250, 150, 80, 46, 46, 46, 150, 120)
        for i, c in enumerate(COLS):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=widths[i], anchor="w" if i in (1, 7, 8) else "center")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        vsb.pack(side="left", fill="y", pady=6)
        body.add(tf, weight=1)

        lf = ttk.Labelframe(body, text=" 运行日志 ")
        self.txt = tk.Text(lf, height=12, wrap="word", state="disabled",
                           font=("Consolas", 10))
        lb = ttk.Scrollbar(lf, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=lb.set)
        self.txt.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        lb.pack(side="left", fill="y", pady=6)
        body.add(lf, weight=1)

        self.status = tk.StringVar(value="待命")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w").pack(
            fill="x", side="bottom", padx=10, pady=(0, 8))

    def _prefill(self):
        """secret.txt 已有账号时把学号填上，密码仍旧要你亲手输。"""
        p = os.path.join(HERE, "secret.txt")
        try:
            line = open(p, encoding="utf-8", errors="ignore").read().strip().splitlines()[0]
            user = line.split(":", 1)[0].strip()
            if user:
                self.e_user.insert(0, user)
                self._log("已从上次的 secret.txt 填入学号（密码没读，请自己输）")
        except Exception:
            pass

    # ---------- 公告 / 新版本（后台拉一次，拉不到就什么都不做） ----------
    def _check_meta(self):
        meta = z.fetch_meta(META_URL) or {}
        text = (meta.get("notice") or "").strip()
        until = (meta.get("notice_until") or "").strip()
        if text and until and time.strftime("%Y-%m-%d") > until:
            text = ""          # 过了生效日期自动不显示，省得你忘了删
        if not text:
            local = os.path.join(HERE, "公告.txt")
            if os.path.exists(local):
                text = open(local, encoding="utf-8", errors="ignore").read().strip()
        if text:
            self.q.put(("notice", (text, bool(meta.get("popup")))))
        ver = (meta.get("version") or "").strip()
        url = (meta.get("url") or meta.get("download") or "").strip()
        if ver and z.is_newer(ver, APP_VERSION):
            self.q.put(("update", (ver, url)))

    def _dismiss_notice(self, _event=None):
        self.v_notice.set("")
        self.notice.pack_forget()

    def _offer_update(self, ver, url):
        """同一个新版本只提醒一次，不然天天弹窗会被骂。"""
        st = os.path.join(HERE, "update_state.txt")
        try:
            seen = open(st, encoding="utf-8").read().strip()
        except Exception:
            seen = ""
        if seen == ver:
            return
        try:
            open(st, "w", encoding="utf-8").write(ver)
        except Exception:
            pass
        if not url:
            messagebox.showinfo("发现新版本 v%s" % ver,
                                "当前 v%s，最新 v%s。\n\n这个版本没填写下载地址，请联系发布者。"
                                % (APP_VERSION, ver))
        elif messagebox.askyesno("发现新版本 v%s" % ver,
                                "当前 v%s，最新 v%s。\n\n现在打开下载页吗？（不打开也能继续用）"
                                % (APP_VERSION, ver)):
            webbrowser.open(url)

    # ---------- 通用 ----------
    def _log(self, msg):
        self.q.put(("log", msg))

    def _clear(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _kingo(self):
        user = self.e_user.get().strip()
        pwd = self.e_pwd.get().strip()
        if not user or not pwd:
            raise z.NeedSecret("学号和密码都要填（只有 Cookie 不填密码也可以，但那样就得手动更新 cookies.txt）")
        if self.v_remember.get():
            with open(os.path.join(HERE, "secret.txt"), "w", encoding="utf-8") as f:
                f.write("%s:%s" % (user, pwd))
            self._log("账号密码已记在本机 secret.txt")
        return z.Kingo(cookie_file=os.path.join(HERE, "cookies.txt"),
                       secret_file=os.path.join(HERE, "secret.txt"),
                       interval=self.v_int.get(), creds=(user, pwd), sink=self._log)

    # ---------- 动作 ----------
    def on_login(self):
        self._start(self._job_login, "测试登录", (self.btn_login,))

    def on_start(self):
        self._start(self._job_patrol, "巡逻", (self.btn_start, self.btn_list), stoppable=True)

    def on_list(self):
        self._start(self._job_list, "扫描", (self.btn_start, self.btn_list), stoppable=True)

    def on_stop(self):
        self.stop.set()
        self._log("已请求停止：正在查的那一门课查完就停（最多等几秒）")
        self.btn_stop.configure(state="disabled")

    def _confirm_commit(self):
        if not self.v_commit.get():
            return
        ok = messagebox.askyesno(
            "确认要自动提交选课吗",
            "开启后，一旦发现有空的线上班，程序会直接替你提交选课，\n"
            "这会改变你的真实学籍数据，而且可能选到你不想要的班。\n\n"
            "确定要开启自动提交？（不确定就点否，先用只提示模式）")
        if not ok:
            self.v_commit.set(False)
            self._log("自动提交没开启：命中时只提示、只响铃，你自己去网页上点")
        else:
            self._log("⚠ 已开启自动提交：命中即选课")

    def _start(self, job, name, buttons, stoppable=False):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在运行", "上一个任务还没结束（%s）。要换任务请先点停止。" % name)
            return
        # Tk 的值只能在主线程读，所以在这里一次性拍快照给工作线程用
        try:
            k = self._kingo()
        except z.NeedSecret as e:
            self._log("[需要你处理] %s" % e)
            return
        self.opts = {"online": self.v_online.get(), "commit": self.v_commit.get(),
                     "rounds": self.v_rounds.get(), "interval": self.v_int.get()}
        self.stop.clear()
        for b in buttons:
            b.configure(state="disabled")
        self.btn_login.configure(state="disabled")
        if stoppable:
            self.btn_stop.configure(state="normal")
        self.worker = threading.Thread(target=self._guard, args=(job, name, k), daemon=True)
        self.worker.start()

    def _guard(self, job, name, k):
        try:
            job(k)
        except z.NeedSecret as e:
            self._log("[需要你处理] %s" % e)
        except z.StopRun as e:
            self._log("[停止] %s" % e)
        except z.SessionDead as e:
            self._log("[会话被顶掉了] %s\n    一个账号只能有一个在线会话。是不是还开着命令行巡逻、"
                      "或者别的电脑/浏览器也登了这个学号？关掉多余的，再点一次就行。" % e)
        except Exception as e:
            self._log("[出错了] %s：%s" % (type(e).__name__, e))
        finally:
            self.q.put(("done", name))

    def _job_login(self, k):
        z.connect(k, retry=10, tries=1)
        tr = k.tr
        self._log("登录可用：轮次=%s 窗口=%s ~ %s 学号=%s"
                  % (tr.get("lcmc"), tr.get("qssj"), tr.get("jssj"), z.mask(tr.get("xh"))))
        cs = k.courses()
        self._log("可选课程 %d 门，可以开始巡逻了" % len(cs))

    def _job_patrol(self, k):
        o = self.opts
        z.connect(k, retry=int(o["interval"] * 20), tries=0)
        self.patrol = z.Patrol(
            k, online_only=o["online"], commit=o["commit"],
            rounds=o["rounds"], retry=int(o["interval"] * 20),
            log=self._log, on_hit=self._hit, should_stop=self.stop.is_set)
        self._log("巡逻开始（%s）" % ("只盯线上课" if o["online"] else "全部课程"))
        self.patrol.run()

    def _job_list(self, k):
        online_only = self.opts["online"]
        z.connect(k, retry=10, tries=3)
        courses = k.courses()
        self._log("开始逐门查班级表（%d 门约 %d 分钟），只列真有余量的班"
                  % (len(courses), max(1, int(len(courses) * k.interval / 60))))
        n = 0
        for i, c in enumerate(courses, 1):
            if self.stop.is_set():
                self._log("已按要求停止扫描（扫到第 %d 门）" % i)
                return
            for x in k.classes(c["kcdm"]):
                if (x["free"] or 0) <= 0 or not x["selectable"]:
                    continue
                if online_only and not x.get("online"):
                    continue
                self._row(x, c["kc"], "有空，可去网页选")
                n += 1
            if i % 25 == 0 or i == len(courses):
                self._log("进度 %d/%d，目前列出 %d 个有空的班" % (i, len(courses), n))
        self._log("扫描完：%s 中共 %d 个班有余量"
                  % ("线上课" if online_only else "全部课程", n))
        if n == 0:
            self._log("一个空的都没有——这正是要开巡逻的原因，让它替你盯着。")

    def _hit(self, hit):
        self.q.put(("row", (hit, hit["course"], "自动提交成功" if hit["ok"] else
                            ("提交没成功" if hit["committed"] else "已提示，快去网页选"))))
        z._notify(z.hit_line(hit), path=os.path.join(HERE, "hits.log"))

    def _row(self, x, course, result):
        self.q.put(("row", (x, course, result)))

    # ---------- 回灌界面 ----------
    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append(payload)
                elif kind == "row":
                    x, course, result = payload
                    self.tree.insert("", "end", values=(
                        "线上" if x.get("online") else "线下", course, x["skbjdm"], x.get("teacher", ""),
                        x.get("cap", ""), x.get("used", ""), x["free"], x.get("place", ""), result))
                elif kind == "notice":
                    text, popup = payload
                    self.v_notice.set("📢 " + text + "　（点一下这条可以关掉）")
                    self.notice.pack(fill="x", padx=10, pady=(0, 4), before=self.opt)
                    if popup:
                        messagebox.showinfo("公告", text)
                elif kind == "update":
                    self._offer_update(*payload)
                elif kind == "done":
                    for b in (self.btn_start, self.btn_list, self.btn_login):
                        b.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self._append("—— %s 任务结束 ——" % payload)
        except queue.Empty:
            pass
        self.root.after(200, self._drain)

    def _append(self, msg):
        self.txt.configure(state="normal")
        self.txt.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _tick(self):
        s = self.patrol.stats if self.patrol else None
        if s:
            self.status.set("状态：%s｜第 %d 轮｜课程池 %d 门｜线上班 %d 个｜命中 %d 次｜间隔 %.1f 秒"
                            % (s["status"], s["round"], s["pool"], s["online"], s["hits"],
                               self.v_int.get()))
        elif not (self.worker and self.worker.is_alive()):
            self.status.set("待命：填学号密码 → 测试登录 → 开始巡逻")
        self.root.after(1000, self._tick)

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("正在运行", "巡逻还没停，确定要退出吗？（退出就不再盯了）"):
                return
            self.stop.set()
        self.root.destroy()


def main():
    if getattr(sys, "frozen", False):
        os.chdir(HERE)
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    if not z.acquire_single_instance():
        messagebox.showerror(
            "已经开着一个了",
            "这台电脑上已经有一个本程序在跑（或者命令行巡逻正在跑）。\n\n"
            "一个账号只能有一个在线会话，开两个会互相把对方踢下线，"
            "两边都扫不稳。\n请先关掉多余的那个，再打开这个。")
        root.destroy()
        return
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
