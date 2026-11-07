#!/usr/bin/env python3
"""Render chat JSON without touching configuration or runtime files."""
import json
import os
import sys


def human_error(error):
    """audit-01 R4：推送出去的文本不带命令、event_id（SKILL 播报规则；投递/回消息跳早已转人话）。"""
    error = str(error or '未知错误')
    if error.startswith('unresolved_send'):
        return '⛔ 有待核对的发送，对话本跳暂停（先确认那个 HR 有没有收到消息，我再核对放行）'
    if error.startswith('另一个实例在跑'):  # audit-01 R5：不推锁路径
        return '⏭ 上一跳还在跑，本跳跳过'
    if error.startswith('skip_ledger_corrupt'):
        return '⛔ 发送流水文件损坏，对话本跳暂停（需要先修复流水）'
    return '⚠️ 对话失败：' + error


def render(data):
    lines = list(data.get('notifications') or [])
    if data.get('dry_run') and data.get('message'):  # onboard-01：chat --dry-run 离线校准只推这一句
        lines.append(data['message'])
    if data.get('ok') is False and not lines:
        if os.environ.get('BOSS_CHAT_DRY_RUN') == '1':  # iter-02：干跑失败别渲染成「对话失败」事故
            lines.append('🧪 对话跳干跑失败：' + str(data.get('error') or '未知错误'))
        else:
            lines.append(human_error(data.get('error')))
    if data.get('pending_verification') and not any('待核' in line for line in lines):
        lines.append('⚠️ 有发送结果待核对（先确认那个 HR 有没有收到消息）')
    if data.get('dry_run'):
        for plan in data.get('plans') or []:
            for action in plan.get('actions') or []:
                lines.append('🧪 计划 %s %s %s' % (
                    plan['conv_key'], action['type'], action.get('text', action.get('kind', ''))))
    if not lines and data.get('candidates'):
        lines.append(data.get('overview') or '💬 对话｜候选 %d' % data['candidates'])
    if data.get('summary_line'):
        lines.append(data['summary_line'])
    return '\n'.join(lines)


def main():
    raw = sys.stdin.read()
    try:
        start = raw.find('{')
        if start < 0:
            raise ValueError('no JSON object')
        data = json.JSONDecoder().raw_decode(raw[start:])[0]  # 前后有杂讯也能读（audit-01 R4）
        if not isinstance(data, dict):
            raise ValueError('expected JSON object')
        text = render(data)
    except (ValueError, TypeError, KeyError) as exc:
        print('⚠️ 对话播报渲染失败：%s\n%s' % (exc, raw[-300:]))
        return 1
    if text:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
