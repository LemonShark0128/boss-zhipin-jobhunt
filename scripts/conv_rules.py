"""Pure conversation planning. Returned actions are intentions, not delivery receipts.

The caller must validate conversation identity and confirm delivery before committing
the returned record. No browser, configuration loading, or LLM implementation lives here.
Templates support round1/round2 and individual ANSWER_KEYS for partial questions.
classify(text, cfg=None) also permits per-call conversation.patterns overrides.
"""
import copy
import re

from conv_store import add_action
import greet as G
import message_policy

ANSWER_KEYS = ('requirements', 'salary_structure', 'interview_rounds', 'online_interview')
ROUND_KEYS = {1: ANSWER_KEYS[:2], 2: ANSWER_KEYS[2:]}
TERMINAL = frozenset(('manual', 'complete', 'closed', 'declined'))
DECLINE_TEXT = '感谢您的关注！这个方向和我目前的求职方向不太匹配，就不打扰了，祝招聘顺利！'
INTERVIEW_REPLY = '希望线上，请先给时段，我确认后答复'

# Order is policy: rejection, manual facts/contact requests, invitations, resumes, greetings.
# Bare "面试/线上/薪资" deliberately do not match: these also occur in HR answers.
PATTERNS = {
    'reject': r'不(?:太|是很)?(?:合适|适合|匹配|符合|吻合)|'
              r'并不完全(?:匹配|符合|吻合)|'
              r'(?:需求|要求|经验).{0,8}不(?:完全)?(?:匹配|符合|吻合)|已(?:经)?招(?:满|到)|招满了|'
              r'招到人了|暂(?:时)?不(?:考虑|需要|招)|(?:岗位|职位).{0,5}(?:关闭|停止|下架|结束)|'
              r'(?![^。！？?\n]*[吗呢?？])(?:更(?:适配|合适|匹配)(?:该|这个|此)?(?:岗位|职位)?的?(?:人选|候选人)?|'
              r'初筛.{0,6}(?:结束|未通过|不通过)|综合评估.{0,10}(?:不|更)|'
              r'(?<![不没])(?:优先|会|将|更)考虑(?:其他|别的)(?:候选人|人选)|'
              r'倾向(?:于)?.{0,8}(?:不同|其他|别的|更).{0,10}(?:候选人|人选)|'
              r'不(?:太|是很)?相符|'
              r'抱歉.{0,12}(?:寻找|在找|物色)(?:其他|别的).{0,6}(?:候选人|人选)|'
              r'(?:背景|经历|经验|方向|专业).{0,12}不(?:太|是很)?(?:相关|对口)|'
              r'很遗憾.{0,20}(?:(?:无法|不能|未能)(?:进入|通过|推进|继续|安排(?:面试|下一轮))|'
              r'不(?:太|是很)?(?:合适|匹配|符合|相符))|'
              r'很遗憾[，,]?.{0,6}(?:职位|岗位)(?:需要|要求)|'
              r'匹配度.{0,4}不(?:是很|太|够)?高|'
              r'(?:专业技能|背景|经历|经验).{0,12}(?:与|和|跟).{0,10}(?:职位|岗位).{0,6}'
              r'(?:不(?:太|是很|完全)?(?:匹配|符合|吻合|相符|契合)|有(?:一定|些)?(?:差距|出入)))',
    'manual': r'(?:期望|期待|预期|希望|要求|理想).{0,4}(?:薪资|工资|薪酬|待遇)|'
              r'(?:薪资|工资|薪酬|待遇).{0,3}(?:期望|要求)|'
              r'(?:什么时候|何时|几号|多久|最快).{0,8}(?:到岗|入职)|'
              r'(?:到岗|入职)(?:时间|日期)|(?:到岗|入职).{0,4}(?:多久|何时|几号|什么时候)|'
              r'(?:你|您).{0,8}(?:到岗|入职).{0,5}(?:吗|呢|？|\?)|'
              r'自我介绍|(?:介绍|说说).{0,4}(?:你自己|您自己|自己)|'
              r'(?:上一份|上份|上家|上一家).{0,10}(?:工作|公司|离职)|'
              r'离职(?:的)?(?:原因|理由)|为什么(?:要|想)?(?:离职|换工作|看机会|看新机会)|'
              r'(?:之前|以前|原来)(?![^。！？?\n]*(?:吗|没有?|[？?])).{0,12}(?:做|负责|带|管)|'
              r'(?:之前|以前|原来)[^。！？?\n]{0,12}(?:什么|哪些)|'
              r'(?:多少|几个|几家|几人)[^。！\n]{0,8}[？?吗呢]|'
              r'(?:做过|干过|负责过|带过|管过|跑过|签过|拓展过).{0,10}(?:多少|几个|几家|几人)|'
              r'(?:经历|经验|背景|履历).{0,10}(?:说说|聊聊|介绍|讲讲|详细|具体)|'
              r'(?:前置收费|自备|垫资|押金|加盟费)|置换.{0,4}(?:资源|客户|名单|货物|押金|费用)|'
              r'详细.{0,6}(?:说说|介绍|讲讲|说明)|'
              r'(?:交换|互换|换|加|留|给|发|提供).{0,10}(?:电话|手机|微信|联系方式|weixin|wechat)|'
              r'(?:电话|手机|微信|联系方式).{0,8}(?:多少|交换|给我|发我|方便|吗|？|\?)|'
              r'(?:以上|上述|前面|刚说).{0,20}(?:接受|考虑|可以|能否|是否)|'
              r'什么意思|怎么说|具体指什么|能.{0,4}解释|(?:这个|那样|这样).{0,5}(?:呢|可以吗|行吗)|'
              r'^(?:为什么|怎么|哪方面|哪些方面)[？?]?$',
    # conv-10：在职状态自动答；意向城市（targets[].city）内的工作地点自动答可以（调动/外派/出差仍转人工）
    'employment': r'在职还是离职|(?:是否|是不是|还)在职|(?:目前|现在|当前)(?:是)?(?:在职|离职)(?:状态)?(?:吗|呢|[？?]|还是)|'
                  r'在职(?:状态)?(?:吗|呢|[？?])',
    'location': r'(?:接受|能接受|可以接受|考虑).{0,6}(?:工作)?(?:地点|地址|城市|base|调动|外派|出差|异地)|'
                r'(?:工作)?(?:地点|地址|base).{0,10}(?:接受|可以吗|能接受|方便吗|ok吗|OK吗)|'
                r'调动.{0,6}(?:接受|可以|能吗)',
    # conv-10：HR 主动招呼 / 城市说明等陈述，按招呼走正常询问流程（不能吞掉要简历：classify 里另做排除）
    'outreach': r'(?:考虑|看|寻找|找|关注)(?:一下|下)?(?:新的?|其他)?(?:工作)?机会|'
                r'(?:经历|经验|背景|简历).{0,12}(?:吻合|匹配|契合|适合|感兴趣)|'
                r'对(?:你|您).{0,10}(?:经历|经验|背景).{0,6}感兴趣|'
                r'(?:适合|匹配)(?:我们|这个|该)的?(?:岗位|职位)|'
                r'求职地|意向城市|期望城市|我们(?:公司)?(?:是|在)[\u4e00-\u9fa5]{2,3}(?:呢|哦|哈)',
    'experience': r'(?:你|您).{0,8}(?:经历|经验|项目|业绩|团队)|'
                  r'(?:有没有|是否有|有|做过|接触过|熟悉|了解).{0,8}(?:相关|这方面|类似)?(?:的)?(?:工作)?(?:经验|经历|资源|客户|团队)[^。！\n]{0,12}[？?吗呢]|'
                  r'(?:有没有|是否有).{0,10}(?:经验|资源|团队)|'
                  r'(?:做过|干过|负责过|带过|管过|跑过|签过).{0,15}(?:吗|没有?|[？?])',
    'interview': r'(?:约|邀请|参加|进行|来|过来).{0,10}(?:面试|面谈|复试|初试)|'
                 r'(?:请|想|我们|给你|给您).{0,5}安排.{0,10}(?:面试|面谈|复试|初试)|'
                 r'安排.{0,8}(?:面试|面谈|复试|初试).{0,5}(?:吧|吗|？|\?)|'
                 r'(?:面试|面谈|复试|初试).{0,12}(?:方便吗|可以吗|有空吗|时间定|定在)|'
                 r'(?:明天|后天|今天|周[一二三四五六日天]|星期[一二三四五六日天]).{0,12}'
                 r'(?:方便|可以|有空).{0,5}(?:面试|面谈|复试|初试)|'
                 r'(?:见面|面聊|面谈|当面|来公司|到公司|来我们公司|过来公司|线下聊)|'
                 r'(?:明天|后天|今天|今晚|周[一二三四五六日天]|星期[一二三四五六日天]|上午|下午|晚上|\d{1,2}[点:：])'
                 r'.{0,12}(?:聊|见|过来|来一趟)',
    'resume': r'发.{0,12}简历|'
              r'简历.{0,5}(?:发|给|传)|(?:提供|投递|发送).{0,5}简历|'
              r'(?:求|要|来|看看|看下|看一下|拿)(?:一下|一份|一|份|个|下)?(?:你的|您的)?(?:附件)?简历',
    'greet': r'你好|您好|哈喽|\bhi\b|\bhello\b|在吗|聊聊|聊一下|沟通一下|交流一下|感兴趣|有兴趣|有意向',
    # conv-10：纯确认/致谢（「好的」「收到，我同步给用人经理…」）不转人工，静等
    'ack': r'^\s*(?:好的?|好滴|好嘞|嗯+|ok|收到|了解|明白|谢谢|感谢)[\s，,。.!！~～]*(?:谢谢|感谢)?[\s，,。.!！~～]*$|'
           r'^\s*(?:好的|收到)[，,。\s]*(?:谢谢[，,。\s]*)?(?:我|这边|我这边).{0,30}(?:同步|反馈|转给|看一下|看下|看看)',
}
EMPLOYMENT_ANSWER = '目前在职，在看合适的机会。'
LOCATION_ANSWER = '工作地点可以接受。'
DEFAULT_TEMPLATES = {
    'round1': '方便说下这个岗位主要的工作要求，和薪资结构（底薪/提成/奖金）吗？',
    'round2': '面试大概有几轮？可以安排线上面试吗？',
    'requirements': '方便说下这个岗位主要的工作要求吗？',
    'salary_structure': '方便说下薪资结构（底薪/提成/奖金）吗？',
    'interview_rounds': '面试大概有几轮？',
    'online_interview': '可以安排线上面试吗？',
    'experience_tail': '您这边对这个岗位还有什么想了解的吗？',
}


def safe_lead(text, cfg):
    """Validate injected leads too, before they reach the unchanged sender gate."""
    return (isinstance(text, str) and bool(text.strip()) and len(text) <= 40
            and not re.search(r'[\d零一二两三四五六七八九十百千万〇?？\n]|吗|么|呢|'
                              r'时间|今天|明天|后天|星期|周|上午|下午|晚上|稍后|待会|随后|'
                              r'现在|马上|一会|以后|下次|今晚|早上|中午|简历|附件|'
                              r'电话|手机|微信|薪资|工资|待遇|整理|发您|发给|'
                              r'承诺|保证|一定|准时|同意|接受|我会|我将|发你|给你', text)
            and G.basic_safety(text, cfg)[0] and not message_policy.commitment(text)
            and not message_policy.self_negation(text))


def safe_answer(text, cfg):
    """Require traceable fact excerpts, including number tokens, not HR claims."""
    if (not isinstance(text, str) or not text.strip() or len(text) > 60
            or re.search(r'[?？\n]|吗|么|呢', text)
            or not G.basic_safety(text, cfg)[0] or message_policy.commitment(text)
            or message_policy.self_negation(text)):
        return False
    facts = G.facts(cfg)
    numbers = set(G.DIGIT_RE.findall('\n'.join(facts)))
    if not set(G.DIGIT_RE.findall(text)) <= numbers:
        return False
    # Conservative grounding: select a fact clause rather than accepting a new
    # claim merely because it has no digits or happens to share vocabulary.
    def normalized_clauses(value):
        # Compare formatting only; retain the original answer for delivery and
        # keep number-token validation above independent of normalization.
        value = re.sub(r'\s+', '', value).translate(str.maketrans('（）：／', '():/'))
        return [p for p in re.split(r'[，,。；;！!]', value) if p]

    clauses = normalized_clauses(text)
    source = {p for fact in facts for p in normalized_clauses(fact)}
    acknowledgments = {'有的', '做过', '是的', '有'}
    if clauses and clauses[0] in acknowledgments:
        clauses = clauses[1:]
        if any(p in acknowledgments for p in clauses):
            return False
    return bool(clauses) and all(p in source for p in clauses)


def classify(text, cfg=None):
    overrides = ((cfg or {}).get('conversation') or {}).get('patterns') or {}
    for label, default in PATTERNS.items():
        candidate = text or ''
        if label == 'resume':
            # Remove delivery statements, retaining any separate request in the text.
            candidate = re.sub(r'(?:已(?:经)?发(?:送|过)?|发过).{0,12}简历|'
                               r'简历.{0,5}(?:已(?:经)?发(?:送|过)?|发过)', '', candidate)
        if re.search(overrides.get(label, default), candidate, re.IGNORECASE):
            if label == 'outreach':
                # 冷招呼里夹着要简历的，交给 resume 判（PATTERNS 顺序里 resume 在后面）
                if re.search(overrides.get('resume', PATTERNS['resume']), candidate, re.IGNORECASE):
                    continue
                return 'greet'
            return label
    return 'other'


def _names_other_place(text, cfg):
    """HR 句子里点名了某个地点但不含意向城市（如「工作地点在南京」「在浦东」）→ 不替用户答。"""
    cities = [t.get('city') for t in (cfg or {}).get('targets') or [] if isinstance(t, dict) and t.get('city')]
    generic = ('哪', '这', '那', '我们', '公司', '附近', '本地', '此', '该', '同城', '总部')
    for m in re.finditer(r'(?:在|是|位于|base)\s*([\u4e00-\u9fa5]{2,6}?)(?=市|区|县|新区|呢|[，,。\s]|能|可以|是否|吗|的话|$)', text):
        place = m.group(1)
        if any(place.startswith(g) for g in generic):
            continue
        if not any(city in place for city in cities):
            return True
    return False


def _city_ok(row, ctx, cfg):
    """岗位城市在意向城市内才算：先看岗位 cityName，没有再看岗位名里只出现一个意向城市。"""
    cities = [t.get('city') for t in (cfg or {}).get('targets') or [] if isinstance(t, dict) and t.get('city')]
    if not cities:
        return False
    job_city = (ctx.get('job_city') or '').strip()
    if job_city:
        return any(city in job_city for city in cities)
    hits = {city for city in cities if city in (row.get('title') or '')}
    return len(hits) == 1


def _empty(value):
    # False is a meaningful online_interview answer, and zero can mean no rounds.
    return value is None or (isinstance(value, str) and not value.strip())


def round_questions(record, cfg, round_no):
    keys = ROUND_KEYS[round_no]
    missing = [key for key in keys if _empty(record['answers'].get(key))]
    if not missing:
        return ''
    templates = {**DEFAULT_TEMPLATES,
                 **(((cfg or {}).get('conversation') or {}).get('templates') or {})}
    if len(missing) == len(keys):
        return templates['round%d' % round_no]
    return ''.join(templates[key] for key in missing)


def _card(record):
    card = {key: record[key] for key in ('brand', 'title', 'boss')}
    card.update({key: '未答' if _empty(record['answers'].get(key)) else record['answers'][key]
                 for key in ANSWER_KEYS})
    return card


def decide(record, new_msgs, ctx):
    """Return at most one text and one resume intent; never mutate caller-owned data."""
    row = copy.deepcopy(record)
    actions = []
    cfg = ctx['cfg']
    now = ctx['now_ts']
    extraction_attempted = False
    extraction_failed = False
    extracted = {}

    def emit(action):
        actions.append(action)
        add_action(row, now, action['type'], 'planned')

    def text_sent():
        return any(action['type'] == 'send_text' for action in actions)

    def send_text(text, template=None, prefix=''):
        if not text or text_sent():
            return False
        template = text if template is None else template
        count = row.get('sent_templates', []).count(template)
        if count >= 2 or (count == 1 and not prefix):
            actions[:] = [a for a in actions if a['type'] != 'send_resume']
            row['resume_sent_ts'] = record['resume_sent_ts']
            finish('manual')
            return False
        action = {'type': 'send_text', 'text': text}
        if prefix:
            action.update(template_part=template, opener_part=prefix)
        emit(action)
        return True

    def lead(kind):
        try:
            text = ctx['lead'](kind)
            return text if safe_lead(text, cfg) else ''
        except Exception:
            return ''

    def send_question(round_no):
        template = round_questions(row, cfg, round_no)
        if not template:
            return False
        prefix = lead('round%d' % round_no)
        return send_text(prefix + template, template, prefix)

    def send_resume():
        if row['origin'] == 'inbound' and ctx.get('job_ok') is not True:
            return
        if row['resume_sent_ts'] is None:
            emit({'type': 'send_resume'})
            row['resume_sent_ts'] = now

    def finish(kind):
        row['status'] = kind
        if not row['notified'][kind]:
            emit({'type': 'notify', 'kind': kind, 'card': _card(row)})
            row['notified'][kind] = True

    def first_actions():
        if row['origin'] == 'inbound':
            if ctx.get('job_ok') is None:
                return
            question = round_questions(row, cfg, 1)
            if row['asked_round1_ts'] is None and not text_sent():
                try:
                    opener = ctx['opener']()
                    if not isinstance(opener, str) or not opener.strip() or len(opener) > 60:
                        raise ValueError('invalid opener')
                except Exception:
                    add_action(row, now, 'opener', 'failed')
                    return
                if send_text(opener + question, question, opener):
                    row['asked_round1_ts'] = now
            if row['status'] in TERMINAL:
                return
            send_resume()
        elif row['origin'] == 'reply':
            send_resume()
            if row['asked_round1_ts'] is None:
                if send_question(1):
                    row['asked_round1_ts'] = now
        if row['status'] in TERMINAL:
            return
        if row['asked_round1_ts'] is not None and (
                row['resume_sent_ts'] is not None or ctx.get('job_ok') is False):
            row['status'] = 'engaged'

    for message in new_msgs:
        row['last_seen'] = {'ts': message['ts'], 'text': message['text']}
        if row['status'] in TERMINAL:
            continue
        row['updated_ts'] = now
        if ctx.get('declined'):
            send_text(ctx.get('decline_text') or DECLINE_TEXT)
            row['status'] = 'declined'
            continue
        intent = classify(message['text'], cfg)
        if intent == 'ack':
            # conv-10：新会话里的纯确认/致谢静等；已在问答中的「好的」可能就是答案，按 other 交给抽取
            if row['status'] == 'new':
                continue
            intent = 'other'
        if row['status'] == 'round2' and intent == 'interview':
            if not re.search(r'明天|后天|今天|周[一二三四五六日天]|星期|上午|下午|晚上|\d+点|几点|时间|方便吗|可以吗|有空吗|行吗|\?|？',
                             message['text'] or ''):
                intent = 'other'
        if intent == 'reject':
            row['status'] = 'closed'
        elif intent == 'manual':
            finish('manual')
        elif intent in ('employment', 'location'):
            # conv-10：固定答句 + 下一轮问句；每会话各答一次，答过再问 / 调动外派出差 / 城市不明 → 转人工
            field = intent + '_answered_ts'
            if intent == 'employment':
                answer = EMPLOYMENT_ANSWER
            elif (re.search(r'调动|外派|出差|异地|驻外', message['text'] or '')
                  or _names_other_place(message['text'] or '', cfg)
                  or not _city_ok(row, ctx, cfg)):
                answer = ''
            else:
                answer = LOCATION_ANSWER
            if not answer or row.get(field) is not None:
                finish('manual')
                continue
            if text_sent():
                actions[:] = [a for a in actions if a['type'] != 'send_text']
                for name in ('asked_round1_ts', 'asked_round2_ts'):
                    row[name] = record[name]
            number = 2 if row['asked_round2_ts'] is not None else 1
            template = round_questions(row, cfg, number)
            if not template and number == 1:
                number = 2
                template = round_questions(row, cfg, number)
            if not template:
                template = ((cfg.get('conversation') or {}).get('templates') or {}).get(
                    'experience_tail', DEFAULT_TEMPLATES['experience_tail'])
                number = None
            if send_text(answer + template, template, answer):
                if row['status'] == 'new':
                    send_resume()
                row[field] = now
                if number:
                    name = 'asked_round%d_ts' % number
                    if row[name] is None:
                        row[name] = now
                    row['status'] = 'round2' if number == 2 else 'engaged'
        elif intent == 'experience':
            if row.get('experience_answered_ts') is not None:
                finish('manual')
                continue
            if text_sent():
                # Nothing has been delivered yet. The later experience question
                # supersedes a greeting/question planned earlier in this batch.
                actions[:] = [a for a in actions if a['type'] != 'send_text']
                for field in ('asked_round1_ts', 'asked_round2_ts'):
                    row[field] = record[field]
            try:
                answer = ctx['answer'](message['text'])
            except Exception:
                answer = ''
            if isinstance(answer, str) and answer and not answer.endswith(('，', '。', '；', '！')):
                answer += '。'
            if not safe_answer(answer, cfg):
                finish('manual')
                continue
            number = 2 if row['asked_round2_ts'] is not None else 1
            template = round_questions(row, cfg, number)
            if not template and number == 1:
                number = 2
                template = round_questions(row, cfg, number)
            if not template:
                template = ((cfg.get('conversation') or {}).get('templates') or {}).get(
                    'experience_tail', DEFAULT_TEMPLATES['experience_tail'])
                number = None
            if send_text(answer + template, template, answer):
                if row['status'] == 'new':
                    send_resume()
                row['experience_answered_ts'] = now
                if number:
                    field = 'asked_round%d_ts' % number
                    if row[field] is None:
                        row[field] = now
                    row['status'] = 'round2' if number == 2 else 'engaged'
        elif intent == 'interview':
            send_text((cfg.get('user') or {}).get('interview_reply') or INTERVIEW_REPLY)
            finish('manual')
        elif row['status'] == 'new' and intent in ('resume', 'greet'):
            first_actions()
        elif intent == 'resume':
            send_resume()
            if row['asked_round1_ts'] is None and send_question(1):
                row['asked_round1_ts'] = now
        elif (intent == 'other'
              and (row['asked_round1_ts'] is not None or row['asked_round2_ts'] is not None)
              and re.search(r'[？?]|吗|呢|请问|能否|是否', message['text'] or '')):
            # 我们刚问过问题，对回来的也是一句我们答不上的提问 -> 交人工，别拿模板硬推下一轮
            finish('manual')
        elif row['status'] in ('engaged', 'round2') and intent in ('other', 'greet'):
            # All new_msgs predate questions planned by this call. They cannot
            # answer a first round that we have only just asked.
            if record['asked_round1_ts'] is None and row['asked_round1_ts'] is not None:
                continue
            if not extraction_attempted:
                extraction_attempted = True
                try:
                    extracted = ctx['extract'](copy.deepcopy(ctx['history'][-3:]))
                    if not isinstance(extracted, dict):
                        raise ValueError('extract must return an object')
                except Exception:
                    extraction_failed = True
                    add_action(row, now, 'extract', 'failed')
            if extraction_failed:
                continue
            for key in ANSWER_KEYS:
                if _empty(row['answers'].get(key)) and not _empty(extracted.get(key)):
                    row['answers'][key] = copy.deepcopy(extracted[key])
            if all(not _empty(row['answers'].get(key)) for key in ANSWER_KEYS):
                finish('complete')
            elif row['status'] == 'round2':
                # Only a round already open at batch entry can be completed
                # by a partial answer. Full answers above may complete directly.
                if record['status'] == 'round2':
                    finish('complete')
            elif any(not _empty(row['answers'].get(key)) for key in ROUND_KEYS[1]):
                question = round_questions(row, cfg, 2)
                lead_text = lead('round2') if question else ''
                if not question:
                    finish('complete')
                elif send_text(lead_text + question, question, lead_text):
                    row['asked_round2_ts'] = now
                    row['status'] = 'round2'
        elif row['status'] == 'new':
            # No context or trustworthy answer yet: let the candidate interpret it.
            finish('manual')
    return actions, row
