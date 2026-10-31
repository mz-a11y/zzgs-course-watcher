# -*- coding: utf-8 -*-
"""图形界面离线自测：用假的 Kingo 跑通巡逻线程、队列回灌和表格填充，不发任何网络请求。"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zzgs_grab as z
import zzgs_gui as gui

FAKE = {}


class FakeKingo(object):
    def __init__(self, cookie_file=None, secret_file=None, interval=2.4, creds=None, sink=None):
        self.interval = interval
        self.cookie_file = cookie_file
        self.creds = creds
        self.sink = sink
        self.tr = {"lcmc": "假轮次", "qssj": "0", "jssj": "0", "xh": "200000000001"}

    def say(self, m):
        pass

    def courses(self, only=None):
        return [{"kcdm": "90000001", "kc": "[51220001]假线上课A"},
                {"kcdm": "90000002", "kc": "[51220002]假线上课B"},
                {"kcdm": "90000003", "kc": "[51220003]假线上课C"}]

    def classes(self, kcdm):
        full = {"skbjdm": kcdm + "-001", "teacher": "网络教师", "cap": 32, "used": 32,
                "free": 0, "place": "线上教学", "online": True, "selectable": True}
        if FAKE.get("free_at") == kcdm:
            full = dict(full, free=3, used=29)
        return [full]

    def submit(self, c, x):
        FAKE["submitted"] = (c["kcdm"], x["skbjdm"])
        return True


def pump(app, seconds=6.0):
    """转主循环，直到线程结束且队列里的东西已经回灌进界面。"""
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.05)
        if not (app.worker and app.worker.is_alive()) and app.q.empty():
            time.sleep(0.35)      # 给 after(200) 的 _drain 留一次触发窗口
            app.root.update()
            break


def pump_app(app, seconds=3.0):
    """只转主循环，等后台线程把队列灌完（公告这类不占 worker 线程的功能用）。"""
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.05)


def text_of(app):
    app.txt.configure(state="normal")
    t = app.txt.get("1.0", "end")
    app.txt.configure(state="disabled")
    return t


def rows_of(app):
    return [app.tree.item(i)["values"] for i in app.tree.get_children()]


def main():
    z.Kingo = FakeKingo
    z.connect = lambda k, retry=60, tries=3: k.tr
    z._notify = lambda msg, path=None: FAKE.setdefault("notified", []).append(msg)
    gui.META_URL = ""          # 自测必须零网络：别让 App 启动时去拉 Gitee
    root = tk.Tk()
    root.withdraw()                      # 自测不弹到桌面
    app = gui.App(root)
    app.e_user.insert(0, "200000000001")
    app.e_pwd.insert(0, "fakepw")

    print("[A] 全满 + 2 轮：不应有命中行")
    app.v_rounds.set(2)
    FAKE.clear()
    app.on_start()
    pump(app, 5)
    txt = text_of(app)
    assert "第1轮扫描完毕" in txt and "第2轮扫描完毕" in txt, txt[-400:]
    assert "命中" not in txt, txt
    assert rows_of(app) == []
    print("    日志尾部:", txt.strip().splitlines()[-2:])
    print("    状态栏:", app.status.get())

    print("[B] 第 2 门放 3 个空位（只提示模式）：应命中并停下")
    app._clear()
    FAKE["free_at"] = "90000002"
    app.v_rounds.set(0)
    app.on_start()
    pump(app, 5)
    txt = text_of(app)
    r = rows_of(app)
    assert "命中" in txt, txt[-400:]
    assert len(r) == 1, r
    assert r[0][0] == "线上" and "假线上课B" in r[0][1] and r[0][6] == 3, r[0]
    assert "提交" not in str(r[0][8]) and "已提示" in r[0][8], r[0]
    assert "submitted" not in FAKE, "只提示模式绝不能发写请求"
    assert FAKE.get("notified") and "假线上课B" in FAKE["notified"][0], FAKE.get("notified")
    print("    命中行:", r[0])
    print("    报警文本:", FAKE["notified"][0])

    print("[C] 开启自动提交：应调用 submit 并标记成功")
    app._clear()
    FAKE.clear()
    FAKE["free_at"] = "90000003"
    app.v_commit.set(True)               # 直接设变量，跳过确认弹窗（弹窗本身另行核对代码）
    app.on_start()
    pump(app, 5)
    assert FAKE.get("submitted") == ("90000003", "90000003-001"), FAKE
    r = rows_of(app)
    assert r and r[0][8] == "自动提交成功", r
    assert "提交结果：成功，已选上" in text_of(app), text_of(app)[-300:]
    assert app.patrol.stats["status"] == "已选上", app.patrol.stats
    print("    submit 收到:", FAKE["submitted"])

    print("[D] 「列出现在有余量的课」：应列出 1 行且带进度")
    app._clear()
    app.v_commit.set(False)
    FAKE.clear()
    FAKE["free_at"] = "90000001"
    app.on_list()
    pump(app, 5)
    r = rows_of(app)
    txt = text_of(app)
    assert len(r) == 1 and "有空" in r[0][8], r
    assert "进度 3/3" in txt and "共 1 个班有余量" in txt, txt[-300:]
    print("    列出:", r[0])

    print("[E] 未填密码应被拦住，不发登录请求")
    app._clear()
    app.e_pwd.delete(0, "end")
    try:
        app._kingo()
        raise AssertionError("空密码没被拦住")
    except z.NeedSecret as e:
        print("    拦住了:", e)

    print("[F] 公告条 + 新版本提醒（远程 JSON 用假的模拟，不联网）")
    BOX = []

    class FakeBox(object):
        def __init__(self, log):
            self.log = log

        def askyesno(self, title, *a, **k):
            self.log.append(("ask", title))
            return False

        def showinfo(self, title, *a, **k):
            self.log.append(("info", title))

    gui.messagebox = FakeBox(BOX)
    z.fetch_meta = lambda url, timeout=3.0: {"notice": "选课 10-16 12:00 截止", "popup": True,
                                             "version": "9.9", "url": "http://x/新.exe"}
    st = os.path.join(gui.HERE, "update_state.txt")
    if os.path.exists(st):
        os.remove(st)
    app2 = gui.App(tk.Toplevel(root))
    pump_app(app2, 3)
    assert app2.notice.winfo_manager() == "pack", "公告条没显示出来"
    assert "10-16 12:00" in app2.v_notice.get(), app2.v_notice.get()
    assert ("info", "公告") in BOX and ("ask", "发现新版本 v9.9") in BOX, BOX
    assert open(st, encoding="utf-8").read().strip() == "9.9", "没记住已提醒过的版本"
    print("    公告条:", app2.v_notice.get())
    print("    弹窗:", BOX)
    app2._dismiss_notice()
    assert app2.notice.winfo_manager() == "" and app2.v_notice.get() == "", "点一下没关掉"
    print("    点一下公告条：已收起")

    print("[G] 同一个新版本，第二次打开不该再弹")
    BOX2 = []
    gui.messagebox = FakeBox(BOX2)
    app3 = gui.App(tk.Toplevel(root))
    pump_app(app3, 3)
    assert BOX2 == [("info", "公告")], BOX2
    os.remove(st)
    print("    第二次只剩公告，没有重复的版本弹窗")

    print("\n全部通过：线程回灌、命中判定、写操作开关、输入校验、公告与升级提醒都对得上")
    root.destroy()


if __name__ == "__main__":
    main()
