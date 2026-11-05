#!/usr/bin/env python3
"""Render chat JSON without touching configuration or runtime files."""
import json
import sys


def render(data):
    lines = list(data.get('notifications') or [])
    if data.get('ok') is False and not lines:
        lines.append('⚠️ 对话失败：' + str(data.get('error') or '未知错误'))
    if data.get('pending_verification') and not any('待核实' in line for line in lines):
        lines.append('⚠️ 待核实，请先运行 ledger-ack --list')
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
        data = json.loads(raw)
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
