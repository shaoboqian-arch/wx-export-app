# -*- coding: utf-8 -*-
"""公开仓净化门禁：上传前必须全过，否则不许推。

用法：
    python tests/verify_public_safe.py            # 检查当前仓库
    SKIP_PUBLIC_CHECK=1 git push                  # 明知故犯时绕过本地 pre-push 钩子

检查项（任一 FAIL 退出码 1）：
  1. 本机敏感词名单        tests/public_safe.local.txt（不进 Git）；缺文件或为空则不通过
  2. 真实 wxid              wxid_ 后面 12 位以上（测试里的合成样例是 8 位以内）
  3. 邮箱                   只放行 GitHub noreply / example.*
  4. 带真实用户名的绝对路径   占位符（尖括号用户名）放行，真实用户名拦
  5. 手机号                 11 位大陆号段
  6. 跟踪清单卫生           验证输出/ 构建产物/__pycache__/dist 二进制不得入 Git
  7. 提交作者邮箱           全部可达提交必须是 noreply
  8. 标签指向               tag 必须落在当前历史内（防孤儿 tag 指回净化前）
  9. 名单文件未被跟踪       public_safe.local.txt 不得出现在 git ls-files

只读：不修改任何文件。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_LIST = os.path.join("tests", "public_safe.local.txt")
LOCAL_LIST_PATH = os.path.join(ROOT, LOCAL_LIST)
results = []

# 3. 邮箱白名单（子串匹配）
MAIL_ALLOW = ("users.noreply.github.com", "noreply@", "example.com",
              "example.org", "localhost", "@test")

# 4. 允许出现的路径占位符
PATH_PLACEHOLDERS = {"<用户名>", "<用户>", "<user>", "<name>", "<username>",
                     "用户名", "用户", "你的用户名", "name", "user", "username",
                     "xxx", "*", "…", "..."}

# 绝对路径：单反斜杠、双反斜杠、正斜杠都要认（真实文件里通常是单反斜杠）
PATH_RES = (
    re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]([^\\/\s\"'`]+)"),
    re.compile(r"/(?:Users|home)/([^/\s\"'`]+)"),
)

TEXT_EXT = (".py", ".md", ".json", ".txt", ".csv", ".yml", ".yaml", ".toml",
            ".ini", ".cfg", ".bat", ".ps1", ".sh", ".html", ".js", ".css")


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def git(*args):
    r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def tracked_files():
    return [p.strip() for p in git("ls-files").splitlines() if p.strip()]


def read_text(path):
    try:
        with open(os.path.join(ROOT, path), "r", encoding="utf-8", errors="strict") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return None


def load_local_secrets():
    if not os.path.isfile(LOCAL_LIST_PATH):
        return None
    terms = []
    with open(LOCAL_LIST_PATH, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            terms.append(line.lower())
    return terms


def scan_patterns(local_secrets):
    hard, wid, mail, path, phone = [], [], [], [], []
    secrets = local_secrets or []
    for rel in tracked_files():
        if rel.replace("\\", "/") == LOCAL_LIST.replace("\\", "/"):
            continue
        if not rel.lower().endswith(TEXT_EXT):
            continue
        text = read_text(rel)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            for s in secrets:
                if s and s in low:
                    hard.append(f"{rel}:{i}")
                    break
            for m in re.finditer(r"wxid_[a-z0-9]{12,}", low):
                wid.append(f"{rel}:{i} ({m.group(0)[:6]}…)")
            for m in re.finditer(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", line):
                addr = m.group(0)
                if not any(a in addr for a in MAIL_ALLOW):
                    mail.append(f"{rel}:{i} ({addr})")
            for rx in PATH_RES:
                for m in rx.finditer(line):
                    seg = m.group(1)
                    if seg in PATH_PLACEHOLDERS or seg.startswith(("<", "{", "$", "…")):
                        continue
                    path.append(f"{rel}:{i} (…/{seg})")
            for m in re.finditer(r"(?<![\d.])1[3-9]\d{9}(?![\d.])", line):
                phone.append(f"{rel}:{i}")
    return hard, wid, mail, path, phone


def main():
    files = tracked_files()
    local_secrets = load_local_secrets()
    if local_secrets is None:
        check("已配置本机敏感词名单", False, f"缺少 {LOCAL_LIST}（可复制 tests/public_safe.local.example.txt）")
    elif not local_secrets:
        check("已配置本机敏感词名单", False, f"{LOCAL_LIST} 是空的，请写入要拦截的字面量")
    else:
        check("已配置本机敏感词名单", True, f"{len(local_secrets)} 条（名单本身不进 Git）")

    hard, wid, mail, path, phone = scan_patterns(local_secrets)

    check("仓库里没有本机名单中的字面量", not hard, f"{len(hard)} 处 · {hard[:3]}")
    check("没有真实 wxid", not wid, f"{len(wid)} 处 · {wid[:3]}")
    check("没有非白名单邮箱", not mail, f"{len(mail)} 处 · {mail[:3]}")
    check("没有带真实用户名的绝对路径", not path, f"{len(path)} 处 · {path[:3]}")
    check("没有手机号", not phone, f"{len(phone)} 处 · {phone[:3]}")

    bad_tracked = [f for f in files
                   if f.startswith("验证输出/") or f.startswith("build/pyi-dist/")
                   or "__pycache__" in f or f.endswith(".pyc")
                   or (f.startswith("dist/") and f.lower().endswith((".exe", ".zip")))]
    check("构建产物与验证输出未被跟踪", not bad_tracked,
          f"{len(bad_tracked)} 个 · {bad_tracked[:3]}")

    tracked_local = [f for f in files if f.replace("\\", "/") == LOCAL_LIST.replace("\\", "/")]
    check("本机敏感词名单未被跟踪", not tracked_local,
          ("请从暂存区撤出 " + LOCAL_LIST) if tracked_local else "")

    commits = [l for l in git("log", "--format=%h %ae %ce", "HEAD").splitlines() if l.strip()]
    bad_mail = [c for c in commits
                if not all("@users.noreply.github.com" in part
                           for part in c.split()[1:])]
    check("所有提交作者都是 GitHub noreply", not bad_mail,
          f"{len(bad_mail)} 个 · {bad_mail[:3]}")

    tags = [t for t in git("tag", "-l").splitlines() if t.strip()]
    orphan = []
    for t in tags:
        r = subprocess.run(["git", "merge-base", "--is-ancestor", t, "HEAD"], cwd=ROOT)
        if r.returncode != 0:
            orphan.append(t)
    check("所有标签都在当前历史内", not orphan, f"孤儿 tag {orphan}")

    failed = [n for n, ok in results if not ok]
    print(f"\n合计 {len(results)} 项 · 失败 {len(failed)} · 跟踪文件 {len(files)} 个")
    if failed:
        print("→ 未通过，禁止上传。修完再跑一次。")
        return 1
    print("→ 通过：可以上传。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
