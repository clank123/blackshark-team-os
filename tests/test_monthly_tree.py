import copy
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('monthly',ROOT/'scripts/compile_monthly.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def sample():
    return {'store':'河畔店','month':'2030-04','records':[
        {'id':'P1','parent_id':None,'kind':'project','title':'晚餐入口','line':'统筹','status':'进行中','affiliation':'黑鲨','owner':'推进角色','start':'2030-04-01','due':'2030-04-30','acceptance':'结论','actual':'','blocker':'','dependencies':[]},
        {'id':'T1','parent_id':'P1','kind':'task','title':'套餐图','line':'平台','status':'未开始','affiliation':'河畔店','owner':'设计角色','start':'2030-04-01','due':'2030-04-02','acceptance':'图与套餐一致','actual':'','blocker':'','dependencies':[]},
        {'id':'T2','parent_id':'P1','kind':'task','title':'页面更新','line':'平台','status':'未开始','affiliation':'黑鲨','owner':'运营角色','start':'2030-04-03','due':'2030-04-04','acceptance':'更新截图与购买路径','actual':'','blocker':'','dependencies':['T1']}]}


class TreeTests(unittest.TestCase):
    def test_valid_final_tree(self):
        self.assertEqual(module.validate(sample(),True),([],[]))
    def test_draft_can_progress_with_missing_owner(self):
        d=sample();d['records'][1]['owner']=None
        e,w=module.validate(d);self.assertFalse(e);self.assertTrue(w)
        self.assertTrue(module.validate(d,True)[0])
    def test_pending_decision_has_local_scope(self):
        d=sample();d['records'][1].update(status='待拍板',owner=None,blocker='等待资源负责人')
        self.assertFalse(module.validate(d,True)[0])
    def test_duplicate_id_rejected(self):
        d=sample();d['records'][2]['id']='T1';self.assertTrue(module.validate(d)[0])
    def test_title_change_keeps_identity(self):
        d=sample();d['records'][1]['title']='新版套餐图';self.assertFalse(module.validate(d)[0]);self.assertEqual(d['records'][2]['dependencies'],['T1'])
    def test_missing_parent_rejected(self):
        d=sample();d['records'][1]['parent_id']='none';self.assertTrue(module.validate(d)[0])
    def test_empty_root_parent_cannot_silently_hide_records(self):
        d=sample();d['records'][0]['parent_id']='';self.assertTrue(module.validate(d)[0])
    def test_placeholder_owner_is_not_final_assignment(self):
        for owner in ['待定', 'TBD', '运营（待确认）', '  ']:
            with self.subTest(owner=owner):
                d=sample();d['records'][1]['owner']=owner
                self.assertFalse(module.validate(d)[0])
                self.assertTrue(module.validate(d,True)[0])
    def test_placeholder_acceptance_is_not_final(self):
        d=sample();d['records'][1]['acceptance']='待补'
        self.assertTrue(module.validate(d,True)[0])
    def test_concrete_acceptance_can_describe_unknowns(self):
        d=sample();d['records'][1]['acceptance']='核对套餐信息；未知逐项列出，不写未经确认承诺'
        self.assertEqual(module.validate(d,True),([],[]))
    def test_parent_cycle_rejected(self):
        d=sample();d['records'][0]['parent_id']='P1';self.assertTrue(module.validate(d)[0])
    def test_dependency_cycle_rejected(self):
        d=sample();d['records'][1]['dependencies']=['T2'];self.assertTrue(module.validate(d)[0])
    def test_missing_dependency_rejected(self):
        d=sample();d['records'][1]['dependencies']=['no'];self.assertTrue(module.validate(d)[0])
    def test_invalid_dates_rejected(self):
        d=sample();d['records'][1]['due']='2030-02-30';self.assertTrue(module.validate(d)[0])
    def test_reversed_dates_rejected(self):
        d=sample();d['records'][1]['due']='2030-03-30';self.assertTrue(module.validate(d)[0])
    def test_parent_cannot_complete_before_children(self):
        d=sample();d['records'][0].update(status='已完成',actual='文件生成');self.assertTrue(module.validate(d)[0])
    def test_completed_leaf_requires_evidence(self):
        d=sample();d['records'][1]['status']='已完成';self.assertTrue(module.validate(d,True)[0])
    def test_cancelled_dependency_requires_handling(self):
        d=sample();d['records'][1]['status']='取消';self.assertTrue(module.validate(d,True)[0])
    def test_cancelled_parent_cannot_have_active_child(self):
        d=sample();d['records'][0]['status']='取消';self.assertTrue(module.validate(d)[0])
    def test_export_preserves_relations_and_no_external_success(self):
        d=sample();d['store']='河畔 "A" 店';d['records'][1]['title']='=HYPERLINK("x")'
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/'new';result=module.export(d,out,True)
            self.assertEqual(set(result['external'].values()),{'未执行'})
            with (out/'运营推进表.csv').open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
            self.assertEqual(rows[1]['父任务'],'P1');self.assertEqual(rows[1]['任务编号'],'T1')
            self.assertTrue(rows[1]['动作'].startswith("'="))
            schema=json.loads((out/'运营推进表结构.json').read_text());self.assertEqual(schema['store'],d['store'])
            self.assertTrue(schema['embedding']['required'])
            self.assertIn('  - **', (out/'推进表预览.md').read_text())
            with self.assertRaises(FileExistsError):module.export(d,out)
    def test_invalid_input_writes_nothing(self):
        d=sample();d['records'][0]['parent_id']='P1'
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/'bad'
            with self.assertRaises(ValueError):module.export(d,out)
            self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()
