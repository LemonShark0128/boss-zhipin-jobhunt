#!/bin/bash
# 回消息跳入口（cron 10/16/22 点 :10）：inbox 只读归类 + 播报，不发消息（cleanup-02 起回 HR 只走 chat）。
# 与投递跳、对话跳共用 <workdir>/.wrapperlock（flock）；仓库外部署的 wrapper 只需 exec 本文件。
set -u
export no_proxy=localhost,127.0.0.1,::1
export NO_PROXY=localhost,127.0.0.1,::1
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
REPO="$(cd -- "$SCRIPT_DIR/../.." && pwd)" || exit 1
PYTHON="${BOSS_HUNT_PYTHON:-/usr/bin/python3}"
CONFIG="${BOSS_HUNT_CONFIG:-$HOME/.boss-hunt/config.json}"
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
except FileNotFoundError:
    # iter-02：新用户常在 init 之前先试 --dry-run，告诉他下一步（仍不带本机路径）
    print(json.dumps({'ok': False, 'error': '配置文件读不到或格式不对：还没有配置文件，'
                      '先运行 python3 scripts/boss_hunt.py init'}, ensure_ascii=False))
    sys.exit(1)
except json.JSONDecodeError as exc:
    print(json.dumps({'ok': False, 'error': '配置文件读不到或格式不对：JSON 第 %d 行第 %d 列有误，'
                      '改好后运行 python3 scripts/boss_hunt.py validate' % (exc.lineno, exc.colno)},
                     ensure_ascii=False))
    sys.exit(1)
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
    cmd = [sys.executable, sys.argv[2], '--config', config, '--json', 'inbox'] + sys.argv[3:]  # audit-01 R6：转发额外参数（如 --dry-run），别丢
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
DRY=0
for arg in "$@"; do
    [ "$arg" = "--dry-run" ] && DRY=1
done
# iter-02：告诉播报这是干跑（wrapper 自己读配置失败时，输出里没有 dry_run 字段）
MSG=$(printf '%s' "$OUT" | BOSS_INBOX_DRY_RUN="$DRY" "$PYTHON" "$SCRIPT_DIR/boss-hunt-inbox-fmt.py")
fmt_rc=$?
if [ "$fmt_rc" -ne 0 ]; then
    FMT_ERR=$(printf '⚠️ BOSS 回消息跳：播报渲染退出 %s（业务退出码 %s）' "$fmt_rc" "$rc")
    MSG="${MSG:+$MSG
}$FMT_ERR"
fi
[ -n "$MSG" ] && printf '%s\n' "$MSG"
# notify-01：系统 cron 下没有 Agent 在场，播报按 config.notify 推给用户本人；推送成败不改业务退出码。
# 干跑只报告推送配置，不推。
CONFIG_OK=1  # 配置都读不到时不知道推给谁，播报只留在 stdout（与上面加锁段的报错文案对应）
case "$OUT" in *'配置文件读不到或格式不对'*) CONFIG_OK=0 ;; esac
if [ "$DRY" = 1 ] && [ "$CONFIG_OK" = 1 ]; then
    printf '🧪 %s\n' "$("$PYTHON" "$REPO/scripts/boss_hunt.py" --config "$CONFIG" notify --check 2>&1)"
elif [ -n "$MSG" ] && [ "$CONFIG_OK" = 1 ]; then
    printf '%s\n' "$MSG" | "$PYTHON" "$REPO/scripts/boss_hunt.py" --config "$CONFIG" notify --stdin --source inbox
fi
exit "$rc"
