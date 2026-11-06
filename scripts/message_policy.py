"""Shared subjects requiring human decisions, for incoming and outgoing text."""
import re

RESTRICTED_TOPIC = r"出差|驻外|外派|异地|搬迁|调动|外地工作"

def commitment(text, historical_amounts=False):
    """Promises require a human even when copied from a learned hint."""
    if historical_amounts:
        # Only the broad bare amount rule is relaxed; promises remain blocked.
        if re.search(r"薪资|月薪|年薪|底薪|待遇|工资|接受.{0,10}[万千元kKwW]", text or ""):
            return True
        text = re.sub(r"[一二两三四五六七八九十百零\d]+(?:[.点][\d一二三四五六七八九]+)?[万千元kKwW]", "<amount>", text or "")
    return bool(re.search(RESTRICTED_TOPIC, text or "")) or bool(re.search(
        r"面试|面谈|复试|初试|到岗|入职|录用|offer|"
        r"[一二两三四五六七八九十百零\d]+(?:[.点][\d一二三四五六七八九]+)?[万千元kKwW]|"
        r"(准时|一定|保证|承诺|没问题|可以|能|会|确定|接受|同意).{0,25}(面试|面谈|复试|初试|到岗|入职|录用)|"
        r"(明天|后天|今天|周[一二三四五六日天]|星期|\d+点|下午|上午).{0,20}(到|来|参加|面试|入职)|"
        r"(到岗|入职|面试).{0,15}(明天|后天|周[一二三四五六日天]|\d+)|"
        r"(薪资|月薪|年薪|底薪|待遇|工资).{0,15}(\d|[一二两三四五六七八九十]+[千万])|"
        r"(接受|同意|要求|期望).{0,10}\d+.{0,5}(元|万|千|[kKwW])|保证.{0,8}录用", text or ""))



# conv-12：我方话术不许自我否定（「但无…专项经验」「缺乏…背景」）。否定词与经验类词之间不跨标点，
# 「无责底薪」「无底薪」「无论…」不带经验类词，不会命中。
SELF_NEGATION_RE = re.compile(
    r'(?:无|没有|没|缺乏|缺少|不具备|欠缺|不太?熟悉?|未曾?|尚未|暂无)'
    r'[^，。,；;！!？?\n]{0,8}?(?:经验|经历|背景|资源|能力|专项|基础)')


def self_negation(text):
    return bool(isinstance(text, str) and SELF_NEGATION_RE.search(text))
