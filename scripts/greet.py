#!/usr/bin/env python3
"""按岗位 JD 现写招呼语：本地 Ollama 生成 → 质量闸门 → 缓存 → 模板回退。

为什么要这个模块
----------------
固定模板对每个岗位说同样的话，HR 一眼看出是群发。这里改成：发送前把岗位详情页的
JD 正文 + 候选人事实清单交给本地模型，现写一条 100–180 字、点名岗位、只挑该 JD 最
看重的 2 条战绩的招呼语。

三条硬约束（改代码前先读）
--------------------------
1. **事实只能来自 config**：`user.greeting.facts` 是唯一可引用的事实源。模型写出的每个
   阿拉伯数字都必须能在 facts / JD / 岗位字段里找到，否则判定为编造 → 重试 → 回退模板。
2. **失败不阻断发送**：Ollama 没起、超时、连续两轮不过闸门，都回退 `user.greeting_template`
   渲染，并把原因写进返回值（`greet_fallback:<reason>`），不抛异常。
3. **同一岗位话术稳定**：结果按 `(job_id, jd_hash, model)` 缓存到 `data/greet_cache.json`，
   重跑不会换词，也不会重复等模型。

配置（`user.greeting`，全部可选）
--------------------------------
    {
      "mode": "llm",                       // llm | template
      "length": {"min": 90, "max": 200},   // 过闸门的字数区间（>380 必被 BOSS 拦）
      "facts": ["5 年示例行业经验", "…"],  // 唯一可引用事实；缺省用 user.highlights
      "forbid_words": ["本人", "贵司", "该岗位"],
      "llm": {"base_url": "http://127.0.0.1:11434", "model": "qwen3.5:9b",
              "think": false, "temperature": 0.7, "num_predict": 360,
              "timeout_s": 60, "retries": 1, "max_jd_chars": 1000, "cache": true}
    }

`think` 必须显式 false：qwen3.5 这类思考模型默认先输出大段 thinking，num_predict 一到
就截断，回复正文是空字符串（2026-10-24 实测：num_predict=260 全被 thinking 吃掉）。
"""
from message_policy import commitment
import hashlib
import json
import os
import random
import re
import difflib
import time
import urllib.error
import urllib.request

import hunt_config as HC

DEFAULTS = {
    "mode": "template",
    "length": {"min": 90, "max": 200},
    "forbid_words": ["本人", "贵司", "该岗位"],
    "style_forbid": [],          # 额外想禁的 AI 味词，会并进内置 STYLE_WORDS
    "style_hits_max": 1,         # 命中几个 AI 味词算不合格（默认 >1 才重写）
    "semicolon_max": 1,          # 分号上限（真人偶尔用，但不该满篇）
    "min_numbers": 2,            # 至少几个阿拉伯数字，保证成绩真的带数据
    "max_numbers": 6,            # 上限：超过就变成 KPI 罗列，读着就"AI"
    "closings": [],              # 覆盖结尾候选句（空=用内置 CLOSINGS）
    "llm": {
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3.5:9b",
        "think": False,
        "temperature": 0.7,
        "top_p": 0.9,
        "num_predict": 360,
        "timeout_s": 60,
        "retries": 1,
        "max_jd_chars": 1000,
        "max_prompt_chars": 3000,
        "cache": True,
    },
}

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\uFE0F]")
MARKDOWN_RE = re.compile(r"\*\*|__|^#|^\s*[-*+]\s|^\s*\d+[.)、]\s|```")
DIGIT_RE = re.compile(r"\d+(?:\.\d+)?")
# 中文数字串（"一五""九零""两百""十五"）：事实里全是阿拉伯数字，模型一旦中文化，
# 数字溯源闸门就失效了，而且读起来像编的。单字（一年/四城/三人）允许。
CN_NUM_RE = re.compile(r"[零一二三四五六七八九十百千]{2,}")
PROMPT_VERSION = 6     # 改了 RULES/prompt 结构就 +1：让旧缓存失效，否则会一直发旧话术

# 「简历体 / AI 味」词库：命中 2 个以上直接判不合格重写。
# 只放虚词和名词化短语，不动行业词（KA / SOP / GMV / 动销率这些都是 BD 真话）。
# 这几个太典型（简历模板/PPT 词），出现一个就重写；其余软词允许 1 个。
STYLE_WORDS_HARD = [
    "具备", "拥有", "经验丰富", "深耕", "赋能", "闭环", "沉淀", "抓手", "助力",
    "全方位", "一体化", "致力于", "综合素质", "跨部门协作", "匹配度", "高度匹配",
]

STYLE_WORDS = [
    "具备", "拥有", "丰富的", "经验丰富", "深耕", "赋能", "闭环", "沉淀", "抓手", "助力",
    "高效", "全方位", "深度", "一体化", "致力于", "综合素质", "跨部门协作", "痛点",
    "打法", "实现了", "能够", "匹配度", "高度匹配", "快速", "积极", "擅长沟通",
]

# 结尾三选一：由代码固定，既保证是完整句子（模型常写出"吗。""想聊聊"这种半截话），
# 又不至于每条一模一样。配置里 user.greeting.closings 可以整组替换。
CLOSINGS = [
    "方便的话想跟您聊十分钟，听听这个岗位现在的重点。",
    "如果方向合适，想跟您细聊下这个岗位具体要解决的问题。",
    "方便的时候想跟您请教下这个岗位接下来最要紧的事。",
]

RULES = """写一条 BOSS 直聘第一条招呼语（HR 在手机上看的第一条私信）。读起来要像一个真人手打的，不要像简历摘要。

人称（最容易写错，务必守住）：
- 「我」= 候选人本人，「您」或「你们」= 招聘方。**绝不能把候选人的经历说成对方的**（不许写"你做这行 7 年了吧"）。
- 不用"你"称呼对方，不用反问句，不用"吧/呢/吗"收尾。

按这个骨架写，四段之间用句号自然衔接，不要写序号：
① 看到你们在招{job_name}，这行我做了 {years} 年。
② 选 2 条最对口的成绩，每条写成"在哪家公司或城市 + 做了什么 + 结果数字"，其中要有一句 12 字以内的话。
③ 一句呼应：你们这个岗位看重 X（用 JD 原词），我在 Y 上做过（只能用「候选人事实」里的内容）。
   呼应时必须用事实里明写的公司或城市组合，不要把意向城市说成经历城市；
   数字照抄事实原文，含 + 号、% 号（写 500% 或 +500%，不要只写 500）。
④ 结尾**原样照抄**这一句，放在最后，一个字都不要改：
   - 「{closing}」

语气（像微信里跟人说话，不像写简历）：
- 多用短句，一句一个意思；少用四字词、成语和书面连词（此外/同时/以及/在此基础上），不写排比句。
- 可以口语一点：「这行」「干了」「跑了」「带了」，但不能市侩，不用「哥们/兄弟/亲」。

硬性：
- 全文 {min}–{max} 字，一段纯文本；无换行、无 emoji、无 markdown、无分点、无解释、无引号。目标 120–140 字，绝对不超过 {max} 字；写完自己数一遍，超了就删掉一条成绩重写，宁短勿长。
- 阿拉伯数字一共 {min_num}–{max_num} 个；**禁止写成中文数字**（八成五 / 加一五 / 一五零 / 三成 / 七年 都不行）。
- 最多 1 个术语（KA、SOP、GMV 这类）；不要用分号。
- 禁用词：「{forbid}」「{style_forbid}」。
- 不承诺面试时间/到岗，不表达外派、出差、薪资意愿，不新增事实外的能力。
- 不要写『我在A和B都做过/跑过…』这类概括句；每一句经历都要对应上面事实清单里的某一条，用它的原意。
- 不要「我在 X 上做过」这类绕口句式，也不要「做过搭出/做过搭建」这种动词连用，直接说做过什么。"""


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def block(cfg):
    """合并默认值，返回 user.greeting 的完整配置。"""
    g = ((cfg or {}).get("user") or {}).get("greeting") or {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in g.items() if v is not None})
    llm = dict(DEFAULTS["llm"])
    llm.update({k: v for k, v in (g.get("llm") or {}).items() if v is not None})
    out["llm"] = llm
    ln = dict(DEFAULTS["length"])
    ln.update({k: v for k, v in (g.get("length") or {}).items() if v is not None})
    out["length"] = ln
    return out


def enabled(cfg):
    return block(cfg).get("mode") == "llm"


def facts(cfg):
    """唯一可引用的事实清单；配置里没写就用 user.highlights 兜底。"""
    u = (cfg or {}).get("user") or {}
    fs = [str(x).strip() for x in (block(cfg).get("facts") or []) if str(x).strip()]
    if fs:
        return fs
    fs = [str(x).strip() for x in (u.get("highlights") or []) if str(x).strip()]
    if u.get("years_experience") not in (None, ""):
        head = "%s 年%s行业经验" % (u.get("years_experience"),
                                 u.get("industry") or u.get("current_title") or "")
        fs.insert(0, head.strip())
    return fs


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------
def closings_of(cfg):
    cs = [str(c).strip() for c in (block(cfg).get("closings") or []) if str(c).strip()]
    return cs or CLOSINGS


def display_job_name(job):
    """用于称呼岗位的名称：去掉注释、薪资和营销尾巴。"""
    original = (job or {}).get('jobName') or ''
    name = re.sub(r'[（(][^（）()]*[）)]', '', original)
    name = re.sub(
        r'(?:月\s*)?\d+(?:\.\d+)?(?:\s*[~～\-－到]\s*\d+(?:\.\d+)?)?\s*[kKwW]\s*\+?'
        r'|年薪\s*\d+(?:\.\d+)?(?:\s*[~～\-－到]\s*\d+(?:\.\d+)?)?\s*万\s*\+?',
        '', name)
    # 只裁掉含条件词或数字的尾段，保留「正编-事业部-岗位」这种有效名称。
    for match in re.finditer(r'[-－|｜·【】]([^-－|｜·【】]*)', name):
        if re.search(r'\d|提成|出差|双休|五险|急招|高薪|包住', match.group(1)):
            name = name[:match.start()]
            break
    name = re.sub(r'\s+', ' ', name).strip(' \t\r\n-－|｜·【】,，。;；:：/、')
    return name or original


def _without_job_names(text, job):
    """删除引用的岗位/品牌原文，避免把招聘方信息当成候选人事实。"""
    job = job or {}
    names = {n for n in (job.get('jobName'), display_job_name(job), job.get('brandName')) if n}
    if not names:
        return text or ''
    pattern = '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.sub(pattern, '', text or '')


def _without_zero_to_one(text):
    return re.sub(r'(?<!\d)从\s*0\s*到\s*1(?!\d)|(?<!\d)0\s*[-到~]\s*1(?!\d)', '', text)


def job_lines(job):
    job = job or {}
    rows = [
        ("职位", display_job_name(job)),
        ("公司", job.get("brandName")),
        ("行业", job.get("brandIndustry")),
        ("公司规模", job.get("brandScaleName")),
        ("薪资", job.get("salaryDesc")),
        ("城市", " ".join(x for x in (job.get("cityName"), job.get("areaDistrict"),
                                    job.get("businessDistrict")) if x)),
        ("经验/学历", " ".join(x for x in (job.get("jobExperience"), job.get("jobDegree")) if x)),
        ("技能标签", "、".join(job.get("skills") or [])),
        ("福利", "、".join(job.get("welfareList") or [])),
    ]
    return ["%s：%s" % (k, v) for k, v in rows if v]


def build_prompt(cfg, job, jd_text="", closing=None):
    g = block(cfg)
    llm = g["llm"]
    jd = re.sub(r"\s+", " ", jd_text or "").strip()[: int(llm.get("max_jd_chars") or 1000)]
    fact_txt = "\n".join("- " + f for f in facts(cfg))
    forbid = "」「".join(g.get("forbid_words") or []) or "本人"
    style_forbid = "」「".join(list(STYLE_WORDS[:12]) + list(g.get("style_forbid") or []))
    closing = closing or random.choice(closings_of(cfg))
    rules = RULES.format(min=int(g["length"]["min"]), max=int(g["length"]["max"]),
                         min_num=int(g.get("min_numbers") or 2),
                         max_num=int(g.get("max_numbers") or 6),
                         job_name=display_job_name(job) or "这个岗位",
                         years=(((cfg or {}).get("user") or {}).get("years_experience")
                                or ((cfg or {}).get("user") or {}).get("years") or 7),
                         closing=closing, forbid=forbid, style_forbid=style_forbid)
    parts = [
        "你是候选人的求职助理，替他在 BOSS 直聘上写第一条招呼语。",
        "",
        "候选人事实（唯一可引用的信息，禁止编造任何数字、公司或经历）：",
        fact_txt,
        "",
        "岗位信息：",
        "\n".join(job_lines(job)) or "（无）",
    ]
    if jd:
        parts += ["", "岗位描述（JD 原文，可能被截断）：", jd]
    else:
        parts += ["", "（没拿到 JD 正文，只能依据上面的职位名与技能标签判断这个岗位最看重什么）"]
    parts += ["", rules]
    prompt = "\n".join(parts)
    cap = int(llm.get("max_prompt_chars") or 3000)
    return prompt[:cap]


# --------------------------------------------------------------------------
# 质量闸门
# --------------------------------------------------------------------------
def allowed_numbers(cfg, job, jd_text=""):
    """Conservative provenance: all generated numbers must be candidate facts.

    Job/JD numbers remain available to the prompt, but cannot support claims.
    """
    src = "\n".join(facts(cfg))
    return set(DIGIT_RE.findall(src))


# Full-clause recruiter questions only. Config may append trusted regex templates
# through user.greeting.question_whitelist.
RECRUITER_QUESTION_PATTERNS = (
    r'(?:方便(?:说说|说下|介绍下)?|可以(?:说说|说下)?|请问)?(?:这个|该)?岗位(?:是)?(?:负责|覆盖)(?:哪个|哪些|什么)区域(?:吗|呢)?',
    r'(?:方便(?:说说|说下|介绍下)?|请问)?(?:这个|该)?岗位(?:的)?(?:负责的区域和团队规模|团队规模|汇报对象|薪资结构|到岗时间|职责范围|工作区域)(?:是)?(?:多少|什么|怎样|如何|哪位)?(?:吗|呢)?',
    r'(?:您想先了解哪块|您想先聊什么呢)',
)


def _norm_clause(value):
    return re.sub(r'[\s，,。；;！!？?：:]', '', value or '')


def _clauses(value):
    return [_norm_clause(c) for c in re.split(r'[，,。；;！!？?：:]', value or '') if _norm_clause(c)]


def _strip_lead(value):
    return re.sub(r'^(?:您好|你好)?(?:我曾|我有|我|有)', '', value or '')


EXPERIENCE_VERB_RE = re.compile(r'(在|任|干过|做过|带过|当过|待过|负责|带|搭|签|跑|管|操盘|干了|做了)')
# 评价/呼应句（不陈述经历，只是把岗位和自己的方向对上）
EVAL_RE = re.compile(r'(比较接近|接近|对口|契合|相关|看重|重点|关注|感兴趣)')
# 第一人称陈述：只有这类句子需要绑定事实，避免把「看到在招X」当成编造
CLAIM_RE = re.compile(r'(我(?:曾|在|任|做|干|带|管|签|搭|跑|负责|有)|本人|自己|在[\u4e00-\u9fa5A-Za-z]{2,10}(?:担任|任职|做过|负责|带过))')
# 不要求机构以「公司」结尾；「在招」属于招聘方引用，不是经历地点。
CLAIM_PLACE_RE = re.compile(r'在(?!招)([\u4e00-\u9fa5A-Za-z0-9·]+?)(?:担任|任职|主导过|接过|跑过|做过|干过|带过|做|干|带|任|负责|当)')
# 「乙公司」「某某科技公司」这类机构名：事实里没有就不许出现。
ORG_ALLOW = {'本公司', '贵公司', '你们公司', '我们公司', '公司'}
ORG_RE = re.compile(r'([\u4e00-\u9fa5A-Za-z]{1,8}公司)')
YEARS_CLAIM_RE = re.compile(r'^(?:这行)?(?:我)?(?:做|干)了?(\d+)年(?:了)?$')


def _similarity(value, source_clauses):
    """最相似的候选事实分句相似度；用来放行改写、卡住编造。"""
    value = value or ''
    if not value or not source_clauses:
        return 0.0
    return max(difflib.SequenceMatcher(None, value, sc).ratio() for sc in source_clauses)


def _best_clause(value, source_clauses):
    """返回（相似度, 最相似的事实分句）。数字分句要靠它对齐指标，防张冠李戴。"""
    value = value or ''
    if not value or not source_clauses:
        return 0.0, ''
    scored = [(difflib.SequenceMatcher(None, value, sc).ratio(), sc) for sc in source_clauses]
    return max(scored, key=lambda pair: pair[0])


def _place_in_facts(place, fact_blob):
    """按最长事实子串逐段核验地点；只容忍虚词和不足两字的残片。"""
    place, fact_blob = place.casefold(), fact_blob.casefold()
    offset, unknown = 0, 0
    while offset < len(place):
        size = next((end - offset for end in range(len(place), offset + 1, -1)
                     if place[offset:end] in fact_blob), 0)
        filler = re.match(r'那边|的|在', place[offset:])
        if size or filler:
            offset += size or len(filler.group())
            unknown = 0
        else:
            unknown += 1
            if unknown >= 2:
                return False
            offset += 1
    return True


def _keywords_bound(clause, places, fact_blob, source_clauses):
    """无数字经历的补充路径：地点已核验，内容中的每个词组都须有出处。"""
    if not places or DIGIT_RE.search(clause):
        return False
    content = CLAIM_PLACE_RE.sub('', clause)
    content = re.sub(r'主导过|跑过|做过|带过|干过|接过|我|也', '', content)
    groups = [part for part in re.split(r'[、和与及]', content) if part]
    blob = fact_blob.casefold()
    sources = [part.casefold() for part in source_clauses]
    return bool(groups) and all(
        part.casefold() in blob or _similarity(part.casefold(), sources) >= 0.6
        for part in groups)


def fact_binding(text, cfg, job=None):
    """事实闸门：数字必须溯源，陈述必须能对上事实，编造机构/地区一律拦。

    2026-10-28 改：原来要求「分句与事实分句逐字相等」，模型和用户自己的模板都会被拦空
    （实测两个真实岗位都返回空文本）。改成相似度绑定：
    - 带数字的分句：数字必须都出现在 facts 里，且与最相似的 facts 分句相似度 ≥ 0.45；
    - 不带数字的分句：不含经历动词（在/任/负责/带/搭/签…）就是评价或呼应句，放行；
      含经历动词则要求与某个事实分句相似度 ≥ 0.40，否则判 fact_not_bound；
    - 「这行我做了 N 年」按 years_experience 放行。
    """
    norm = _norm_clause
    split = _clauses
    strip = _strip_lead
    source = facts(cfg) + [str(f) for f in ((cfg.get('user') or {}).get('highlights') or [])]
    source_clauses = [strip(c) for f in source for c in split(f)]
    bound = set(source_clauses)
    all_fact_digits = set(DIGIT_RE.findall('\n'.join(source)))
    fact_blob = ''.join(norm(f) for f in source)
    user = cfg.get('user') or {}
    years = user.get('years_experience')
    remaining = text or ''
    for closing in CLOSINGS:
        remaining = remaining.replace(closing, '')
    safe = {'您好', '你好', '方便', '方便的', '可以', '可以的', '可以聊聊',
            '方便的话想和您详细聊聊', '方便的话想跟您聊聊',
            '这行我做了%s年' % (years if years is not None else 7),
            '这行我干了%s年' % (years if years is not None else 7)}
    job = job or {}
    for name in [job.get('jobName'), job.get('brandName'), user.get('current_title')]:
        if name:
            name = norm(str(name))
            safe.update({name, '看到在招「'+name+'」', '看到你们在招'+name,
                         '看到贵司在招「'+name+'」', '看到你们在招「'+name+'」'})
    patterns = RECRUITER_QUESTION_PATTERNS + tuple(
        (user.get('greeting') or {}).get('question_whitelist') or ())
    bad = []
    for clause in split(remaining):
        # 概括句先拦，不能因地点拆开可核验而获得关键词放行。
        if re.search(r'我在.+(?:和|与|及).+都(?:做|跑)过', clause):
            bad.append('fact_not_bound:' + clause)
            continue
        places = CLAIM_PLACE_RE.findall(clause)
        places_bound = all(_place_in_facts(place, fact_blob) for place in places)
        is_claim = bool(CLAIM_RE.search(clause) or places)
        # 经历中的机构/城市先用原文核验，不能被岗位名/品牌名删除或相似度放过。
        brand = norm(job.get('brandName') or '')
        brand_experience = ('我' in clause and EXPERIENCE_VERB_RE.search(clause))
        if ((is_claim and not places_bound)
                or (brand and brand in clause and brand not in fact_blob
                    and (is_claim or brand_experience))):
            bad.append('org_not_in_facts:' + clause)
        if not is_claim:
            clause = norm(_without_job_names(clause, job))
        if clause in safe or strip(clause) in bound:
            continue
        if any(re.fullmatch(pattern, clause) for pattern in patterns):
            continue
        years_claim = YEARS_CLAIM_RE.match(clause)
        if years_claim and years is not None and int(years_claim.group(1)) == int(years):
            continue
        orgs = [org for org in ORG_RE.findall(clause) if org not in ORG_ALLOW]
        if any(org not in fact_blob for org in orgs):
            bad.append('org_not_in_facts:' + clause)
            continue
        # 「从 0 到 1」是成语式表达，不算业绩数字。
        digit_scan = _without_zero_to_one(clause)
        digits = DIGIT_RE.findall(digit_scan)
        if digits:
            sim, matched = _best_clause(strip(clause), source_clauses)
            # 主体不能换：事实里把数字挂在「团队」上，个人就不许写成「我」的成绩。
            team_metric = re.search(r'团队[^0-9]{0,6}\d|\d[^0-9]{0,6}团队', matched)
            if team_metric and '团队' not in clause:
                bad.append('subject_mismatch:' + clause)
                continue
            # 数字必须与最相似的事实分句一一对上：8 人团队 / 400 家商户不能互换。
            if (not set(digits) <= all_fact_digits or sim < 0.45
                    or sorted(digits) != sorted(DIGIT_RE.findall(matched))):
                bad.append('number_metric_not_in_facts:' + clause)
            continue
        # 无数字：评价/呼应句、或没有第一人称陈述的句子放行；有「我在某处做过」才要求对得上事实。
        if digit_scan != clause:
            continue      # 「从 0 到 1 做过」是市场开拓的固定说法，不算业绩数字
        if EVAL_RE.search(clause) or not is_claim:
            continue
        if _similarity(strip(clause), source_clauses) >= 0.40:
            continue
        if places_bound and _keywords_bound(clause, places, fact_blob, source_clauses):
            continue
        bad.append('fact_not_bound:' + clause)
    return not bad, bad


def basic_safety(text, cfg, job=None):
    """发送前闸门：事实绑定 + 承诺词拦截（事实口径见 fact_binding）。"""
    reasons = fact_binding(text, cfg, job)[1]
    if commitment(_without_job_names(text, job), historical_amounts=True):
        reasons.append('manual_required')
    return not reasons, reasons


def clean(text):
    t = (text or "").strip()
    t = re.sub(r"^```[a-z]*|```$", "", t).strip()
    t = re.sub(r"^(招呼语|回复|文案)[:：]\s*", "", t)
    t = re.sub(r"[ \t]*\n[ \t]*", "", t)          # 中文换行直接接上，不留空格
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = t.strip().strip('“”"\'「」『』')
    return t.strip()


def _norm(s):
    """去掉所有空白：模型常把「海外销售BDM」写成「海外销售 BDM」，按原样比会误判。"""
    return re.sub(r"\s+", "", s or "")


def _name_hit(text, job):
    name = (job or {}).get("jobName") or ""
    core = re.sub(r"[（(].*?[)）]", "", name).strip()
    t = _norm(text)
    cands = [name, core, display_job_name(job), (job or {}).get("brandName") or ""]
    return any(c and len(_norm(c)) >= 2 and _norm(c) in t for c in cands)


def _feedback(cand, reasons, g):
    """把闸门原因翻成「具体怎么改」，直接拼进重试 prompt（比只说"不合格"有效得多）。"""
    lo, hi = int(g["length"]["min"]), int(g["length"]["max"])
    tips = []
    for r in reasons:
        if r.startswith("too_long"):
            tips.append("太长了（%d 字，上限 %d）：删掉一条成绩或一句评价，只保留最对口的 2 条数据"
                        % (len(cand), hi))
        elif r.startswith("too_short"):
            tips.append("太短了（%d 字，下限 %d）：补一条和 JD 相关的具体数据" % (len(cand), lo))
        elif r.startswith("numbers_not_in_facts"):
            tips.append("这些数字在事实里找不到，必须删掉或换成事实里的：%s" % r.split(":", 1)[-1])
        elif r.startswith("fact_not_bound:"):
            tips.append("这句『%s』在事实清单里找不到对应条目，删掉它，或换成事实清单里某一条的原意"
                        % r.split(":", 1)[1])
        elif r == "manual_required":
            tips.append("不要写出差、薪资、到岗、面试时间相关的意愿或承诺")
        elif r == "no_job_or_brand":
            tips.append("开头必须点出岗位名和公司名")
        elif r.startswith("forbid:"):
            tips.append("不要用「%s」" % r.split(":", 1)[-1])
        elif r.startswith(("ai_style:", "ai_style_hard:")):
            tips.append("这些词太像简历/AI 写的，一个字都不要留，换成大白话或直接说做过什么：%s"
                        % r.split(":", 1)[-1])
        elif r.startswith("cn_numbers:"):
            tips.append("数字必须照抄阿拉伯数字和原单位（85% 不能写成八成五/加一五，-30% 不能写成三十天）")
        elif r.startswith("too_many_semicolons"):
            tips.append("分号太多，用句号断成短句")
        elif r.startswith("too_few_numbers"):
            tips.append("至少两条成绩要带阿拉伯数字")
        elif r.startswith("too_many_numbers"):
            tips.append("数字太多，挑 2 条最对口的成绩，其余删掉")
        elif r == "bad_closing":
            tips.append("结尾必须原样照抄给定的三句之一，一个字不改")
        elif r in ("emoji", "markdown", "multiline"):
            tips.append("只输出一段纯文本，不要符号和换行")
        else:
            tips.append(r)
    return ("上一版不合格，按下面的要求重写，只输出招呼语正文（%d–%d 字）：\n- "
            % (lo, hi)) + "\n- ".join(tips)


def check(text, cfg, job, jd_text="", closing=None):
    """返回 (ok, reasons)。reasons 会写进日志，别删——排障全靠它。"""
    g = block(cfg)
    lo, hi = int(g["length"]["min"]), int(g["length"]["max"])
    reasons = []
    if not text:
        return False, ["empty"]
    reasons.extend(basic_safety(text, cfg, job)[1])
    n = len(text)
    if n < lo:
        reasons.append("too_short:%d<%d" % (n, lo))
    if n > min(hi, 380):
        reasons.append("too_long:%d>%d" % (n, min(hi, 380)))
    if EMOJI_RE.search(text):
        reasons.append("emoji")
    if MARKDOWN_RE.search(text):
        reasons.append("markdown")
    if re.search(r"\n", text):
        reasons.append("multiline")
    if not _name_hit(text, job):
        reasons.append("no_job_or_brand")
    for w in g.get("forbid_words") or []:
        if w and w in text:
            reasons.append("forbid:%s" % w)
    digit_scan = _without_zero_to_one(_without_job_names(text, job))
    bad = sorted(set(DIGIT_RE.findall(digit_scan)) - allowed_numbers(cfg, job, jd_text),
                 key=lambda x: (len(x), x))
    if bad:
        reasons.append("numbers_not_in_facts:%s" % ",".join(bad))
    # --- 说话像不像人（这组是「AI 感」的主要来源，别为了少重试把阈值放回去） ---
    nums = DIGIT_RE.findall(digit_scan)
    if len(nums) < int(g.get("min_numbers") or 2):
        reasons.append("too_few_numbers:%d" % len(nums))
    cn = CN_NUM_RE.findall(text)
    if cn:
        reasons.append("cn_numbers:%s" % ",".join(dict.fromkeys(cn)))
    semis = text.count("；")
    if semis > int(g.get("semicolon_max") or 1):
        reasons.append("too_many_semicolons:%d" % semis)
    max_n = int(g.get("max_numbers") or 6)
    if len(nums) > max_n:
        reasons.append("too_many_numbers:%d" % len(nums))
    want_close = [closing] if closing else closings_of(cfg)
    if not any(text.endswith(c) for c in want_close):
        reasons.append("bad_closing")
    hard = [w for w in (STYLE_WORDS_HARD + list(g.get("style_forbid") or [])) if w and w in text]
    if hard:
        reasons.append("ai_style_hard:%s" % ",".join(dict.fromkeys(hard)))
    style_words = list(dict.fromkeys(list(g.get("style_forbid") or []) + STYLE_WORDS))
    hits = [w for w in style_words if w and w in text]
    if len(hits) > int(g.get("style_hits_max") or 1):
        reasons.append("ai_style:%s" % ",".join(hits))
    return (not reasons), reasons


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------
def _cache_path(cfg):
    wd = ((cfg or {}).get("paths") or {}).get("workdir")
    return os.path.join(wd, "greet_cache.json") if wd else ""


def _cache_load(cfg):
    p = _cache_path(cfg)
    if not p or not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _cache_put(cfg, key, row):
    p = _cache_path(cfg)
    if not p:
        return
    data = _cache_load(cfg)
    data[key] = row
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:
        pass


def _key(job, jd_hash, model):
    return "%s|%s|%s" % ((job or {}).get("encryptJobId") or "unknown", jd_hash, model)


def _jd_hash(cfg, jd_text):
    raw = (jd_text or "") + "||" + "\n".join(facts(cfg)) + "||v%d" % PROMPT_VERSION
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------
def _post(url, payload, timeout, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=h)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 别走系统代理
    with opener.open(req, timeout=timeout) as r:
        return json.load(r)


def _env_key(name, llm=None):
    """取模型 key，不回显。顺序（audit-01 Q11）：环境变量 → llm.env_file / $BOSS_HUNT_ENV_FILE
    → ~/.boss-hunt/.env → ~/.hermes/.env（Hermes 部署的兼容位置）。"""
    v = os.environ.get(name or "") if name else ""
    if v or not name:
        return v or ""
    files = [(llm or {}).get("env_file"), os.environ.get("BOSS_HUNT_ENV_FILE"),
             "~/.boss-hunt/.env", "~/.hermes/.env"]
    for p in files:
        if not p:
            continue
        try:
            with open(os.path.expanduser(p), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(name + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


def _cloud(llm):
    return str(llm.get("provider") or "ollama").lower() in ("deepseek", "openai", "cloud")


def ollama_status(cfg):
    """(ok, msg)：本地模式看服务+模型；云端模式只检查 key 在不在。"""
    llm = block(cfg)["llm"]
    if _cloud(llm):
        key = llm.get("api_key") or _env_key(llm.get("api_key_env") or "DEEPSEEK_API_KEY", llm)
        if not key:
            return False, "no_key:%s" % (llm.get("api_key_env") or "DEEPSEEK_API_KEY")
        return True, "ok:%s@%s" % (llm.get("model"), llm.get("provider") or "cloud")
    url = str(llm.get("base_url") or "").rstrip("/") + "/api/tags"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=6) as r:
            d = json.load(r)
    except Exception as e:
        return False, "down:%s" % str(e)[:60]
    names = [m.get("name") or "" for m in d.get("models") or []]
    want = llm.get("model")
    if want and not any(n == want or n.split(":")[0] == str(want).split(":")[0] for n in names):
        return False, "model_missing:%s（已装 %s）" % (want, ",".join(names) or "无")
    return True, "ok:%s" % want


def _cache_stamp(cfg):
    """缓存写入时间按配置时区（audit-01 #13）。"""
    return HC.stamp(cfg)


def _generate_raw(cfg, prompt):
    llm = block(cfg)["llm"]
    timeout = float(llm.get("timeout_s") or 60)
    if _cloud(llm):
        key = llm.get("api_key") or _env_key(llm.get("api_key_env") or "DEEPSEEK_API_KEY", llm)
        if not key:
            return None, "llm_no_key"
        url = str(llm.get("base_url") or "").rstrip("/") + "/chat/completions"
        payload = {
            "model": llm.get("model"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(llm.get("temperature", 0.7)),
            "top_p": float(llm.get("top_p") or 0.9),
            "max_tokens": int(llm.get("num_predict") or 400),
            "stream": False,
        }
        try:
            d = _post(url, payload, timeout, {"Authorization": "Bearer " + key})
        except urllib.error.URLError as e:
            return None, "llm_unreachable:%s" % str(e)[:60]
        except Exception as e:
            return None, "llm_error:%s" % str(e)[:60]
        try:
            txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except Exception:
            txt = ""
        if not txt.strip():
            return None, "llm_empty(%s)" % (d.get("error") or "?")
        return txt, ""
    payload = {
        "model": llm.get("model"),
        "prompt": prompt,
        "stream": False,
        "think": bool(llm.get("think")),
        "options": {"temperature": float(llm.get("temperature", 0.7)),
                    "top_p": float(llm.get("top_p") or 0.9),
                    "num_predict": int(llm.get("num_predict") or 360)},
    }
    url = str(llm.get("base_url") or "").rstrip("/") + "/api/generate"
    try:
        d = _post(url, payload, timeout)
    except urllib.error.URLError as e:
        return None, "llm_unreachable:%s" % str(e)[:60]
    except Exception as e:
        return None, "llm_error:%s" % str(e)[:60]
    txt = d.get("response") or ""
    if not txt.strip():
        return None, "llm_empty(%s)" % (d.get("done_reason") or "?")
    return txt, ""


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def safe_fallback(cfg, job, fallback):
    text = fallback(job) if fallback else ''
    if text and basic_safety(text, cfg, job)[0]:
        return text
    # A legacy template may contain an unbound qualitative claim. Replace it
    # with controlled fact slots; never salvage a numeric/commitment violation.
    if text and all(r.startswith('fact_not_bound:') for r in basic_safety(text, cfg, job)[1]):
        controlled = '；'.join(facts(cfg)) + '。' + CLOSINGS[1]
        if facts(cfg) and basic_safety(controlled, cfg, job)[0]:
            return controlled
    return ''


def generate(cfg, job, jd_text="", fallback=None, use_cache=True, log=None):
    """返回 dict：text / source(llm|cache|template) / attempts / reasons / secs。

    fallback(job) -> str 由调用方提供（boss_hunt.render 渲染旧模板），保证永远有话术可发。
    """
    g = block(cfg)
    llm = g["llm"]
    model = llm.get("model") or ""
    say = log or (lambda *a, **k: None)
    if g.get("mode") != "llm":
        return {"text": safe_fallback(cfg, job, fallback), "source": "template",
                "attempts": 0, "reasons": ["mode=template"], "secs": 0.0}
    jd_hash = _jd_hash(cfg, jd_text)
    key = _key(job, jd_hash, model)
    if use_cache and llm.get("cache", True):
        hit = _cache_load(cfg).get(key)
        if hit and hit.get("text") and check(hit["text"], cfg, job, jd_text)[0]:
            return {"text": hit["text"], "source": "cache", "attempts": 0,
                    "reasons": [], "secs": 0.0, "ts": hit.get("ts")}
    t0 = time.time()
    attempts, reasons, note = 0, [], ""
    closing = random.choice(closings_of(cfg))       # 固定本轮结尾：重试也用它
    prompt = build_prompt(cfg, job, jd_text, closing=closing)
    text = ""
    for i in range(int(llm.get("retries") or 0) + 1):
        attempts = i + 1
        raw, err = _generate_raw(cfg, prompt)
        if raw is None:
            note = err
            reasons = [err]
            if str(err).startswith("llm_unreachable"):
                break                      # 本地模型没起，重试也是等超时
            continue
        cand = clean(raw)
        ok, rs = check(cand, cfg, job, jd_text, closing=closing)
        reasons = rs                        # 只留最后一版的原因，排障看这个就够
        if ok:
            text = cand
            break
        say("   ! 招呼语不过闸门(%s)，重试" % ",".join(rs))
        prompt = (build_prompt(cfg, job, jd_text, closing=closing) + "\n\n"
                  + _feedback(cand, rs, g))
    secs = round(time.time() - t0, 1)
    if text:
        _cache_put(cfg, key, {"text": text, "ts": _cache_stamp(cfg),
                              "model": model, "chars": len(text), "secs": secs})
        return {"text": text, "source": "llm", "attempts": attempts,
                "reasons": reasons, "secs": secs}
    fb = safe_fallback(cfg, job, fallback)
    reason = note or (",".join(reasons) if reasons else "unknown")
    say("   ! 招呼语回退模板：%s" % reason[:120])
    return {"text": fb, "source": "template_fallback", "attempts": attempts,
            "reasons": reasons, "secs": secs, "fallback_reason": reason}


def describe(cfg, job, jd_text=""):
    """给 CLI / 日志用的一行摘要。"""
    return "mode=%s model=%s facts=%d len=%s–%s" % (
        block(cfg).get("mode"), block(cfg)["llm"].get("model"), len(facts(cfg)),
        block(cfg)["length"]["min"], block(cfg)["length"]["max"])
