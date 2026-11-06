#!/bin/bash
# 投递跳入口（cron 挂在 schedule.windows 整点）：tick 按窗口补池 + 投递（+ 可选收件箱/小结）。
# 与对话跳、回消息跳共用 <workdir>/.wrapperlock（flock），锁被占就静默跳过本跳；保留业务退出码。
# audit-01 R5：给新用户的仓库版 wrapper（作者本机的投递跳另有部署版，行为等价）。
set -u
export no_proxy=localhost,127.0.0.1,::1
export NO_PROXY=localhost,127.0.0.1,::1
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
REPO="$(cd -- "$SCRIPT_DIR/../.." && pwd)" || exit 1
PYTHON="${BOSS_HUNT_PYTHON:-/usr/bin/python3}"
CONFIG="${BOSS_HUNT_CONFIG:-$HOME/.boss-hunt/config.json}"
export BOSS_HUNT_CONFIG="$CONFIG"  # audit-01 R7：播报脚本据此推导 broadcasts.log 位置
OUT=$("$PYTHON" - "$CONFIG" "$REPO/scripts/boss_hunt.py" "$@" <<'PY' 2>&1
import fcntl
import json
import os
import shutil
import subprocess
import sys

config = os.path.abspath(os.path.expanduser(sys.argv[1]))
try:
    with open(config, encoding='utf-8') as stream:
        cfg = json.load(stream)
    if not isinstance(cfg, dict):
        raise ValueError('config is not an object')
    # audit-01 R6：workdir 推导也在保护内（paths 不是对象、workdir 不是字符串时推人话，不推堆栈）
    workdir = os.path.abspath(os.path.expanduser(
        (cfg.get('paths') or {}).get('workdir') or os.path.join(os.path.dirname(config), 'data')))
except (OSError, ValueError, AttributeError, TypeError):
    # audit-01 R4：推人话，不推堆栈和本机路径
    print(json.dumps({'ok': False, 'error': '配置文件读不到或格式不对'}, ensure_ascii=False))
    sys.exit(1)
os.makedirs(workdir, exist_ok=True)
with open(os.path.join(workdir, '.wrapperlock'), 'a+', encoding='utf-8') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('另一个实例在跑；本跳跳过')
        sys.exit(75)
    cmd = [sys.executable, sys.argv[2], '--config', config, '--json', 'tick'] + sys.argv[3:]  # audit-01 R6：转发额外参数（如 --dry-run），别丢
    caffeinate = shutil.which('caffeinate')
    if caffeinate:
        cmd = [caffeinate, '-i', '-t', '2400'] + cmd
    sys.exit(subprocess.call(cmd))
PY
)
rc=$?
if [ "$rc" -eq 75 ]; then
    printf '%s\n' "$OUT"
    exit 0
fi
DRY=0
for arg in "$@"; do
    [ "$arg" = "--dry-run" ] && DRY=1  # audit-01 R8：告诉播报这是干跑（tick 失败的输出未必带 dry_run）
done
printf '%s' "$OUT" | BOSS_TICK_DRY_RUN="$DRY" "$PYTHON" "$SCRIPT_DIR/../boss-hunt-tick-fmt.py"
fmt_rc=$?
if [ "$fmt_rc" -ne 0 ]; then
    printf '⚠️ BOSS 投递跳：播报渲染退出 %s（业务退出码 %s）\n' "$fmt_rc" "$rc"
fi
exit "$rc"
