#!/usr/bin/env python3
"""复现 spec §3；只用 mkdtemp 构造数据，绝不读取用户运行目录或调用浏览器。"""
import copy
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import boss_hunt as B
import hunt_config as HC
import hunt_schedule as HQ
import hunt_state as HS
import outbound as O


def main():
    root = Path(tempfile.mkdtemp(prefix='boss-phase-a-accept-'))
    log = (root / 'acceptance.log').open('w', encoding='utf-8')
    def say(line):
        print(line)
        log.write(line+'\n'); log.flush()
    def command(number, args):
        say('\n# '+str(number)+' $ python3 '+' '.join(str(a) for a in args))
        result = subprocess.run([sys.executable]+list(map(str,args)), cwd=REPO, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PYTHONPYCACHEPREFIX=str(root/'pycache')))
        say(result.stdout.strip() or '(无输出)')
        say('exit='+str(result.returncode))
        assert result.returncode == 0
        return result.stdout
    cfg=json.loads((REPO/'assets/config.example.json').read_text())
    cfg['paths']={'workdir':str(root/'data')}
    cfg['inbox']['interview_auto_reply']=False
    cfg['inbox']['followup']['enabled']=False
    path=root/'config.json';path.write_text(json.dumps(cfg,ensure_ascii=False))
    cfg=HC.load_config(str(path))
    (root/'platform.txt').write_text('负责商户拓展和开城，落实平台政策。')
    (root/'brand.txt').write_text('负责门店运营，管理加盟商，安排督导。')
    prefix=['scripts/boss_hunt.py','--config',path]
    def jobs():
        return [{'encryptJobId':g+str(i),'jobName':'城市经理','brandIndustry':'示例行业'+g,
                 'brandName':'示例公司'+g+str(i),'bossName':g+str(i),'cityName':'示例城市',
                 'salaryDesc':'20-30K'} for g,n in [('A',30),('B',15)] for i in range(n)]
    raw=Path(cfg['paths']['raw_dir'])/'sample.json'
    sample=[jobs()[0], jobs()[-1], {'encryptJobId':'c','jobName':'其它职能','brandName':'示例公司C'}]
    raw.write_text(json.dumps({'code':0,'zpData':{'jobList':sample}}))
    command(1,['-m','py_compile']+sorted(str(p.relative_to(REPO)) for p in (REPO/'scripts').glob('*.py')))
    command(2,prefix+['validate'])
    output=command(3,prefix+['grade','--jd-file',root/'platform.txt','--job-name','城市经理'])
    assert '等级 A｜' in output
    output=command(4,prefix+['grade','--jd-file',root/'brand.txt','--job-name','城市经理'])
    assert '等级 B｜' in output
    command(5,prefix+['grades','--top','20'])
    say('\n# 6 $ B.send_one=lambda *a,**k:"sent"; B.time.sleep=lambda *a,**k:None; do_send(...,30,[]) x6')
    raw.write_text(json.dumps({'code':0,'zpData':{'jobList':jobs()}}))
    st=HS.load_state(cfg)
    now=HC.now_local(cfg).replace(hour=22,minute=0,second=0,microsecond=0)
    with patch.object(B.CM,'Browser',side_effect=AssertionError('禁止真实浏览器')), \
         patch.object(O,'inspect_job',side_effect=lambda br,tab,j,c:('ready','商户拓展开城' if j['encryptJobId'].startswith('A') else '门店运营')), \
         patch.object(O,'send_one',lambda *a,**k:'sent'), patch.object(B.time,'sleep',lambda *a,**k:None), \
         patch.object(HC,'now_local',return_value=now):
        for i in range(6):
            result=O.do_send(cfg,None,None,st,30,[])
            say('round=%d sent=%d by_grade=%s day_count=%d'%(i+1,result['sent'],result.get('by_grade',{}),result['day_count']))
        assert st['day_by_grade']=={'A':22,'B':8} and st['day_count']==30
        say('state='+cfg['paths']['state']+' day_by_grade='+str(st['day_by_grade']))
    say('\n# 7 $ state.day_by_grade={"A":18,"B":0}; quota_for_now(cfg,state,12:00/14:00/22:00)')
    quota_state={'day':HC.stamp(cfg,'%Y-%m-%d'),'day_count':18,'sent':{},'day_by_grade':{'A':18,'B':0}}
    HS.save_state(cfg,quota_state)
    for hour in [12,14,22]:
        q=HQ.quota_for_now(cfg,HS.load_state(cfg),now.replace(hour=hour))
        say('%02d:00 -> A=%d B=%d'%(hour,*q))
    say('SPEC_CONFLICT: 剩余 A=22-18=4，公式的本跳 A 不可能 >4；按 R2 公式验收，未声称通过矛盾条件。')
    rows={str(i):dict(brand='示例公司',title='城市经理',grade=g,applied_ts=HC.stamp(cfg),detected_ts=HC.stamp(cfg),kind=k,raw='构造样本',attribution=a,reason=r,target='示例方向')
          for i,(g,k,a,r) in enumerate([('A','explicit','our_apply','无理由婉拒'),('A','explicit','our_apply','无理由婉拒'),('B','explicit','our_apply','经验不符'),('B','explicit','hr_initiated','已招满'),('A','no_reply','our_apply','无回复'),('B','no_reply','our_apply','无回复'),('A','no_reply','unknown','无回复')])}
    (root/'data/rejects.json').write_text(json.dumps(rows,ensure_ascii=False))
    command(8,prefix+['rejects','--days','30'])
    st.update(day_count=29,day_by_grade={'A':22,'B':7},pending={'one':{},'two':{}})
    st['sent'].pop('B7',None)
    HS.save_state(cfg,st)
    command(9,prefix+['summary'])
    machine=json.loads(command('9-json',prefix+['summary','--json']))
    assert (machine['sent'],machine['rejects'],machine['no_reply'],machine['pending'])==(29,4,3,2)
    legacy=json.loads(path.read_text());legacy.pop('grading')
    for key in ('grade_quota','carry_over','max_per_window'): legacy['sending'].pop(key,None)
    legacy_path=root/'legacy.json';legacy_path.write_text(json.dumps(legacy,ensure_ascii=False))
    command('10-validate',['scripts/boss_hunt.py','--config',legacy_path,'validate'])
    command('10-grades',['scripts/boss_hunt.py','--config',legacy_path,'grades','--top','0'])
    for label,zone in [('scan-send','Asia/Shanghai'),('inbox','UTC'),('send','America/Los_Angeles'),('inbox-summary','Asia/Shanghai'),('outside','UTC')]:
        c=copy.deepcopy(cfg); moment=datetime.now(ZoneInfo(zone)); w=moment.strftime('%H:%M')
        c['schedule']={'timezone':zone,'windows':[w],'scan_windows':[w] if label=='scan-send' else [],
                       'inbox_windows':[w] if 'inbox' in label else [],'summary_window':w if 'summary' in label else None,'per_window':0}
        if label=='outside': c['schedule']['windows']=[f'{(moment.hour+2)%24:02d}:00']
        tick_path=root/f'tick-{label}.json';tick_path.write_text(json.dumps(c,ensure_ascii=False))
        command('11-'+label,['scripts/boss_hunt.py','--config',tick_path,'tick','--dry-run'])
    command('regression',['-m','unittest','discover','-s','tests','-v'])
    say('\n证据目录：'+str(root))
    log.close()
    return root


if __name__=='__main__':
    main()
