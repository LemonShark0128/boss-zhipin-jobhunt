"""conv-03n regression contracts, using only fictional data and offline doubles."""
import copy
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

import test_conversation_run as runs
import test_conv_rules as rules
import conv_actions as A
import conv_rules as R
import conv_store as S


class DeclineRulesTests(unittest.TestCase):
    for _name in ('record', 'ctx', 'run_step'):
        locals()[_name] = getattr(rules.ConversationRulesTests, _name)
    def test_unqualified_inbound_replies_without_resume_through_round2(self):
        actions, row = self.run_step(self.record('inbound'), '你好', self.ctx(job_ok=False))
        self.assertEqual([a['type'] for a in actions], ['send_text'])
        self.assertEqual(row['status'], 'engaged')
        self.assertIsNone(row['resume_sent_ts'])
        actions, row = self.run_step(row, '请发简历', self.ctx(job_ok=False))
        self.assertFalse(any(a['type'] == 'send_resume' for a in actions))
        actions, row = self.run_step(row, '虚构底薪', self.ctx({'salary_structure': '虚构底薪'}, job_ok=False))
        self.assertEqual(row['status'], 'round2')

    def test_decline_precedes_all_intents_but_not_terminal(self):
        for origin in ('reply', 'inbound'):
            for text in ('你好', '请发简历', '什么时候到岗', '明天来面试', '不合适'):
                actions, row = self.run_step(self.record(origin), text,
                    self.ctx(declined=True, decline_text='虚构回绝语'))
                self.assertEqual(actions, [{'type': 'send_text', 'text': '虚构回绝语'}])
                self.assertEqual(row['status'], 'declined')
                self.assertIsNone(row['resume_sent_ts'])
                self.assertFalse(any(row['notified'].values()))
                self.assertEqual(self.run_step(row, text, self.ctx(declined=True))[0], [])
        self.assertNotIn('ignored', R.TERMINAL)
        self.assertIn('declined', R.TERMINAL)


class DeclineRunTests(unittest.TestCase):
    # Reuse offline boundaries without inheriting unrelated test cases.
    for _name in ('setUp', 'module', 'item', 'record', 'history', 'run_hop', 'saved'):
        locals()[_name] = getattr(runs.ConversationRunTests, _name)

    def test_industry_declines_once_for_both_origins(self):
        for inbound in (False, True):
            with self.subTest(inbound=inbound):
                S.save(self.path, {})
                self.br.new_tab.side_effect = [10, 11]
                self.cfg['conversation']['decline_industries'] = ['金融']
                item = dict(self.item(), brandIndustry='互联网金融')
                c = self.module()
                with patch.object(A, 'mark_not_interested_company', return_value='done', create=True) as mark:
                    result, _, calls, _, _ = self.run_hop(items=[item], history=self.history(inbound=inbound))
                    self.assertEqual([a[0] for a in calls], ['text'])
                    mark.assert_called_once()
                    self.assertEqual(self.saved()['status'], 'declined')
                    self.assertEqual(result['declined'], 1)
                    self.assertIn('回绝 1', result['overview'])
                    self.assertIn('🚫 回绝 虚构公司1｜虚构经理｜虚构HR1（命中：金融）', result['notifications'])
                    self.br.new_tab.side_effect = [10]
                    again, _, _, _, _ = self.run_hop(items=[item], history=self.history(inbound=inbound))
                    self.assertEqual(again['processed'], 0)
                    mark.assert_called_once()

    def test_send_failure_does_not_mark_or_commit_decline(self):
        self.cfg['conversation']['decline_industries'] = ['金融']
        with patch.object(A, 'mark_not_interested_company', create=True) as mark:
            result, _, _, _, _ = self.run_hop(items=[dict(self.item(), brandIndustry='金融')], text_result='fill_err')
        mark.assert_not_called()
        self.assertNotEqual(self.saved()['status'], 'declined')
        self.assertEqual(result.get('declined', 0), 0)

    def test_mark_failure_warns_without_pending(self):
        self.cfg['conversation']['decline_industries'] = ['金融']
        with patch.object(A, 'mark_not_interested_company', return_value='aborted:company', create=True):
            result, _, _, _, _ = self.run_hop(items=[dict(self.item(), brandIndustry='金融')])
        self.assertEqual(self.saved()['status'], 'declined')
        self.assertTrue(any(n.startswith('⚠️ 不感兴趣未点成') for n in result['notifications']))
        self.assertFalse(result['pending_verification'])
        self.assertIn('aborted:company', [a['result'] for a in self.saved()['actions']])

    def test_scoped_jd_and_all_existing_identity_sources(self):
        for field in ('brandName', 'jobName', 'brandIndustry', 'jd', 'benefits', 'empty'):
            with self.subTest(field=field):
                S.save(self.path, {})
                self.br.new_tab.side_effect = [10, 11]
                self.cfg['conversation']['decline_industries'] = [] if field == 'empty' else ['保险']
                item = self.item()
                jd = '岗位职责：\n负责虚构产品\n福利待遇：\n补充商业保险'
                if field in item or field == 'brandIndustry':
                    item[field] = '虚构保险'
                if field == 'jd':
                    jd = '岗位职责：\n负责保险产品'
                with patch.object(A, 'mark_not_interested_company', return_value='done', create=True) as mark:
                    result, _, calls, _, _ = self.run_hop(items=[item], history=self.history(inbound=True), jd=jd, job_ok=False)
                declined = field not in ('benefits', 'empty')
                self.assertEqual(self.saved()['status'], 'declined' if declined else 'engaged')
                self.assertEqual(mark.call_count, int(declined))
                self.assertEqual([a[0] for a in calls], ['text'])

    def test_ignored_migrates_unchanged_snapshot_and_lost_cursor_once(self):
        item = self.item()
        row = self.record(item, status='ignored')
        row.update(origin='inbound', job_ok=False, jd='虚构JD', last_msg_id='old-missing',
                   last_seen=self.module()._snapshot(item))
        S.save(self.path, {row['conv_key']: row})
        result, _, calls, _, _ = self.run_hop(history=self.history(inbound=True), job_ok=False)
        self.assertEqual(result['processed'], 1)
        self.assertEqual([a[0] for a in calls], ['text'])
        self.assertEqual(self.saved()['status'], 'engaged')
        self.br.new_tab.side_effect = [10]
        again, _, calls, _, _ = self.run_hop(history=self.history(inbound=True))
        self.assertEqual(again['processed'], 0)
        self.assertFalse(calls)

    def test_ignored_last_turn_ours_skips(self):
        row = self.record(status='ignored')
        S.save(self.path, {row['conv_key']: row})
        history = self.history() + [{'id': 'm2', 'who': 'mine', 'text': '虚构回复'}]
        _, _, calls, _, _ = self.run_hop(history=history)
        self.assertFalse(calls)

    def test_dry_decline_only_locates_and_toggle_skips_mark(self):
        self.cfg['conversation']['decline_industries'] = ['金融']
        with patch.object(A, 'mark_not_interested_company', return_value='dry_ok', create=True) as mark:
            result, _, calls, _, _ = self.run_hop(items=[dict(self.item(), brandIndustry='金融')], dry=True)
        self.assertTrue(result['plans'][0]['decline'])
        self.assertEqual(result['plans'][0]['not_interested'], 'dry_ok')
        self.assertTrue(mark.call_args.args[2]['conversation']['dry'])
        self.assertFalse(calls)
        self.assertFalse(self.path.exists())
        self.br.new_tab.side_effect = [10]
        self.cfg['conversation']['decline_not_interested'] = False
        with patch.object(A, 'mark_not_interested_company', create=True) as mark:
            self.run_hop(items=[dict(self.item(), brandIndustry='金融')])
        mark.assert_not_called()
        self.assertEqual(self.saved()['status'], 'declined')


class DeclineActionTests(unittest.TestCase):
    def test_action_dry_identity_and_ambiguity(self):
        br = Mock()
        cfg = {'conversation': {'dry': True}}
        br.json.return_value = {'ok': True}
        self.assertEqual(A.mark_not_interested_company(br, 1, cfg, 'id:fiction'), 'dry_ok')
        br.click.assert_not_called()
        br.json.return_value = {'ok': False, 'reason': 'unknown_conversation'}
        self.assertEqual(A.mark_not_interested_company(br, 1, cfg, 'id:fiction'), 'aborted:unknown_conversation')

    def test_action_company_ambiguity_closes_and_never_journals(self):
        br = Mock()
        br.click.return_value = {}
        br.json.side_effect = [
            {'ok': True}, {'ok': True},
            {'ok': False, 'reason': 'company_ambiguous', 'html': '虚构弹层'},
            {'ok': True}]
        with patch.object(A.HC, 'trace'), patch.object(A.time, 'sleep'), \
             patch.object(A.SS, 'ledger_send', side_effect=AssertionError('must not journal')):
            receipt = A.mark_not_interested_company(br, 1, {}, 'id:fiction')
        self.assertTrue(receipt.startswith('aborted:'))
        self.assertGreaterEqual(br.click.call_count, 3)  # menu, item, cancel


# Tiny offline DOM: selectors traverse the tree, and clicks mutate the same tree.
# Run the production JS, rather than returning canned selector-stage receipts.
DECLINE_DOM = r'''
const options = JSON.parse(process.argv[1]);
class Element {
  constructor(tag, cls='', text='', attrs={}) {
    this.tag=tag; this.className=cls; this.text=text; this.attrs=attrs;
    this.children=[]; this.parentElement=null; this.hidden=false;
  }
  add(e) {e.parentElement=this; this.children.push(e); return e;}
  get id() {return this.attrs.id || '';}
  set id(v) {this.attrs.id=v;}
  get innerText() {return this.text + this.children.map(e=>e.innerText).join('');}
  get textContent() {return this.innerText;}
  get offsetParent() {return this.hidden || (this.parentElement &&
    this.parentElement.offsetParent === null) ? null : {};}
  get classList() {return {contains:c=>this.className.split(/\s+/).includes(c)};}
  get outerHTML() {return `<${this.tag} class="${this.className}">${this.innerText}</${this.tag}>`;}
  getAttribute(a) {return a==='class' ? this.className : this.attrs[a] || null;}
  removeAttribute(a) {delete this.attrs[a];}
  contains(e) {return this.children.some(c=>c===e || c.contains(e));}
  matches(s) {
    const tag=s.match(/^[a-z]+/i);
    if (tag && this.tag!==tag[0]) return false;
    if ([...s.matchAll(/\.([\w-]+)/g)].some(m=>!this.classList.contains(m[1]))) return false;
    if ([...s.matchAll(/#([\w-]+)/g)].some(m=>this.id!==m[1])) return false;
    return [...s.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)]
      .every(m=>m[2] ? this.getAttribute(m[1])===m[2] : this.getAttribute(m[1])!==null);
  }
  querySelectorAll(selector) {
    const descendants=this.children.flatMap(c=>[c,...c.querySelectorAll('*')]);
    return descendants.filter(e=>selector.split(',').some(s=>{
      const parts=s.trim().replace(/\s*>\s*/g,' > ').split(/\s+/);
      let node=e, i=parts.length-1;
      if (!node.matches(parts[i--])) return false;
      while (i>=0) {
        if (parts[i]==='>') {node=node.parentElement; i--;
          if (!node || !node.matches(parts[i--])) return false;
        } else {
          node=node.parentElement;
          while (node && !node.matches(parts[i])) node=node.parentElement;
          if (!node) return false;
          i--;
        }
      }
      return true;
    }));
  }
  querySelector(s) {return this.querySelectorAll(s)[0] || null;}
}
const document=new Element('document');
const getComputedStyle=e=>({display:e.offsetParent===null ? 'none' : 'block'});
const panel=document.add(new Element('div','chat-conversation'));
const top=panel.add(new Element('div','top-info-content','',{'data-conversation-id':'fiction'}));
top.add(new Element('span','brand-name','虚构公司'));
const user=top.add(new Element('div','user-info'));
const right=user.add(new Element('div','right-content'));
const menu=right.add(new Element('div','ui-dropmenu-v1 ui-dropmenu'));
const label=menu.add(new Element('div','ui-dropmenu-label'));
const more=label.add(new Element('span','top-operation-item','更多'));
const list=menu.add(new Element('ul','more-setting')); list.hidden=true;
const item=list.add(new Element('li','','不感兴趣'));
const dialog=document.add(new Element('div','zp-dialog-wrap zp-feedback-dialog'));
dialog.hidden=true;
const container=dialog.add(new Element('div','zp-dialog-container'));
const body=container.add(new Element('div','zp-dialog-body'));
function section(title, labels) {
  const section=body.add(new Element('div','zp-type-cont'));
  section.add(new Element('div','zp-type-title',title));
  const ul=section.add(new Element('ul','zp-type-item-list'));
  return labels.map(label=>ul.add(new Element('li','zp-type-item ellipsis',label,
    options.textOnly ? {} : {title:label})));
}
const companies=section('不太感兴趣',[
  ...(options.companies ?? ['“虚构公司”公司']), '“11-17K”薪资低',
  '年龄不合适','“虚构地点”距离远','“虚构职位”职位']);
section('效率反馈',['重复推荐','职位停招/招满',...(options.interference ? ['“虚构公司”公司'] : [])]);
const other=body.add(new Element('div','zp-type-cont'));
other.add(new Element('div','zp-type-title','其他反馈'));
other.add(new Element('textarea','zp-type-textarea'));
const footer=container.add(new Element('div','zp-dialog-footer'));
const buttons=footer.add(new Element('div','zp-btns'));
const cancel=buttons.add(new Element('span','zp-btn zp-btn-outline zp-btn-cancel','取消',{ka:'dialog_cancel'}));
const sure=buttons.add(new Element('span','zp-btn zp-btn-primary zp-btn-sure','确定',{ka:'dialog_sure'}));
if (options.confirmCount===0) sure.hidden=true;
if (options.confirmCount===2) buttons.add(new Element('span','zp-btn-sure','确定'));
if (options.dialogCount===2) {
  const extra=document.add(new Element('div','zp-feedback-dialog'));
  extra.hidden=true; item.extra=extra;
}
let clicks=[], activationPolls=0, disappearancePolls=0;
require('readline').createInterface({input:process.stdin}).on('line',line=>{
  try {
    const request=JSON.parse(line);
    if (request.expression) {
      if (activationPolls && ++activationPolls>options.activationDelay) companies[0].className+=' actived';
      if (disappearancePolls && ++disappearancePolls>options.disappearanceDelay) {dialog.hidden=true; panel.hidden=true;}
      console.log(JSON.stringify({value:eval(request.expression)}));
    } else {
      const target=document.querySelector(request.selector);
      if (!target || target.offsetParent===null) throw Error('unclickable '+request.selector);
      clicks.push(target.innerText);
      if (target===more) list.hidden=false;
      else if (target===item) {dialog.hidden=false; if(item.extra) item.extra.hidden=false;}
      else if (companies.includes(target)) {
        if (options.activate!==false) {
          if (options.activationDelay) activationPolls=1;
          else target.className+=' actived';
        }
      } else if (target===cancel) dialog.hidden=true;
      else if (target===sure && options.dismiss!==false) {
        if (options.disappearanceDelay) disappearancePolls=1;
        else {dialog.hidden=true; panel.hidden=true;}
      }
      console.log(JSON.stringify({value:{ok:true},clicks}));
    }
  } catch (e) {console.log(JSON.stringify({error:String(e)}));}
});
'''


class DeclineDialogTests(unittest.TestCase):
    def run_dialog(self, dry=False, **options):
        process = subprocess.Popen(['node', '-e', DECLINE_DOM, json.dumps(options)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        clicks = []

        def request(payload):
            process.stdin.write(json.dumps(payload) + '\n')
            process.stdin.flush()
            response = json.loads(process.stdout.readline())
            self.assertNotIn('error', response, response.get('error'))
            if 'clicks' in response:
                clicks[:] = response['clicks']
            return response['value']

        br = Mock()
        br.json.side_effect = lambda tab, expression, default=None: request({'expression': expression})
        br.click.side_effect = lambda tab, selector: request({'selector': selector})
        try:
            with patch.object(A.HC, 'trace') as trace, patch.object(A.time, 'sleep'), \
                 patch.object(A.SS, 'ledger_send', side_effect=AssertionError('must not journal')):
                result = A.mark_not_interested_company(br, 1, {'conversation': {'dry': dry}}, 'id:fiction')
            return result, clicks, trace.call_args_list
        finally:
            process.stdin.close()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

    def test_real_dialog_click_order_and_closed_panel(self):
        result, clicks, _ = self.run_dialog()
        self.assertEqual(result, 'done')
        self.assertEqual(clicks, ['更多', '不感兴趣', '“虚构公司”公司', '确定'])

    def test_missing_or_ambiguous_company_cancels_without_confirm(self):
        for companies in ([], ['“虚构公司”公司', '“虚构分公司”公司']):
            with self.subTest(companies=companies):
                result, clicks, _ = self.run_dialog(companies=companies)
                self.assertTrue(result.startswith('aborted:'), result)
                self.assertEqual(clicks, ['更多', '不感兴趣', '取消'])

    def test_unselected_company_cancels_without_confirm(self):
        result, clicks, _ = self.run_dialog(activate=False)
        self.assertEqual(result, 'aborted:company_not_selected')
        self.assertEqual(clicks, ['更多', '不感兴趣', '“虚构公司”公司', '取消'])

    def test_confirm_without_disappearance_aborts(self):
        result, clicks, _ = self.run_dialog(dismiss=False)
        self.assertEqual(result, 'aborted:confirm_no_effect')
        self.assertEqual(clicks, ['更多', '不感兴趣', '“虚构公司”公司', '确定', '取消'])

    def test_efficiency_section_company_text_is_never_clicked(self):
        result, clicks, _ = self.run_dialog(companies=[], interference=True)
        self.assertTrue(result.startswith('aborted:'), result)
        self.assertEqual(clicks, ['更多', '不感兴趣', '取消'])
        result, clicks, _ = self.run_dialog(interference=True)
        self.assertEqual(result, 'done')
        self.assertEqual(clicks, ['更多', '不感兴趣', '“虚构公司”公司', '确定'])

    def test_dry_only_locates_menu_item(self):
        result, clicks, _ = self.run_dialog(dry=True)
        self.assertEqual(result, 'dry_ok')
        self.assertEqual(clicks, [])

    def test_text_fallback_and_unique_brand_mismatch_traced(self):
        result, clicks, trace = self.run_dialog(textOnly=True, companies=[' “虚构别名” 公 司 '])
        self.assertEqual(result, 'done')
        self.assertEqual(clicks, ['更多', '不感兴趣', ' “虚构别名” 公 司 ', '确定'])
        self.assertTrue(any("'brand_mismatch': True" in call.args[0] for call in trace))

    def test_confirm_must_be_unique_and_present(self):
        for count in (0, 2):
            with self.subTest(count=count):
                result, clicks, _ = self.run_dialog(confirmCount=count)
                self.assertTrue(result.startswith('aborted:'), result)
                self.assertEqual(clicks, ['更多', '不感兴趣', '“虚构公司”公司', '取消'])

    def test_multiple_visible_feedback_dialogs_abort(self):
        result, clicks, _ = self.run_dialog(dialogCount=2)
        self.assertTrue(result.startswith('aborted:'), result)
        self.assertEqual(clicks, ['更多', '不感兴趣'])

    def test_waits_for_activation_and_disappearance(self):
        result, clicks, _ = self.run_dialog(activationDelay=4, disappearanceDelay=4)
        self.assertEqual(result, 'done')
        self.assertEqual(clicks, ['更多', '不感兴趣', '“虚构公司”公司', '确定'])
