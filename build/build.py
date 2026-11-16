# -*- coding: utf-8 -*-
"""构建交付物（全部产物与依赖都落在工作区内，不改动系统环境）。

用法:
    "C:/Program Files/Python312/python.exe" build/build.py

步骤:
    1) pip 隔离安装 pyinstaller + pycryptodome 到 build/vendor（清华源，--no-cache-dir）
    2) PyInstaller 打一个单文件 exe（console 模式，MCP 需要 stdout；GUI 启动时隐藏控制台）
    3) 生成 Setup.exe / 便携 zip / mcp配置.json / 交付说明
    4) 基础自检：PE 头、--version、文件大小
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SRC = os.path.join(APP, "src")
DIST = os.path.join(APP, "dist")
VENDOR = os.path.join(HERE, "vendor")
PYI_DIST = os.path.join(HERE, "pyi-dist")
PYI_WORK = os.path.join(HERE, "pyi-work")
sys.path.insert(0, SRC)
from common import VERSION  # noqa: E402  —— 版本号唯一真源

SETUP_EXE = f"微信收藏导出-Setup-V{VERSION}.exe"
PORTABLE_ZIP = f"微信收藏导出-V{VERSION}-便携版.zip"
PY = sys.executable
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def run(cmd, env=None, check=True):
    print(">>>", " ".join(str(x) for x in cmd), flush=True)
    r = subprocess.run(cmd, env=env, cwd=APP, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.stdout:
        print(r.stdout[-4000:], flush=True)
    if r.stderr:
        print("[stderr]", r.stderr[-3000:], flush=True)
    if check and r.returncode:
        raise SystemExit(f"命令失败 exit={r.returncode}")
    return r


def ensure_vendor():
    marker = os.path.join(VENDOR, "PyInstaller", "__init__.py")
    crypto = os.path.join(VENDOR, "Crypto", "Cipher", "AES.py")
    if os.path.isfile(marker) and os.path.isfile(crypto):
        print("vendor 已就绪，跳过安装", flush=True)
        return
    os.makedirs(VENDOR, exist_ok=True)
    run([PY, "-m", "pip", "install", "--no-cache-dir", "--no-warn-script-location",
         "--target", VENDOR, "-i", MIRROR,
         "pyinstaller==6.22.3", "pycryptodome==3.23.0"])


def build_exe():
    import make_icon
    ico = make_icon.ensure()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([VENDOR, SRC] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    args = [
        PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--console",
        "--name", "wxexport",
        "--paths", SRC, "--paths", VENDOR,
        "--hidden-import", "Crypto.Cipher.AES",
        "--hidden-import", "Crypto",
        # core/ 里的引擎脚本是运行时从 _MEIPASS 以源码方式加载的，
        # PyInstaller 静态分析看不到它们的 import，必须显式收集标准库依赖。
        "--hidden-import", "sqlite3",
        "--hidden-import", "hmac",
        "--hidden-import", "glob",
        "--hidden-import", "struct",
        "--hidden-import", "hashlib",
        "--hidden-import", "importlib.util",
        "--hidden-import", "ctypes.wintypes",
        "--hidden-import", "unicodedata",
        "--hidden-import", "zstandard",
        "--add-data", os.path.join(SRC, "core") + os.pathsep + "core",
        "--add-data", ico + os.pathsep + ".",
        "--icon", ico,
        "--distpath", PYI_DIST, "--workpath", PYI_WORK, "--specpath", HERE,
        os.path.join(SRC, "app.py"),
    ]
    run(args, env=env)
    exe = os.path.join(PYI_DIST, "wxexport.exe")
    if not os.path.isfile(exe):
        raise SystemExit("PyInstaller 未产出 exe")
    return exe


def write_texts(exe):
    # 与官方 stdio 配置格式一致：type / command / args / description
    with open(os.path.join(DIST, "mcp配置.json"), "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"wx-fav-export": {
            "type": "stdio",
            "command": r"C:\Users\<用户名>\AppData\Local\微信收藏导出\微信收藏导出.exe",
            "args": ["--mcp"],
            "description": "微信收藏按日期范围导出（读取本机微信 4.x 本地数据）"}}},
            f, ensure_ascii=False, indent=2)

    # 说明文档抽成模板，避免 f-string 与 JSON 花括号互相干扰
    with open(os.path.join(HERE, "README.template.md"), encoding="utf-8") as f:
        readme = (f.read()
                  .replace("{VERSION}", VERSION)
                  .replace("{SETUP_EXE}", SETUP_EXE)
                  .replace("{PORTABLE_ZIP}", PORTABLE_ZIP))
    with open(os.path.join(DIST, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)
    return readme


def pack_portable(exe):
    note = (f"# 微信收藏导出 V{VERSION} · 便携版\n\n"
            "双击 `微信收藏导出.exe` 打开界面；「设置」里可复制/写入 MCP 配置并测试连接。\n\n"
            "MCP 接入：把下面这一个键合并进 `~/.workbuddy/mcp.json` 已有的 mcpServers，\n"
            "把 command 换成本机 exe 的实际绝对路径（不要整份覆盖文件）。\n\n"
            "```json\n"
            '{"wx-fav-export": {"type": "stdio", "command": "<本机绝对路径>/微信收藏导出.exe", '
            '"args": ["--mcp"], "description": "微信收藏按日期范围导出"}}\n'
            "```\n\n"
            "保存后到 WorkBuddy 连接器管理 → 右上角自定义连接器 → 对本服务点「信任」才会生效；\n"
            "验证：新建对话调用 wx_status，返回 ready 即接通。\n\n"
            "首次运行：主按钮为「初始化本机环境」，选账号目录后自动提取数据库密钥；\n"
            "图片密钥需先在微信里打开一张收藏图片再点「开始检测」。\n\n"
            "前置：本机微信 4.x 已登录。密钥由程序内提取，不依赖外部脚本。\n")
    with zipfile.ZipFile(os.path.join(DIST, PORTABLE_ZIP), "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, "微信收藏导出/微信收藏导出.exe")
        z.writestr("微信收藏导出/说明.md", note)
        z.writestr("微信收藏导出/版本.txt", f"V{VERSION}\n")


def verify(exe, setup):
    with open(exe, "rb") as f:
        magic = f.read(2)
    assert magic == b"MZ", f"不是有效 PE 文件: {magic!r}"
    r = run([exe, "--version"], check=False)
    ver = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    print(f"[自检] PE=MZ  version={ver!r}  exe={os.path.getsize(exe):,} bytes", flush=True)
    assert ver == VERSION, f"版本号不符: {ver!r}"
    assert os.path.isfile(setup)
    return ver


def main():
    os.makedirs(DIST, exist_ok=True)
    ensure_vendor()
    exe = build_exe()
    setup = os.path.join(DIST, SETUP_EXE)
    shutil.copy2(exe, setup)
    write_texts(exe)
    pack_portable(exe)
    v = verify(exe, setup)
    print("\n=== 交付物 ===", flush=True)
    for name in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, name)
        if os.path.isfile(p):
            print(f"  {name}  {os.path.getsize(p):,} bytes", flush=True)
    print(f"V{v} 构建完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
