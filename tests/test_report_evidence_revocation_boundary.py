from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import User
from generic_agents import report_evidence_prebound_transport as transport
from tests.test_report_evidence_prebound_transport import prepared, _session
from tests.test_cloud_report_plan_allowance_postgres import own_database
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene


def test_committed_witness_after_external_revoke(prepared):
 fixture,call_id,messages,options=prepared
 worker,_,_,_,cap,stage,_=fixture
 with _session(fixture) as db:
  sink=transport.prepare_prebound_capture(db,worker,stage['stage_id'],call_id,messages,options)
  try:
   witness=transport.commit_transport_intent(db,worker,stage['stage_id'],call_id,messages,options,sink)
   with Session(engine()) as admin:
    admin.get(User,worker.user_id).active=False; admin.commit()
   result=witness.consume(worker,cap,stage['stage_id'],call_id,messages,options,sink)
   assert result['call_id']==call_id
  finally:sink.close()
