"""Automation voice is scoped to the selected business and saved with its evidence."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException
from generic_agents import setup_api

class AutomationVoiceTest(unittest.TestCase):
    def test_application_uses_only_selected_automation_instruction_and_replays(self):
        config=SimpleNamespace(id='cfg',version=4,data={'version':1,'connections':[],'resources':[],'businesses':['巡护','灾害'], 'instanceProfile':{'goal':'保持目标'},'setupNotes':{'巡护':'业务原说明'},'automationInstructions':{'巡护':'原自动化说明','灾害':'另一业务说明'}})
        audio=SimpleNamespace(id=str(uuid4()),version=8,status='succeeded',data={'input_sha256':'a','result_hash':'b','recipe_hash':'c'})
        p=SimpleNamespace(user_id='operator');db=SimpleNamespace(commit=lambda:None)
        payload=dict(revision=4,audio_version=8,audio_run_id=audio.id,request_id=str(uuid4()),text='执行前确认',target='automation_instruction',business='巡护',mode='append')
        body=setup_api.ApplyVoice(**payload)
        def record_review(db,p,audio,value):audio.data['review']={'candidate_receipt_id':'receipt','saved_at':'today','text':value}
        def save_draft(body,p,db):config.data=copy.deepcopy(body.configuration);config.version+=1;return {'configuration':config.data,'revision':config.version}
        def view(row):return {'configuration':row.data,'revision':row.version}
        with patch.object(setup_api,'draft',return_value=config),patch.object(setup_api,'scoped',return_value=audio),patch.object(setup_api,'require'),patch.object(setup_api,'check_version'),patch.object(setup_api,'validate_configuration'),patch.object(setup_api,'audit'),patch.object(setup_api,'save_draft',side_effect=save_draft),patch.object(setup_api,'draft_view',side_effect=view),patch('generic_agents.local_audio.checked'),patch('generic_agents.audio_api.record_review',side_effect=record_review):
            result=setup_api.apply_voice(body,p,db)
            cfg=result['configuration']
            self.assertEqual(cfg['automationInstructions'],{'巡护':'原自动化说明\n执行前确认','灾害':'另一业务说明'})
            self.assertEqual(cfg['setupNotes']['巡护'],'业务原说明');self.assertEqual(cfg['instanceProfile']['goal'],'保持目标')
            ref=cfg['voiceInputs'][0];self.assertEqual(ref['target'],'automation_instruction');self.assertEqual(ref['business'],'巡护');self.assertEqual(ref['cloud_calls'],0)
            self.assertEqual(setup_api.apply_voice(body,p,db),result)
            with self.assertRaises(HTTPException) as err:setup_api.apply_voice(setup_api.ApplyVoice(**{**payload,'text':'不同内容'}),p,db)
            self.assertEqual(err.exception.status_code,409)
    def test_malformed_automation_instructions_rejected(self):
        base={'version':1,'resources':[],'connections':[],'instanceProfile':{},'flows':{},'businesses':[]}
        for value in ([],{'业务':None},{'业务':'x'*12001}):
            with self.assertRaises(HTTPException):setup_api.validate_configuration({**base,'automationInstructions':value})

class AutomationInstructionIdentityTest(unittest.TestCase):
    def test_name_changes_and_orphan_reuse_are_blocked_until_explicitly_cleared(self):
        from generic_agents.setup_automation_instruction_guard import protect_automation_instruction_transition as guard
        original={'businesses':['巡护','灾害'],'businessAliases':{},'automationInstructions':{'巡护':'已保存要求'}}
        for replacement in [
            {**original,'businesses':['新巡护','灾害'],'businessAliases':{'新巡护':'巡护'}},
            {**original,'businesses':['灾害']},
            {**original,'businesses':['巡护','灾害'],'businessAliases':{'灾害':'巡护'}},
        ]:
            with self.assertRaises(HTTPException):guard(original,replacement)
        orphan={'businesses':['灾害'],'automationInstructions':{'巡护':'旧业务要求'}}
        with self.assertRaises(HTTPException):guard(orphan,{**orphan,'businesses':['灾害','巡护']})
        omitted={key:value for key,value in original.items() if key!='automationInstructions'}
        with self.assertRaises(HTTPException):guard(original,omitted)
        cleared={**original,'automationInstructions':{'巡护':''}}
        guard(original,cleared)
        guard(cleared,{**cleared,'businesses':['新巡护','灾害'],'businessAliases':{'新巡护':'巡护'}})
        with self.assertRaises(HTTPException):guard(original,cleared,restore=True)
