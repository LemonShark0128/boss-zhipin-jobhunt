"""Canonical message ownership policy; DOM adapters never guess a sender."""
import json
import re

# 真机实测（2026-10-28）：我方气泡的 class 是 `message-item item-myself`。
SELF_MARKERS = ('item-mine', 'item-self', 'item-myself', 'message-mine')
PEER_MARKERS = ('item-friend', 'item-peer', 'message-friend')
SYSTEM_MARKERS = ('item-system', 'item-tip', 'item-time')


def system_message_texts(cfg=None):
    texts = ((cfg or {}).get('conversation') or {}).get(
        'system_texts', ['你与该职位竞争者PK情况'])
    return {re.sub(r'\s+', '', text) for text in texts}


# 真机实测（2026-11-01）：BOSS 的「PK 情况」提示气泡 class 是 `item-friend`,
# 与 HR 真人消息同 class；文本还带「共N人投递 / 优秀竞争者会 / 查看详细分析」。
# 精确文本匹配抓不到它，只能按「系统文案特征」识别。刻意不用前缀匹配：
# 「你与该职位竞争者PK情况，请发简历」这类「系统文案+真人指令」必须留给真人流程。
DEFAULT_SYSTEM_PATTERNS = (
    r'你与该职位竞争者PK情况.{0,20}(?:共.{0,4}人投递|查看详细分析)',
)


def system_patterns(cfg=None):
    raw = ((cfg or {}).get('conversation') or {}).get(
        'system_patterns', DEFAULT_SYSTEM_PATTERNS)
    return [re.compile(p) for p in raw if p]


def is_system_message(message, system_texts=None, system_pats=None):
    """System class markers, exact configured text, or configured system copy; never infer from owner."""
    classes = message.get('classes') or ()
    tokens = classes.split() if isinstance(classes, str) else classes
    texts = system_message_texts() if system_texts is None else system_texts
    pats = system_patterns() if system_pats is None else system_pats
    norm = re.sub(r'\s+', '', str(message.get('text') or ''))
    return (bool(set(tokens).intersection(SYSTEM_MARKERS)) or
            norm in texts or
            any(p.search(norm) for p in pats))


def system_message_parser_js(system_texts=None, system_pats=None):
    """Emit the same class/text/pattern policy for the final atomic DOM guard."""
    texts = system_message_texts() if system_texts is None else system_texts
    pats = (DEFAULT_SYSTEM_PATTERNS if system_pats is None
            else [p.pattern if hasattr(p, 'pattern') else p for p in system_pats])
    return r'''const isSystemMessage = message => {
      const tokens = new Set((message.classes || '').split(/\s+/));
      const norm = String(message.text || '').replace(/\s+/g, '');
      return        %s.some(marker => tokens.has(marker)) ||
        %s.includes(norm) ||
        %s.map(p => new RegExp(p)).some(p => p.test(norm));
    };
''' % (json.dumps(SYSTEM_MARKERS), json.dumps(sorted(texts)),
       json.dumps(list(pats)))


def parse_message_owner(node_classes, text):
    tokens = set(node_classes.split() if isinstance(node_classes, str) else node_classes or ())
    mine = bool(tokens.intersection(SELF_MARKERS))
    peer = bool(tokens.intersection(PEER_MARKERS))
    if tokens.intersection(SYSTEM_MARKERS) or mine == peer:
        return 'unknown'
    return 'mine' if mine else 'friend'


def owner_parser_js():
    """Compile the Python policy to a truth table for the atomic DOM turn.

    JS only encodes marker presence and looks up Python's answer; there is no
    independently maintained ownership decision in page programs.
    """
    groups = (SELF_MARKERS, PEER_MARKERS, SYSTEM_MARKERS)
    table = [parse_message_owner([group[0] for i, group in enumerate(groups) if mask & (1 << i)], '')
             for mask in range(8)]
    return '''const parseMessageOwner = (classes, text) => {
      const tokens = new Set((classes || '').split(/\\s+/));
      const groups = %s, table = %s;
      const mask = groups.reduce((m, g, i) => m | (g.some(x => tokens.has(x)) ? (1 << i) : 0), 0);
      return table[mask];
    };\n''' % (json.dumps(groups), json.dumps(table))
