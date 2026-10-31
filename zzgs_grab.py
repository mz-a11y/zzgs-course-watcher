# -*- coding: utf-8 -*-
"""郑州工商学院（青果 KINGOSOFT 高校教学综合管理服务平台）网上选课助手。

由 D:\\新建文件夹\\mdjnuqk-main（牡丹江师范学院版）迁移而来，接口路径、tableId、
列名、参数绑定方式全部按本校实机抓取结果重写。加密层沿用原项目的青果专用 DES。

用法（只读，默认安全）：
    python zzgs_grab.py probe                 # 列出全部可选课程及各班余量
    python zzgs_grab.py probe --only 体育      # 只看课程名含“体育”的
    python zzgs_grab.py watch --targets 51224785,51224803     # 盯余量，出现余量只提示
    python zzgs_grab.py watch --targets ... --commit          # 出现余量才真正提交选课

登录态：
    优先使用 cookies.txt（浏览器 F12 里复制 JSESSIONID=xxx，整串写入）。
    cookies.txt 失效且存在 secret.txt（内容一行：学号:密码）时自动重新登录。
    两个文件都只在本机读取，脚本不打印其内容。

安全约束：请求间隔默认 2.4 秒且不低于 1.5 秒；不并发；未加 --commit 不会调用
saveElectiveCourse.action（即不会改变真实学籍）。
"""
import argparse
import base64
import hashlib
import json
import os
import random
import re
import socket
import sys
import time

import requests

try:
    import kingo_des
except ImportError:
    sys.exit("[停止] 缺少 kingo_des.py，请与本脚本放在同一目录")

BASE = "http://zhxy2.ztbu.edu.cn:8081"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

CFG = {
    # 选课列表页与其数据表
    "xk_page": "/student/wsxk.zx.html",
    "table_course": "2568",
    "table_class": "6142",
    "popup_page": "/student/report/wsxk.zx_promt.jsp",
    # 接口前缀（本校 .action 挂在 /jw 下）
    "api_prefix": "/jw",
    "time_range": "/common/getWsxkTimeRange.action",
    "precheck": "/common/isSelectableSkbjdm.action",
    "save": "/common/saveElectiveCourse.action",
    "login": "/cas/logon.action",
    # 选课轮次参数：xktype 2=正选；kcfw 必须是 zxggrx，缺它所有 DataTable 都返回 0 行
    "xktype": "2",
    "kcfw": "zxggrx",
    "interval": 2.4,
    "cookie_file": "cookies.txt",
    "secret_file": "secret.txt",
}

DEAD_MARKS = ("凭证已失效", "无效的访问请求", "请使用正常的途径访问本系统")

# 线上教学判定：上课地点写“线上教学”，任课教师列显示“网络教师”
ONLINE_PAT = re.compile("线上|网课|网络教师")  # 不能只看“网络”，会把“网络安全实验室”的线下课误判

# 2568 课程表列名（已与郑州工商实机核对）
COURSE_COLS = ["kc", "xf", "zxs", "lb", "kcsx", "skbh", "skbjmc", "xkfs", "rkjs",
               "xk_points", "is_buy_book", "is_cx", "xk_status", "operation", "kcdm",
               "kclb1", "kclb2", "khfs", "kclb3", "is_yxtj", "skbzdm", "skbjdm"]

# 6142 上课班级表列名 -> 中文含义
CLASS_COLS = {
    "curent_skbjdm": "班号", "skbjmc": "班级名称", "xzbj": "班级构成", "skbzmc": "上课班组",
    "xqmc": "开课校区", "rkjs": "任课教师", "skfs_mc": "授课方式", "xkrssx": "限选人数",
    "xkrs": "已选/免听", "kxrs": "可选人数", "sksj": "上课时间", "skdd": "上课地点",
    "ischk": "选择标记", "skbzdm": "上课班组代码", "skbjdm": "上课班代码",
    "xqdm": "校区代码", "skfs_m": "授课方式码", "outnumber": "允许突破上限",
    "yxsyxkb": "允许使用选课币", "zfx_m": "主辅修标记",
}


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def mask(v):
    v = str(v or "")
    return v if len(v) <= 6 else v[:3] + "*" * (len(v) - 6) + v[-3:]


def strip_html(v):
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", " ", v or "")).strip()


class Kingo(object):
    def __init__(self, cookie_file=None, secret_file=None, interval=CFG["interval"], creds=None,
                 sink=None):
        self.cookie_file = cookie_file or CFG["cookie_file"]
        self.secret_file = secret_file or CFG["secret_file"]
        self.creds = tuple(creds) if creds else None   # 图形界面直接传，不落盘
        self.sink = sink                               # 图形界面用它把底层日志显示到窗口
        self.interval = max(1.5, float(interval))
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"})
        self.tr = {}
        self.log = []
        if not os.path.exists(self.cookie_file):
            for cand in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"),
                         os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "zzgs_capture", "cookies.txt")):
                if os.path.exists(cand):
                    self.cookie_file = cand
                    break
        if os.path.exists(self.cookie_file):
            self._load_cookies()

    # ---------- 基础设施 ----------
    def _load_cookies(self):
        txt = open(self.cookie_file, encoding="utf-8", errors="ignore").read().strip()
        n = 0
        for part in txt.replace("\n", " ").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                self.s.cookies.set(k.strip(), v.strip(), domain="zhxy2.ztbu.edu.cn")
                n += 1
        self.say("已从 %s 载入 %d 项 Cookie" % (os.path.basename(self.cookie_file), n))

    def say(self, msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        if self.sink:
            self.sink(msg)
        elif sys.stdout:
            print(line)
            sys.stdout.flush()
        self.log.append(line)

    def _throttle(self):
        wait = self.interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.4))
        self._last = time.time()

    @staticmethod
    def _dec(resp):
        """页面是 GBK，.action 返回 UTF-8：按内容类型判断，失败再试另一种。"""
        ct = (resp.headers.get("content-type") or "").lower()
        order = ("utf-8", "gbk") if "json" in ct else ("gbk", "utf-8")
        for enc in order:
            try:
                return resp.content.decode(enc)
            except Exception:
                continue
        return resp.content.decode("gbk", "ignore")

    def req(self, method, path, referer=None, data=None, params=None, timeout=30):
        url = BASE + (self.api(path))
        self._throttle()
        try:
            r = self.s.request(method, url, data=data, params=params, timeout=timeout,
                               headers={"Referer": referer or BASE + "/frame/homes.action"})
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            raise NetDown("%s 连不上（%s）" % (BASE, type(e).__name__))
        text = self._dec(r)
        if any(m in text for m in DEAD_MARKS) or r.status_code in (401, 403):
            raise SessionDead("%s -> %d %s" % (path, r.status_code, strip_html(text)[:60]))
        return text

    def api(self, path):
        """.action 走 /jw 前缀，jsp/html 走根路径。"""
        if path.startswith("http"):
            return path[len(BASE):]
        if path.endswith(".action") and not path.startswith(CFG["api_prefix"]):
            return CFG["api_prefix"] + path
        return path

    def alive(self):
        try:
            self.req("GET", CFG["xk_page"], referer=BASE + "/frame/homes.action")
            return True
        except NetDown:
            raise
        except Exception as e:
            self.say("会话不可用：%s" % e)
            return False

    def _jsvar(self, page, name):
        m = re.search(r"var\s+%s\s*=\s*[\"']([^\"']*)[\"']" % name, page)
        return m.group(1) if m else ""

    def _hidden(self, page, name):
        m = re.search(r"<input[^>]*name=\"%s\"[^>]*value=\"([^\"]*)\"" % name, page) or \
            re.search(r"<input[^>]*value=\"([^\"]*)\"[^>]*name=\"%s\"" % name, page)
        return m.group(1) if m else ""

    def login(self):
        """复刻 loginbar.js 155-219 行：密码 md5(md5(pwd)+md5(rand))，账号 base64(账号;;_ssessionid)。

        登录页在根 context 的 /cas/login.action（不是 /jw 下），提交到 /cas/logon.action。
        """
        if self.creds:
            user, pwd = self.creds[0].strip(), self.creds[1].strip()
        elif os.path.exists(self.secret_file):
            line = open(self.secret_file, encoding="utf-8", errors="ignore").read().strip().splitlines()[0]
            if ":" not in line:
                raise NeedSecret("%s 格式应为 学号:密码" % self.secret_file)
            user, pwd = line.split(":", 1)
        else:
            raise NeedSecret("没拿到账号密码：在界面里填，或写一行 学号:密码 到 %s" % self.secret_file)
        if not user or not pwd:
            raise NeedSecret("%s 里冒号两边不能空着（现在是空账号或空密码），"
                             "脚本不会去试着登录，免得把你账号锁了" % self.secret_file)
        self.s.cookies.clear()
        self._throttle()
        page = self._dec(self.s.get(BASE + "/cas/login.action", timeout=25,
                                   headers={"User-Agent": UA}))
        ssessionid = self._jsvar(page, "_ssessionid")
        if not ssessionid:
            raise NeedSecret("登录页没取到 _ssessionid（页面结构变了），请改用浏览器登录后的 cookies.txt")
        if self._jsvar(page, "yzmbt") == "1" and self._hidden(page, "hid_flag") != "1":
            raise NeedSecret("系统已开启验证码，无法自动登录。请在浏览器登录后把 Cookie 写入 %s" % self.cookie_file)
        dk = self.get_enc_key("deskey")
        ts = self.get_enc_key("nowtime")
        # 本校 loginbar.js 把 `if(isNull(randnumber)) randnumber="0000"` 注释掉了，
        # 验证码关闭时浏览器发的是空串：字段名退化成 _u/_p，密码哈希用 md5("") 做盐。
        rand = ""
        hp = hashlib.md5((hashlib.md5(pwd.encode()).hexdigest()
                          + hashlib.md5(rand.encode()).hexdigest()).encode()).hexdigest()
        hu = base64.b64encode(("%s;;%s" % (user, ssessionid)).encode()).decode()
        policy = "1" if (len(pwd) >= 6 and pwd != user) else "0"
        raw = ("_u%s=%s&_p%s=%s&randnumber=%s&isPasswordPolicy=%s"
               "&txt_mm_expression=%s&txt_mm_length=%s&txt_mm_userzh=%s"
               "&hid_flag=%s&hidlag=1&hid_dxyzm=%s"
               % (rand, hu, rand, hp, rand, policy,
                  self._hidden(page, "txt_mm_expression"), self._hidden(page, "txt_mm_length"),
                  self._hidden(page, "txt_mm_userzh"), self._hidden(page, "hid_flag"),
                  self._hidden(page, "hid_dxyzm")))
        # JS: params = getEncParams(params) + "&deskey=" + _deskey + "&ssessionid=" + _ssessionid
        # SetKingoEncypt.jsp 里 _deskey 只声明未赋值，浏览器实际发出的就是空串，
        # 填上真实 deskey 反而会被服务端判为「无效的访问请求」。
        body = self.enc_params(raw, dk=dk, ts=ts) + "&deskey=&ssessionid=" + ssessionid
        self._throttle()
        r = self.s.post(BASE + CFG["login"], data=body, timeout=25,
                        headers={"User-Agent": UA, "Referer": BASE + "/cas/login.action",
                                 "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        try:
            obj = json.loads(r.content.decode("utf-8", "ignore"))
        except Exception:
            raise NeedSecret("登录响应不是 JSON，可能需人工处理验证码")
        if obj.get("status") != "200":
            raise NeedSecret("登录失败：%s" % (obj.get("message") or "")[:60])
        self.say("自动登录成功 学号=%s" % mask(user))
        self._save_cookies()

    def _save_cookies(self):
        """把新会话写回 cookies.txt，免得下次又过期。不打印内容。"""
        try:
            jar = "; ".join("%s=%s" % (c.name, c.value) for c in self.s.cookies
                            if c.name.upper() != "SERVERID")
            if jar:
                with open(self.cookie_file, "w", encoding="utf-8") as f:
                    f.write(jar)
                self.say("已把新会话写回 %s" % os.path.basename(self.cookie_file))
        except Exception as e:
            self.say("回写 cookies 失败（不影响本次运行）：%s" % e)

    def get_enc_key(self, which):
        self._throttle()
        r = self.s.get(BASE + "/frame/homepage?method=getTemp%s" % which.capitalize(),
                       timeout=15, headers={"User-Agent": UA, "Referer": BASE + CFG["xk_page"]})
        v = self._dec(r).strip()
        if not v:
            raise SessionDead("getTemp%s 返回空" % which)
        return v

    def enc_params(self, raw, dk=None, ts=None):
        """复刻 SetKingoEncypt.jsp 的 getEncParams。"""
        dk = dk or self.get_enc_key("deskey")
        ts = ts or self.get_enc_key("nowtime")
        b64 = base64.b64encode(kingo_des.str_enc(raw, dk).encode("utf-8")).decode("ascii")
        token = hashlib.md5((hashlib.md5(raw.encode("utf-8")).hexdigest()
                             + hashlib.md5(ts.encode("utf-8")).hexdigest()).encode("utf-8")).hexdigest()
        return "params=%s&token=%s&timestamp=%s" % (b64.replace("+", "%2B"), token, ts)

    # ---------- 业务 ----------
    def time_range(self):
        if self.tr:
            return self.tr
        txt = self.req("POST", CFG["time_range"], referer=BASE + CFG["xk_page"],
                       params={"xktype": CFG["xktype"]})
        obj = json.loads(txt)
        if obj.get("status") != "200":
            raise NeedSecret("当前不在有效选课时间段：%s" % (obj.get("message") or "")[:60])
        res = obj.get("result")
        self.tr = json.loads(res) if isinstance(res, str) else res
        self.say("轮次=%s  窗口=%s ~ %s  学号=%s"
                 % (self.tr.get("lcmc"), self.tr.get("qssj"), self.tr.get("jssj"), mask(self.tr.get("xh"))))
        return self.tr

    def courses(self, only=None):
        tr = self.time_range()
        body = {"xktype": CFG["xktype"], "xh": tr["xh"], "xn": tr["xn"], "xq": tr["xqM"],
                "nj": tr["nj"], "zydm": tr["zydm"], "lcid": tr["lcid"], "kcmc": "",
                "initQry": "0", "items": "", "kcfw": CFG["kcfw"], "_kcfw": CFG["kcfw"],
                "kxkc": tr.get("kxkc", "0"), "njzy": "%s|%s" % (tr["nj"], tr["zydm"]),
                "hidkcmc": ""}
        txt = self.req("POST", "/taglib/DataTable.jsp?tableId=%s&fre=1" % CFG["table_course"],
                       referer=BASE + CFG["xk_page"], data=body)
        out = []
        for d in self._rows(txt):
            if "选择" not in (d.get("operation") or ""):
                continue
            if only and only not in (d.get("kc") or ""):
                continue
            out.append(d)
        self.say("课程表共 %d 行，符合条件 %d 门" % (len(self._rows(txt)), len(out)))
        return out

    def classes(self, kcdm):
        tr = self.time_range()
        body = {"xn": tr["xn"], "xq_m": tr["xqM"], "xh": tr["xh"], "kcdm": kcdm,
                "skbjdm": "", "xktype": CFG["xktype"], "kcfw": CFG["kcfw"],
                "isyxkc": "false", "lcid": tr["lcid"]}
        txt = self.req("POST", "/taglib/DataTable.jsp?tableId=%s&fre=1" % CFG["table_class"],
                       referer=BASE + CFG["popup_page"], data=body)
        res = []
        for d in self._rows(txt):
            raw = self._raw_cells(txt, d.get("skbjdm"))
            res.append({
                "skbjdm": d.get("skbjdm", ""),
                "skbzdm": d.get("skbzdm", ""),
                "banhao": d.get("curent_skbjdm", ""),
                "teacher": d.get("rkjs", ""),
                "cap": self._int(d.get("xkrssx")),
                "used": self._int((d.get("xkrs") or "0").split("/")[0]),
                "free": self._int(d.get("kxrs")),
                "time": d.get("sksj", ""),
                "place": d.get("skdd", ""),
                "outnumber": d.get("outnumber", "0"),
                "zfx_m": d.get("zfx_m", "0"),
                "xqdm": d.get("xqdm", ""),
                "online": bool(ONLINE_PAT.search((d.get("skdd", "") or "") + (d.get("rkjs", "") or ""))),
                "selectable": bool(raw) and "disabled" not in raw,
            })
        return res

    @staticmethod
    def _int(v):
        m = re.search(r"-?\d+", str(v or ""))
        return int(m.group(0)) if m else None

    @staticmethod
    def _rows(txt):
        rows = []
        for tr in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", txt, re.S | re.I):
            cells = re.findall(r"<td[^>]*name=['\"](\w+)['\"][^>]*>(.*?)</td>", tr.group(1), re.S)
            if cells:
                rows.append({k: strip_html(v) for k, v in cells})
        return rows

    @staticmethod
    def _raw_cells(txt, skbjdm):
        """取某班所在 ischk 单元格原始 HTML，用于判断 radio 是否 disabled。"""
        if not skbjdm:
            return ""
        for tr in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", txt, re.S | re.I):
            if skbjdm in tr.group(1):
                m = re.search(r"<td[^>]*name=['\"]ischk['\"][^>]*>(.*?)</td>", tr.group(1), re.S)
                return m.group(1) if m else ""
        return ""

    def precheck(self, kcdm):
        tr = self.time_range()
        txt = self.req("POST", CFG["precheck"], referer=BASE + CFG["xk_page"],
                       data="xn=%s&xq_m=%s&xh=%s&kcdm=%s" % (tr["xn"], tr["xqM"], tr["xh"], kcdm),
                       params=None)
        try:
            return json.loads(txt).get("status") == "200"
        except Exception:
            return False

    def _popup_fields(self, course):
        """打开课程弹窗，拿回 ActionForm 的真实字段与顺序（提交体就照它构造）。"""
        tr = self.time_range()
        raw = ("kcdm=%(kcdm)s&xh=%(xh)s&xn=%(xn)s&xq=%(xq)s&checked=&skbjdm=&nj=%(nj)s"
               "&zydm=%(zydm)s&kclb1=%(kclb1)s&kclb2=%(kclb2)s&khfs=%(khfs)s&xf=%(xf)s"
               "&xktype=%(xktype)s&lcid=%(lcid)s&xxkckzfs=%(xxkckzfs)s&yxsjct=0"
               "&yxkzyfxxk=%(yxkzyfxxk)s&isbuybook=%(is_buy_book)s&iscx=%(is_cx)s"
               "&isyxtj=%(is_yxtj)s&kcfw=%(kcfw)s&kclb3=%(kclb3)s&xfsx=%(xfsx)s&mssx=%(mssx)s"
               ) % {"kcdm": course["kcdm"], "xh": tr["xh"], "xn": tr["xn"], "xq": tr["xqM"],
                    "nj": tr["nj"], "zydm": tr["zydm"], "kclb1": course.get("kclb1", ""),
                    "kclb2": course.get("kclb2", ""), "khfs": course.get("khfs", ""),
                    "xf": course.get("xf", ""), "xktype": CFG["xktype"], "lcid": tr["lcid"],
                    "xxkckzfs": tr.get("xxkckzfs", "0"), "yxkzyfxxk": tr.get("yxkzyfxxk", "0"),
                    "is_buy_book": course.get("is_buy_book", ""), "is_cx": course.get("is_cx", ""),
                    "is_yxtj": course.get("is_yxtj", ""), "kcfw": CFG["kcfw"],
                    "kclb3": course.get("kclb3", ""), "xfsx": tr.get("xfsx", ""),
                    "mssx": tr.get("mssx", "")}
        url = CFG["popup_page"] + "?" + self.enc_params(raw)
        txt = self.req("GET", url, referer=BASE + CFG["xk_page"])
        m = re.search(r"<form[^>]*id=['\"]ActionForm['\"][^>]*>(.*?)</form>", txt, re.S | re.I)
        if not m:
            raise SessionDead("弹窗页无 ActionForm")
        inner = m.group(1)
        fields = []
        for tag in re.finditer(r"<(input|select|textarea)\b", inner, re.I):
            name = tag.group(1).lower()
            head_end = inner.index(">", tag.start())
            attrs = inner[tag.end():head_end]
            nm = re.search(r"""name=['"]([^'"]+)['"]""", attrs)
            if not nm:
                continue
            if name == "select":
                close = inner.find("</select>", head_end)
                opts = inner[head_end + 1:close if close > 0 else None] or ""
                sel = re.search(r"<option[^>]*selected[^>]*value=['\"]([^'\"]*)['\"]", opts, re.I) \
                    or re.search(r"""<option[^>]*value=['"]([^'"]*)['"]""", opts, re.I)
                fields.append([nm.group(1), sel.group(1) if sel else ""])
                continue
            if name == "textarea":
                close = inner.find("</textarea>", head_end)
                fields.append([nm.group(1), strip_html(inner[head_end + 1:close]) if close > 0 else ""])
                continue
            v = re.search(r"""value=['"]([^'"]*)['"]""", attrs)
            ty = (re.search(r"""type=['"]([^'"]+)['"]""", attrs) or [None, "text"])[1].lower()
            if ty in ("radio", "checkbox") and "checked" not in attrs.lower():
                fields.append([nm.group(1), None])
            else:
                fields.append([nm.group(1), v.group(1) if v else ""])
        if len(fields) < 10:
            raise SessionDead("弹窗 ActionForm 解析异常，只取到 %d 个字段" % len(fields))
        return url, fields, txt

    def build_raw(self, course, klass, points="0"):
        """按弹窗 ActionForm 的真实顺序拼提交体（等价于 jQuery serialize 后去前缀）。"""
        tr = self.time_range()
        pop_url, fields, page = self._popup_fields(course)

        def box(rid):
            m = re.search(r"<input[^>]*id=['\"]%s['\"][^>]*>" % rid, page, re.I)
            tag = m.group(0).lower() if m else ""
            return "checked" in tag, "disabled" in tag

        buy_checked, buy_disabled = box("is_buy_book_radio")
        over = {"skbjdm": klass["skbjdm"], "skbzdm": klass["skbzdm"],
               "outnumber": klass["outnumber"], "zfx_m": klass["zfx_m"], "xqdm": klass["xqdm"],
               "kcmc": strip_html(course.get("kc", "")), "kcfw": CFG["kcfw"],
               "njzy": "%s|%s" % (tr["nj"], tr["zydm"]), "xk_points": points,
               "is_checkTime": "1", "yxsjct": tr.get("yxsjct", "0"),
               # doConfirm(): 教材费=勾选或该勾选项被禁用；重修；允许调剂
               "is_buy_book": "1" if (buy_checked or buy_disabled) else "0",
               "is_cx": "1" if box("is_cx_radio")[0] else "0",
               "is_yxtj": "1" if box("is_yxtj_radio")[0] else "0"}
        pairs = []
        for name, val in fields:
            if val is None:
                continue
            key = name.split(".")[-1]
            pairs.append((name, over.get(key, val)))
        raw = "&".join("%s=%s" % (n, v) for n, v in pairs)
        return pop_url, pairs, raw

    def submit(self, course, klass, points="0"):
        """真正选课。调用前必须已确认有余量且用户授权。"""
        if not self.precheck(course["kcdm"]):
            self.say("预检不通过（isSelectableSkbjdm）")
            return False
        pop_url, _pairs, raw = self.build_raw(course, klass, points)
        body = self.enc_params(raw)
        txt = self.req("POST", CFG["save"], referer=pop_url, data=body)
        try:
            obj = json.loads(txt)
        except Exception:
            self.say("提交响应异常：%s" % strip_html(txt)[:80])
            return False
        ok = obj.get("status") == "200"
        self.say("提交结果 %s：%s" % (obj.get("status"), (obj.get("message") or "")[:60]))
        return ok

    def snapshot(self, only=None, detail=True, online=False):
        data = []
        for c in self.courses(only=only):
            item = {"kcdm": c["kcdm"], "course": c["kc"], "xf": c.get("xf"), "_row": c}
            if detail:
                item["classes"] = self.classes(c["kcdm"])
                if online and not any(x.get("online") for x in item["classes"]):
                    continue
            data.append(item)
        return data


class SessionDead(Exception):
    pass


class NeedSecret(Exception):
    pass


class NetDown(Exception):
    """连不上服务器（不在校园网 / 系统重启），和凭证失效是两回事，不要触发重新登录。"""
    pass


class StopRun(Exception):
    """需要用户介入才能继续（连不上太久、没有凭据、选课窗口关了）。"""
    pass


def connect(k, retry=60, tries=3):
    """让会话可用：先确认连得上，再确认取到选课参数；必要时自动登录。

    tries=0 表示连不上就一直等（挂机巡逻用），否则试够次数抛 StopRun。
    """
    waits = 0
    while True:
        try:
            if not k.alive():
                k.say("凭证已失效，尝试自动登录")
                k.login()
            k.time_range()
            return k.tr
        except NetDown as e:
            waits += 1
            if tries and waits >= tries:
                raise StopRun("连续 %d 次连不上 %s：%s" % (waits, BASE, e))
            k.say("连不上教务系统（%s）。%d 秒后重试；长时间这样通常是没连校园网/VPN，或系统正在维护"
                  % (e, retry))
            time.sleep(retry)


def ensure_ready(k, args):
    """CLI 入口：把 connect 的失败变成人话再退出。"""
    try:
        connect(k, retry=getattr(args, "retry", 60),
                tries=(0 if getattr(args, "mode", None) == "watch" else 3))
    except (StopRun, NeedSecret) as e:
        sys.exit("[停止] %s" % e)


def fmt_classes(cls):
    if not cls:
        return "        （无开课班级）"
    out = []
    for x in cls:
        out.append("        %s %s 教师=%-6s 限=%-4s 已选=%-4s 可选=%-4s %s @%s%s"
                   % ("[线上]" if x.get("online") else "[线下]", x["skbjdm"],
                      x["teacher"], x["cap"], x["used"], x["free"],
                      x["time"], x["place"], "" if x["selectable"] else "  [不可选]"))
    return "\n".join(out)


def resolve(code, courses):
    """页面上看到的是 [51220030]创业人生，后台 kcdm 却是 21310014：两种编号都接受。"""
    if code in courses:
        return code
    for kcdm, c in courses.items():
        if ("[%s]" % code) in (c.get("kc") or ""):
            return kcdm
    return None


def cmd_pick(k, args):
    """干跑：打开弹窗、按浏览器顺序拼出提交体，只显示不提交。"""
    courses = {c["kcdm"]: c for c in k.courses()}
    kcdm = resolve(args.kcdm, courses)
    if not kcdm:
        sys.exit("[停止] %s 不在可选课程里" % args.kcdm)
    c = courses[kcdm]
    cls = k.classes(kcdm)
    if not cls:
        sys.exit("[停止] %s 没有开课班级" % args.kcdm)
    x = next((i for i in cls if i["skbjdm"] == args.ban), cls[0])
    pop_url, pairs, raw = k.build_raw(c, x)
    print("\n弹窗: %s" % pop_url.split("?")[0])
    print("目标: %s  班级: %s  可选人数: %s  可选状态: %s"
          % (c["kc"], x["skbjdm"], x["free"], "可选" if x["selectable"] else "radio 禁用"))
    print("提交体字段（%d 个，去前缀后即 saveElectiveCourse 参数）:" % len(pairs))
    for n, v in pairs:
        print("   %-28s = %s" % (n, v))
    print("\n明文参数串长度=%d（加密后才发送）" % len(raw))
    print("以上仅为构造演练，未发送任何写请求。")


def cmd_probe(k, args):
    data = k.snapshot(only=args.only, online=args.online)
    print("")
    total_free = 0
    for item in data:
        cls = item.get("classes") or []
        if args.online:
            cls = [c for c in cls if c.get("online")]
        best = max([c["free"] or 0 for c in cls], default=0)
        total_free += 1 if best > 0 else 0
        if args.free and best <= 0:
            continue
        print("%s kcdm=%s 学分=%s  最大可选=%s" % (item["course"], item["kcdm"], item["xf"], best))
        print(fmt_classes(cls))
    print("\n有空的课程 %d / 命中 %d 门" % (total_free, len(data)))
    with open("snapshot_%s.json" % time.strftime("%m%d_%H%M"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    k.say("快照已写入 snapshot_*.json")


class Patrol(object):
    """巡逻引擎：命令行和图形界面共用一套。不打印、不退出进程，全靠回调说话。

    log(msg) 逐行输出；on_hit(hit) 每发现一个有余量的班级回调一次；
    should_stop() 每查一门课前问一次，界面按「停止」就能立刻收工。
    stats 里的 round/pool/online/hits/status 给界面做状态栏。
    """

    def __init__(self, k, online_only=False, targets=(), only=None, commit=False,
                 rounds=0, retry=90, rest=0, log=None, on_hit=None, should_stop=None):
        self.k = k
        self.online_only = online_only
        self.targets = set(t for t in targets if t)
        self.only = only
        self.commit = commit
        self.rounds = rounds
        self.retry = retry
        self.rest = rest
        self.log = log or k.say
        self.on_hit = on_hit or (lambda hit: None)
        self.should_stop = should_stop or (lambda: False)
        self.stats = {"round": 0, "pool": 0, "online": 0, "hits": 0, "status": "未开始"}

    def _stopped(self):
        return bool(self.should_stop())

    def run(self):
        k = self.k
        scan_all = self.online_only and not self.targets
        try:
            courses = {c["kcdm"]: c for c in k.courses(only=self.only)}
        except (NetDown, SessionDead, NeedSecret) as e:
            self.stats["status"] = "启动失败"
            self.log("启动失败：%s" % e)
            return False
        wanted = set()
        for t in sorted(self.targets):
            got = resolve(t, courses)
            if got:
                wanted.add(got)
            else:
                self.log("%s 不在可选池里（可能已选上或不在本轮范围）" % t)
        if not scan_all and not wanted:
            self.stats["status"] = "没有可盯的课程"
            self.log("没有可盯的课程：勾「只抢线上课」就会自动盯整个可选池")
            return False
        if scan_all:
            self.log("巡逻范围=全部 %d 门可选课程，每轮约 %d 次查询、%d 秒"
                     % (len(courses), len(courses), int(len(courses) * k.interval)))
        self.stats.update(pool=len(courses), status="巡逻中")

        seen = set(courses)
        rnd = 0
        net_fail = 0
        dead_streak = 0
        while True:
            rnd += 1
            self.stats["round"] = rnd
            n_scan = 0
            online_cnt = 0
            try:
                if scan_all:
                    fresh = {c["kcdm"]: c for c in k.courses(only=self.only)}
                    new = sorted(set(fresh) - seen)
                    seen |= set(fresh)
                    courses = fresh
                    for kc in new:
                        self.log("第%d轮 新课程入池：%s kcdm=%s" % (rnd, fresh[kc]["kc"], kc))
                    self.stats["pool"] = len(courses)
                targets = (wanted & set(courses)) if not scan_all else set(courses)
                n_scan = len(targets)
                for idx, kcdm in enumerate(sorted(targets), 1):
                    if self._stopped():
                        self.stats["status"] = "已停止"
                        self.log("已按操作停止（第%d轮 第%d/%d 门）" % (rnd, idx, n_scan))
                        return False
                    if scan_all and idx % 25 == 0:
                        self.log("第%d轮 进度 %d/%d" % (rnd, idx, n_scan))
                    c = courses[kcdm]
                    for x in k.classes(kcdm):
                        if x.get("online"):
                            online_cnt += 1
                        if self.online_only and not x.get("online"):
                            continue
                        if (x["free"] or 0) <= 0 or not x["selectable"]:
                            continue
                        hit = {"round": rnd, "course": c["kc"], "kcdm": kcdm,
                               "skbjdm": x["skbjdm"], "teacher": x["teacher"],
                               "cap": x.get("cap"), "used": x.get("used"),
                               "free": x["free"], "place": x["place"],
                               "online": x.get("online", False),
                               "committed": False, "ok": False}
                        self.stats["hits"] += 1
                        self.log("第%d轮 命中 %s %s 教师=%s 可选=%s @%s"
                                 % (rnd, c["kc"], x["skbjdm"], x["teacher"], x["free"], x["place"]))
                        if self.commit:
                            hit["ok"] = hit["committed"] = bool(k.submit(c, x))
                            self.log("提交结果：%s" % ("成功，已选上" if hit["ok"] else "未成功，继续盯"))
                        self.on_hit(hit)
                        if hit["ok"]:
                            self.stats["status"] = "已选上"
                            return True
                        if not self.commit:
                            self.stats["status"] = "发现余量，已停下等人工处理"
                            self.log("未开自动提交：已停在这里，你可以马上去网页选，或开启自动提交后重跑")
                            return False
            except NeedSecret as e:
                self.stats["status"] = "已停止"
                self.log("第%d轮 停止：%s（选课窗口可能已结束，或需要人工登录）" % (rnd, e))
                return False
            except NetDown as e:
                net_fail += 1
                self.log("第%d轮 中断：连不上教务系统（%s）。第 %d 次，%d 秒后自动重试"
                         % (rnd, e, net_fail, self.retry))
                if net_fail >= 20:
                    self.stats["status"] = "连续连不上，已退出"
                    raise StopRun("连续 %d 轮连不上 %s" % (net_fail, BASE))
                time.sleep(self.retry)
                continue
            except SessionDead as e:
                self.log("第%d轮 会话失效（%s）：尝试重新登录" % (rnd, e))
                try:
                    k.login()
                    k.tr = {}
                    k.time_range()
                except (NeedSecret, SessionDead) as e2:
                    self.stats["status"] = "重新登录失败"
                    self.log("重新登录失败（%s）。请在浏览器重新登录并更新 cookies.txt" % e2)
                    return False
                dead_streak += 1
                if dead_streak >= 4:
                    self.stats["status"] = "会话反复被顶掉"
                    raise StopRun("连续 %d 次刚登录就被踢下线：八成是同一账号还开着别的"
                                  "客户端（另一个程序、浏览器标签、同学也在用这个号），"
                                  "请只保留一个再重跑" % dead_streak)
                rnd -= 1      # 这一轮只扫到一半，重跑同一轮，别把半轮当整轮
                continue
            self.stats["online"] = online_cnt
            net_fail = 0
            dead_streak = 0
            self.log("第%d轮扫描完毕：池内 %d 门、线上班 %d 个，暂无线上余量（本轮盯 %d 门）"
                     % (rnd, self.stats["pool"], online_cnt, n_scan))
            if self.rounds and rnd >= self.rounds:
                break
            if self.rest:
                time.sleep(self.rest)
        self.stats["status"] = "跑满轮数"
        self.log("结束：全程 %d 轮未出现可选的线上班级" % rnd)
        return False


def cmd_watch(k, args):
    wanted = [t.strip() for t in (args.targets or "").split(",") if t.strip()]
    p = Patrol(k, online_only=args.online, targets=wanted, only=args.only,
               commit=args.commit, rounds=args.rounds, retry=args.retry, rest=args.rest,
               on_hit=lambda hit: _notify(hit_line(hit)))
    if not p.run():
        k.say("巡逻结束（详见上面的日志）")


def hit_line(hit):
    tail = "（自动提交%s）" % ("成功" if hit["ok"] else "失败") if hit["committed"] else ""
    return "第%s轮 %s %s 教师=%s 可选=%s @%s%s" % (
        hit["round"], hit["course"], hit["skbjdm"], hit["teacher"], hit["free"],
        hit["place"], tail)


def _notify(msg, path=None):
    """命中时落一条到 hits.log 并真正发出声音，便于挂机时第一时间发现。"""
    with open(path or "hits.log", "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    try:
        import winsound
        for _ in range(6):
            winsound.Beep(1400, 500)
    except Exception:
        try:
            print("\a", end="")
        except Exception:
            pass



_SINGLE_LOCK = None


def ver_tuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or ""))


def is_newer(remote, local):
    """1.10 要比 1.2 大，所以不能直接比字符串。"""
    return ver_tuple(remote) > ver_tuple(local)


def fetch_meta(url, timeout=3.0):
    """拉远端那份 version.json（版本号 + 公告）。

    任何失败一律返回 None：断网、Gitee 限流、JSON 少个逗号，都不该让同学的程序开不起来。
    """
    if not url:
        return None
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA,
                                                       "Cache-Control": "no-cache"})
        if r.status_code != 200:
            return None
        d = json.loads(r.content.decode("utf-8-sig", "ignore").strip())
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def acquire_single_instance(port=47821):
    """一台电脑上只许开一个客户端。

    青果一个账号只认一个会话：命令行和界面同时登录会互相把对方踢下线，
    双方各自偷偷重登，扫描轮轮被截断，还看不出原因。占个本机端口做互斥最省事。
    """
    global _SINGLE_LOCK
    if _SINGLE_LOCK:
        return True
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _SINGLE_LOCK = s
    return True


def main():
    ap = argparse.ArgumentParser(description="郑州工商 青果 选课助手")
    ap.add_argument("mode", choices=["probe", "watch", "pick"], nargs="?", default="probe")
    ap.add_argument("--only", default=None, help="课程名关键字过滤")
    ap.add_argument("--online", action="store_true", help="只看/只抢线上教学课程（上课地点含“线上”，教师列显示“网络教师”）")
    ap.add_argument("--free", action="store_true", help="probe 只打印当前有余量的课程")
    ap.add_argument("--targets", default="", help="watch 要盯的课程编号，逗号分隔；页面显示的 [51220030] 和后台 kcdm 都能用")
    ap.add_argument("--kcdm", default="", help="pick 模式的课程代码")
    ap.add_argument("--ban", default="", help="pick 模式的上课班代码（缺省取第一个）")
    ap.add_argument("--commit", action="store_true", help="出现余量时真正提交选课（危险，会改学籍）")
    ap.add_argument("--rounds", type=int, default=30, help="watch 最多扫描轮数，0=一直盯下去")
    ap.add_argument("--rest", type=int, default=0, help="watch 两轮之间额外停顿秒数")
    ap.add_argument("--retry", type=int, default=60, help="watch 连不上服务器时，隔多少秒再试一轮")
    ap.add_argument("--interval", type=float, default=CFG["interval"], help="请求间隔秒，最低 1.5")
    ap.add_argument("--cookie-file", default=CFG["cookie_file"])
    ap.add_argument("--secret-file", default=CFG["secret_file"])
    args = ap.parse_args()

    if args.interval < 1.5:
        sys.exit("[停止] 间隔不得低于 1.5 秒：会给教务服务器造成压力并触发防刷")
    if not acquire_single_instance():
        sys.exit("[停止] 这台电脑上已经有一个在跑了（命令行或界面）。"
                 "同一账号开两个会互相踢下线，请只留一个。")

    k = Kingo(args.cookie_file, args.secret_file, args.interval)
    try:
        ensure_ready(k, args)
    except NeedSecret as e:
        sys.exit("[停止] %s" % e)

    if args.mode == "pick":
        if not args.kcdm:
            sys.exit("[停止] pick 需要 --kcdm")
        cmd_pick(k, args)
    elif args.commit:
        print("\n即将真正提交选课，会改变你的学籍数据。输入 yes 继续：")
        if input("> ").strip().lower() != "yes":
            sys.exit("已取消，未提交任何选课")
        cmd_watch(k, args)
    elif args.mode == "probe":
        cmd_probe(k, args)
    else:
        cmd_watch(k, args)


if __name__ == "__main__":
    main()
