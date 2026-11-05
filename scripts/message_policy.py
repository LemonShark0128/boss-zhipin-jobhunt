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



def safe_interview_reply(text, cfg):
    markers = ((cfg.get('interview_reply') or {}).get('require_markers')
               if isinstance(cfg.get('interview_reply'), dict) else None)
    if markers is None:
        markers = ['时段', '确认']
    if not markers or not all(isinstance(m, str) and m and m in text for m in markers):
        return False
    # Neutral interview words express the request; other commitment checks stay.
    neutral = re.sub(r'线上面试|面试|面谈|复试|初试', '', text)
    return not commitment(neutral) and not bool(re.search(
        r'准时|接受|同意|承诺|保证|参加|出席|赴约|已确认|已经确认|一定|肯定|按您安排|听您安排|到岗|入职|录用|薪资|月薪|待遇|工资|条件|'
        r'(我|我们).{0,8}(到|来|去)|'
        r'明天|后天|今天|下午|上午|晚上|周[一二三四五六日天]|星期|[\d一二两三四五六七八九十]+[点:：]|'
        r'(我会|我能|可以|一定).{0,12}(到|来|参加)', text))
