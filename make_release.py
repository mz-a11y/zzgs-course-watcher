# -*- coding: utf-8 -*-
"""一条命令出发布包：打 exe -> 查凭证泄漏 -> 组装 发布版/ -> 压成 zip。

用法（在 zzgs_grab 目录）：
    ..\\.venv\\Scripts\\python.exe -u make_release.py
    ..\\.venv\\Scripts\\python.exe -u make_release.py --bump 1.4
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
EXE_NAME = "郑州工商抢课.exe"
DIST_EXE = os.path.join(HERE, "dist", EXE_NAME)
STAGE = os.path.join(ROOT, "发布版")
ZIP_PATH = os.path.join(ROOT, "郑州工商抢课_exe版.zip")
CLASSMATE_DOCS = ("同学版_使用说明.md", "同学版_读我.txt")


def die(msg):
    print("[停止] " + msg)
    sys.exit(1)


def bump(version):
    path = os.path.join(HERE, "zzgs_gui.py")
    src = open(path, encoding="utf-8", newline="").read()
    new, n = re.subn(r'(APP_VERSION = ")([^"]+)(")', r"\g<1>" + version + r"\3", src, count=1)
    if n != 1:
        die("zzgs_gui.py 里没找到 APP_VERSION 那一行，版本号没改")
    # newline="" 两边都不转换：不然整份文件的换行会被悄悄改成 CRLF，exe 也跟着变
    open(path, "w", encoding="utf-8", newline="").write(new)
    print("[改版本] APP_VERSION -> %s" % version)


def exe_is_running():
    try:
        out = subprocess.run(["tasklist"], capture_output=True).stdout.decode("gbk", "ignore")
    except Exception:
        return False
    return EXE_NAME.lower() in out.lower()


def build():
    if exe_is_running():
        die("上一次启动的 %s 还开着，PyInstaller 覆盖不了它。先关掉那个窗口再重跑。" % EXE_NAME)
    if not os.path.exists(VENV_PY):
        die("找不到打包用的 Python：%s" % VENV_PY)
    cmd = [VENV_PY, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
           "--name", "郑州工商抢课", "zzgs_gui.py"]
    print("[打包] 运行中，约 30 秒…")
    r = subprocess.run(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        tail = r.stdout.decode("utf-8", "ignore").splitlines()[-15:]
        print("\n".join(tail))
        die("PyInstaller 退出码 %d，没打成 exe" % r.returncode)
    if not os.path.exists(DIST_EXE):
        die("打包说成功了，但 dist\\%s 不在" % EXE_NAME)


def secret_tokens():
    """本机凭证里挑出「真正的秘密值」，只用来比对，绝不打印正文。

    键名（JSESSIONID 这种）会原样出现在说明文档里，不算泄漏；只保留像值的东西。
    """
    toks = []
    for name in ("secret.txt", "cookies.txt", os.path.join("dist", "cookies.txt"),
                 os.path.join("dist", "secret.txt")):
        p = os.path.join(HERE, name)
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8", errors="ignore").read()
        for piece in re.split(r"[:：,;\s=]+", text):
            piece = piece.strip()
            if len(piece) < 8 or piece in toks:
                continue
            if re.fullmatch(r"[A-Za-z]+", piece):        # 纯字母 = 键名，不是秘密
                continue
            toks.append(piece)
    return toks


def scan(label, blob, toks):
    hit = [i for i, t in enumerate(toks) if t.encode() in blob or t.encode("utf-16-le") in blob]
    if hit:
        print("[泄漏] %s 里命中了本机凭证（第 %s 项）" % (label, ",".join(str(h + 1) for h in hit)))
        return False
    print("  %-34s 干净" % label)
    return True


def pack(toks):
    if os.path.exists(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE)
    shutil.copy2(DIST_EXE, os.path.join(STAGE, EXE_NAME))
    for d in CLASSMATE_DOCS:
        src = os.path.join(HERE, d)
        if not os.path.exists(src):
            die("缺少同学版文档：%s" % d)
        shutil.copy2(src, os.path.join(STAGE, os.path.basename(d).replace("同学版_", "")))
    if os.path.exists(ZIP_PATH):
        os.remove(ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(STAGE)):
            z.write(os.path.join(STAGE, f), os.path.join("郑州工商抢课", f))
    ok = True
    with zipfile.ZipFile(ZIP_PATH) as z:
        for i in z.infolist():
            ok = scan(i.filename, z.read(i.filename), toks) and ok
    return ok


def main():
    args = sys.argv[1:]
    if "--bump" in args:
        bump(args[args.index("--bump") + 1])
    build()
    toks = secret_tokens()
    print("[自检] 拿本机 %d 个凭证片段去比对（正文不显示）" % len(toks))
    blob = open(DIST_EXE, "rb").read()
    if not scan(EXE_NAME, blob, toks):
        die("exe 里带了你的账号，绝对不要发；先确认 zzgs_gui.py / zzgs_grab.py 没有硬编码，再重打")
    if not pack(toks):
        die("zip 里某个文件带凭证，别发这个包")
    src = open(os.path.join(HERE, "zzgs_gui.py"), encoding="utf-8").read()
    ver = re.search(r'APP_VERSION = "([^"]+)"', src).group(1)
    meta = re.search(r'META_URL = "([^"]*)"', src).group(1)
    print("\n[完成] %s" % ZIP_PATH)
    print("  体积 %.1f MB / 版本 v%s / 公告地址 %s" % (
        os.path.getsize(ZIP_PATH) / 1048576.0, ver, meta or "（空，只读本地公告.txt）"))
    print("  MD5 %s" % hashlib.md5(blob).hexdigest())
    print("  里面只有 3 个文件：%s + 使用说明.md + 读我.txt，没有源码" % EXE_NAME)


if __name__ == "__main__":
    main()
