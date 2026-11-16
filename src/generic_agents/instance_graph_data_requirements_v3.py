"""Data handling derived from actual graph node contracts, without fake steps."""
import copy
from .contracts import digest
from . import instance_data_requirements as legacy

SCHEMA='instance-data-requirements-v3'
EXTRA=('semantic_runtime_plan','object_origins','full_semantic_schema_adoptions','relation_types','relation_origins')


def build_requirements(snapshot):
    from .instance_semantic_runtime_dispatch import modern
    runtime=modern(snapshot); BUSINESS_MARKER=runtime.BUSINESS_MARKER
    plan=runtime.verify_plan(snapshot)
    projected=copy.deepcopy(snapshot)
    projected['businesses']=[b for b in projected['businesses'] if BUSINESS_MARKER not in b]
    base=legacy.build_requirements(projected)
    rows={b['id']:b for b in base['businesses']}
    bindings={a['binding']['business_name']:a['binding'] for a in plan['graph_adoptions']}
    for business in snapshot['businesses']:
        if BUSINESS_MARKER not in business:continue
        binding=bindings[business['name']];graph=binding['graph']
        issues=[*snapshot['general_issues'],*business['issues']]
        usable=business['available'] and not issues
        outputs=[]
        if usable:
            source_steps={s['id']:s for s in business['flow']['steps']}
            for node in graph['nodes']:
                if node['kind'] not in {'human','ability','wait'}:continue
                spec=node.get('execution',{})
                schema=(node['output_schema'] if node['kind']=='human' else spec.get('output_schema'))
                if node['kind']=='wait':schema=next(s['output_schema'] for s in graph['subscriptions'] if s['id']==node['subscription_id'])
                source=source_steps.get(node['source_step_id'],{})
                value={'step_id':node['source_step_id'] or node['id'],'node_id':node['id'],
                    'step_name':source.get('name') or ('等待外部回执' if node['kind']=='wait' else '协同处理'),
                    'kind':node['kind'],'agent_role':spec.get('agent_role','human' if node['kind']=='human' else 'external_event'),
                    'output_schema':copy.deepcopy(schema),'output_schema_hash':digest(schema),
                    'input_bindings':copy.deepcopy(spec.get('input_bindings',{})),
                    'fields':[{'name':key,'schema':copy.deepcopy(value)} for key,value in sorted(schema.get('properties',{}).items())],
                    'storage':'existing_evidence_and_receipts','status':'candidate',
                    'activation_identity':'frozen_on_each_actual_activation'}
                if spec:value.update({k:spec[k] for k in ('capability_id','capability_hash','release_version')})
                if node['kind']=='wait' and binding['schema'] in {'report-full-semantic-graph-binding-v4',
                        'report-object-graph-binding-v171'}:
                    pin=binding['event_source_bindings'][node['subscription_id']]
                    value.update(producer_id=pin['producer_id'],contract_hash=pin['contract_hash'],
                        source_scope=copy.deepcopy(pin['scope']),
                        admission_policy=copy.deepcopy(binding['event_admission_policy']))
                outputs.append(value)
            if binding['schema'] in {'authored-service-workflow-binding-v270', 'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'}:
                from .authored_service_data_outputs_v270 import outputs as perception_outputs
                outputs.extend(perception_outputs(binding))
            if binding['schema'] == 'business-flow-nested-execution-binding-v195' and 'observation_extractor_pins' in binding:
                from .authored_service_data_outputs_v270 import project_outputs
                outputs.extend(project_outputs(binding['observation_extractor_pins'], binding['binding_hash']))
        rows[business['id']]={'id':business['id'],'name':business['name'],'status':'derived' if usable else 'blocked',
            'issues':issues,'outputs':outputs,'summary':'分别保存每次人工处理、注册能力和外部回执，保留分支代次、来源及核查状态；不自动认定现场事实。'}
    dependencies={key:copy.deepcopy(snapshot[key]) for key in (*legacy.DEPENDENCIES,*EXTRA)}
    value={k:v for k,v in base.items() if k not in ('schema','dependency_hash','dependency_hashes','requirements_hash','businesses')}
    value.update(schema=SCHEMA,dependency_hash=digest(dependencies),
        dependency_hashes={k:digest(v) for k,v in dependencies.items()},
        businesses=[rows[b['id']] for b in snapshot['businesses']])
    return {**value,'requirements_hash':digest(value)}
