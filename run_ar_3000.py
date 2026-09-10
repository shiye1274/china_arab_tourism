# -*- coding: utf-8 -*-
"""
run_ar_3000.py —— 阿语 3000+ 篇抓取的自动调度器（应对 OpenAlex 每日额度限制）

逻辑：
  1) 探测 OpenAlex 429 响应头里的 Retry-After，算出额度重置时间；
  2) 等待到重置后，执行正式抓取命令（fetch_papers.py --langs ar ...）；
  3) 若仍被限流，按 Retry-After 周期循环重试；
  4) 成功即退出（exit 0）。配合 oa_cache/ 磁盘缓存：中断恢复不重复消耗额度。

用法（后台运行）：
  python run_ar_3000.py > ar_crawl.log 2>&1
"""
import re
import subprocess
import sys
import time

PROJECT = r"C:\Users\sye13\china_arab_tourism"
FETCH_CMD = [
    sys.executable, "fetch_papers.py",
    "--langs", "ar",
    "--outdir", "data_ar_demo",      # 与你的小样本试验版同位置、同格式
    "--per-lang", "3000",            # 目标：阿语 ≥3000 篇
    "--years", "2010-2026",
    "--relation", "relaxed",         # 阿语文旅话题池；每条记录仍标注 relation_sides
    "--seed", "42",
    "--mailto", "user_ar@univ.edu",
]


def probe_retry_after():
    """探测 OpenAlex 当前是否限流，返回 (blocked: bool, retry_after_seconds: int)。"""
    try:
        out = subprocess.run(
            ["curl", "-s", "-i", "--max-time", "30",
             "https://api.openalex.org/works?per-page=1&mailto=user_ar@univ.edu"],
            capture_output=True, text=True, timeout=40)
        head = out.stdout
        m = re.search(r"Retry-After:\s*(\d+)", head, re.I)
        retry = int(m.group(1)) if m else 3600
        return "429" in head.split("\r\n")[0], retry
    except Exception:
        return False, 0


def main():
    print(f"[{time.strftime('%H:%M:%S')}] 探测 OpenAlex 限流状态 ...", flush=True)
    while True:
        blocked, retry_after = probe_retry_after()
        if blocked:
            wait = max(300, int(retry_after) + 60)
            print(f"[{time.strftime('%H:%M:%S')}] 仍被限流，Retry-After={retry_after}s，"
                  f"等待 {wait}s 后重试 ...", flush=True)
            time.sleep(wait)
            continue
        print(f"[{time.strftime('%H:%M:%S')}] 额度可用，开始抓取 ...", flush=True)
        try:
            r = subprocess.run(FETCH_CMD, cwd=PROJECT)
        except Exception as e:
            print("启动失败:", e, flush=True)
            time.sleep(600)
            continue
        if r.returncode == 0:
            print(f"[{time.strftime('%H:%M:%S')}] 抓取成功完成 ✔", flush=True)
            sys.exit(0)
        print(f"[{time.strftime('%H:%M:%S')}] 抓取退出码={r.returncode}，"
              f"可能是额度再次耗尽；等待后重试（已抓页面在缓存中不会重复消耗）", flush=True)
        time.sleep(600)


if __name__ == "__main__":
    main()
