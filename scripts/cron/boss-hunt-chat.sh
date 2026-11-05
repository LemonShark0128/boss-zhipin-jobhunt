#!/bin/bash
# Shares the delivery/inbox wrapper flock; Python handles flock on macOS too.
set -u
export no_proxy=localhost,127.0.0.1,::1
export NO_PROXY=localhost,127.0.0.1,::1
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
REPO="$(cd -- "$SCRIPT_DIR/../.." && pwd)" || exit 1
PYTHON="${BOSS_HUNT_PYTHON:-/usr/bin/python3}"
CONFIG="${BOSS_HUNT_CONFIG:-$HOME/.boss-hunt/config.json}"
OUT=$("$PYTHON" - "$CONFIG" "$REPO/scripts/boss_hunt.py" <<'PY' 2>&1
import fcntl
import json
import os
import shutil
import subprocess
import sys

config = os.path.abspath(os.path.expanduser(sys.argv[1]))
with open(config, encoding='utf-8') as stream:
    cfg = json.load(stream)
workdir = os.path.abspath(os.path.expanduser(
    (cfg.get('paths') or {}).get('workdir') or os.path.join(os.path.dirname(config), 'data')))
os.makedirs(workdir, exist_ok=True)
with open(os.path.join(workdir, '.wrapperlock'), 'a+', encoding='utf-8') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('另一个实例在跑；本跳跳过')
        sys.exit(75)
    cmd = [sys.executable, sys.argv[2], '--config', config, '--json', 'chat']
    caffeinate = shutil.which('caffeinate')
    if caffeinate:
        cmd = [caffeinate, '-i', '-t', '900'] + cmd
    sys.exit(subprocess.call(cmd))
PY
)
rc=$?
if [ "$rc" -eq 75 ]; then
    printf '%s\n' "$OUT"
    exit 0
fi
printf '%s' "$OUT" | "$PYTHON" "$SCRIPT_DIR/boss-hunt-chat-fmt.py"
fmt_rc=$?
if [ "$fmt_rc" -ne 0 ]; then
    printf '⚠️ BOSS 对话跳：播报渲染退出 %s（业务退出码 %s）\n' "$fmt_rc" "$rc"
fi
exit "$rc"
