"""One offline regression group for every round-three P1."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import boss_hunt as B
import hunt_config as HC
import outbound as O
import chrome_mcp as C
import greet as G
import dump_conversations as D


class P1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.cfg={'paths':{'workdir':str(self.root),'state':str(self.root/'state.json')},
                  'user':{'greeting':{'facts':['带6人团队，新签80家'], 'length':{'min':1,'max':380}}}}
        self.job={'jobName':'城市经理','encryptJobId':'j','salaryDesc':'20-30K'}

    def text(self, claim): return '城市经理，'+claim+'。'+G.closings_of(self.cfg)[0]

    def test_p1_1_metric_binding_and_promises(self):
        for claim in ('带80人团队，新签6家','带20人团队做到30万业绩','带6人团队，新签80家，明天准时到场面试',
                      '带6人团队，新签80家，下周可以到岗', '带6人团队，新签80家，接受月薪20K'):
            with self.subTest(claim=claim): self.assertFalse(G.check(self.text(claim),self.cfg,self.job)[0])
        self.assertTrue(G.check(self.text('带6人团队，新签80家'),self.cfg,self.job)[0])
        self.assertFalse(G.basic_safety('乙公司带6人团队，新签80家', self.cfg)[0])
        self.assertFalse(G.generate(self.cfg,self.job,fallback=lambda j:'明天准时到场面试')['text'])
        self.cfg['user']['greeting']['mode']='llm'
        with patch.object(G,'_generate_raw',return_value=(None,'llm_unreachable')):
            self.assertFalse(G.generate(self.cfg,self.job,fallback=lambda j:'明天准时到场面试')['text'])

    def test_p1_3_unknown_enabled_and_off_switch(self):
        br=Mock()
        for response, expected in [('[]','auto_greeting_unknown'), ('[{"t":"招呼语","on":true}]','auto_greeting_on'),
                                   ('[{"t":"招呼语","on":false}]','auto_greeting_off')]:
            br.js.return_value={'text':response}
            self.assertEqual(O.greeting_switch(br,1),expected)
        for switch in ('auto_greeting_on','auto_greeting_unknown'):
            br.reset_mock()
            with patch.object(O,'inspect_job',return_value=('ready','')), patch.object(O,'job_identity_matches',return_value=True), \
                 patch.object(O,'greeting_switch',return_value=switch), patch.object(G,'generate') as model:
                with self.assertRaisesRegex(RuntimeError,'platform_greeting'):
                    O.send_one(br,1,dict(self.job),self.cfg)
            br.click.assert_not_called(); model.assert_not_called()

    def test_p1_5_rpc_error_raises_code_and_message(self):
        client=C.Client.__new__(C.Client); client.id=0; client.p=Mock()
        client.lines=[json.dumps({'id':1,'error':{'code':-32602,'message':'bad selector'}})]
        with self.assertRaisesRegex(RuntimeError,'-32602.*bad selector'): client.call('click',{})

    def test_p1_6_close_failure_keeps_ids_and_closes_client(self):
        br=C.Browser({}); br.c=Mock(); br.opened=[1,2]
        br.c.call.side_effect=[{'isError':False},{'isError':True,'text':'timeout'}]
        with self.assertRaisesRegex(RuntimeError,'cleanup') as err: br.close_own_tabs()
        self.assertEqual(br.opened,[2]); self.assertIn('2',str(err.exception))
        br.c.call.reset_mock(); br.c.call.side_effect=RuntimeError('rpc')
        with self.assertRaises(RuntimeError):
            with br: pass
        br.c.close.assert_called()

    def test_p1_6_connect_failure_and_dump_business_failure_cleanup(self):
        br=Mock(); br.close_own_tabs.return_value=0; br.connect.side_effect=RuntimeError('connect failed')
        with patch.object(B.CM,'Browser',return_value=br), patch.object(HC,'load_config',return_value=self.cfg):
            args=B.build_parser().parse_args(['--config',str(self.root/'config.json'),'login-check'])
            with self.assertRaisesRegex(RuntimeError,'connect failed'): args.func(args)
        br.close.assert_called_once(); br.close_own_tabs.assert_called_once()
        cfgfile=self.root/'config.json';cfgfile.write_text('{}')
        br=Mock();br.network.side_effect=ValueError('decode failed')
        with patch.object(D.CM,'Browser',return_value=br), patch.object(D.time,'sleep'), patch.object(sys,'argv',['dump','--config',str(cfgfile)]):
            with self.assertRaisesRegex(ValueError,'decode failed'): D.main()
        br.close.assert_called_once();br.close_own_tabs.assert_called_once()

    def test_p1_7_fallback_facts_and_numeric_validation_agree(self):
        self.cfg['user']={'years_experience':5,'highlights':['带6人团队，新签80家'],
                          'greeting':{'length':{'min':1,'max':380}}}
        self.assertEqual(G.allowed_numbers(self.cfg,self.job),{'5','6','80'})
        self.assertTrue(G.check(self.text('带6人团队，新签80家'),self.cfg,self.job)[0])
        self.assertTrue(G.generate(self.cfg,self.job,fallback=lambda j:self.text('带6人团队，新签80家'))['text'])

    def test_p1_1_historical_revenue_is_not_a_salary_promise(self):
        self.cfg['user']['greeting']['facts']=['带6人团队','完成300万元销售']
        self.assertTrue(G.check(self.text('带6人团队，完成300万元销售'),self.cfg,self.job)[0])

    def test_p1_1_team_subject_cannot_become_personal(self):
        self.cfg['user']['greeting']['facts']=['团队新签80家']
        self.assertFalse(G.basic_safety('我新签80家',self.cfg)[0])
        self.assertTrue(G.basic_safety('团队新签80家',self.cfg)[0])

    def test_p1_3_missing_boolean_switch_is_unknown(self):
        br=Mock();br.js.return_value={'text':'[{"t":"招呼语"}]'}
        self.assertEqual(O.greeting_switch(br,1),'auto_greeting_unknown')
        br.js.return_value={'text':'[{"t":"招呼语","on":"false"}]'}
        self.assertEqual(O.greeting_switch(br,1),'auto_greeting_unknown')

    def test_p1_7_explicit_facts_remain_the_only_numeric_source(self):
        self.cfg['user']['years_experience']=99
        result=G.generate(self.cfg,self.job,fallback=lambda j:'我有99年经验，带6人团队')
        self.assertFalse(result['text'])
