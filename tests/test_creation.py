import json
from unittest.mock import AsyncMock
import pytest
from agent.creation import CreationEngine, CreationStep
from agent.engine import Engine
from agent.models import Plan
from agent.netsuite import NetSuiteError
from agent.sdf import CustomType, SDFExecutor, xml_for


def job(kind='ask'):
    return {'id':'1','lease':'lease','request':{'kind':kind,'mode':'create','question':'Create a customer named Acme'}}


async def test_disabled_creation_never_calls_model_or_netsuite(settings, store):
    ns,llm=AsyncMock(),AsyncMock()
    result=await CreationEngine(settings,store,ns,llm).process(job())
    assert result['kind']=='unsupported'
    ns.call.assert_not_called();llm.creation_plan.assert_not_called()


async def test_custom_type_without_sdf_returns_setup_guidance(settings, store):
    settings.creation_enabled = True
    settings.sdf_enabled = False
    settings.sdf_auth_id = ''
    ns, llm = AsyncMock(), AsyncMock()
    j = job()
    j['request']['question'] = (
        'Create a new custom record type named Agent Equipment Inspection, '
        'script ID customrecord_agent_inspection, with a Date field custrecord_ai_date '
        'and an optional text field custrecord_ai_notes. Include the Name field.')
    result = await CreationEngine(settings, store, ns, llm).process(j)
    assert result['kind'] == 'unsupported'
    assert 'AGENT_SDF_ENABLED=true' in result['message']
    assert 'account:setup:ci' in result['message']
    ns.call.assert_not_called()
    llm.creation_plan.assert_not_called()


async def test_custom_type_with_sdf_builds_draft_without_model(settings, store):
    settings.creation_enabled = True
    settings.sdf_enabled = True
    settings.sdf_auth_id = 'agent-sdf'
    settings.integration_role_id = 'customrole_nsa_worker'
    ns, llm = AsyncMock(), AsyncMock()
    ns.call.side_effect = [
        {'service': 'netsuite-agent', 'role': 1122, 'role_script_id': 'customrole_nsa_worker'},
        {'result': {'kind': 'draft', 'operation': 'custom_type'}},
    ]
    engine = CreationEngine(settings, store, ns, llm)
    engine.sdf = AsyncMock()
    engine.sdf.prepare.return_value = {
        'digest': 'a' * 64, 'xml': '<customrecordtype/>', 'entry_roles': ['customrole_nsa_worker']}
    j = job()
    j['request']['question'] = (
        'Create a new custom record type named Agent Equipment Inspection, '
        'script ID customrecord_agent_inspection, with a Date field custrecord_ai_date '
        'and an optional text field custrecord_ai_notes. Include the Name field.')
    result = await engine.process(j)
    assert result['kind'] == 'draft'
    llm.creation_plan.assert_not_called()
    engine.sdf.prepare.assert_awaited_once()
    assert engine.sdf.prepare.await_args.kwargs.get('entry_roles') == ['customrole_nsa_worker']
    assert [c.args[0] for c in ns.call.call_args_list] == ['ping', 'creation_type_draft']
    assert ns.call.call_args_list[1].kwargs.get('entry_roles') == ['customrole_nsa_worker']


async def test_live_metadata_then_draft_without_write(settings, store):
    settings.creation_enabled=True
    ns,llm=AsyncMock(),AsyncMock()
    ns.call.side_effect=[{'fields':[{'id':'companyname'}]},
                         {'result':{'kind':'draft','nonce':'server-nonce'}}]
    llm.creation_plan.return_value=CreationStep(
        action='prepare',record_type='customer',
        payload_json=json.dumps({'fields':{'companyname':'Acme'}}))
    result=await CreationEngine(settings,store,ns,llm).process(job())
    assert result['kind']=='draft'
    assert [c.args[0] for c in ns.call.call_args_list]==['creation_inspect','creation_prepare']
    assert llm.creation_plan.await_count==1


async def test_cued_standard_create_skips_capabilities(settings, store):
    settings.creation_enabled = True
    ns, llm = AsyncMock(), AsyncMock()
    ns.call.side_effect = [
        NetSuiteError(
            "creation_inspect [INSUFFICIENT_PERMISSION]: Permission Violation: "
            "You need the 'Lists -> Employee Record' permission to access this page."),
    ]
    j = job()
    j['request']['question'] = (
        'Ok i want to create a new employee named Marry Jane under subsidiary HoneyComb MFG.')
    result = await CreationEngine(settings, store, ns, llm).process(j)
    assert result['kind'] == 'error'
    assert 'INSUFFICIENT_PERMISSION' in result['message'] or 'Permission Violation' in result['message']
    assert [c.args[0] for c in ns.call.call_args_list] == ['creation_inspect']
    llm.creation_plan.assert_not_called()


async def test_missing_required_value_is_a_clarification(settings, store):
    settings.creation_enabled=True;ns,llm=AsyncMock(),AsyncMock()
    # Ambiguous create phrasing forces capabilities before inspect.
    j = job()
    j['request']['question'] = 'Please set up this party for me'
    ns.call.side_effect=[{'capabilities':{}},{'fields':[]},{'issues':['subsidiary is required']}]
    llm.creation_plan.side_effect=[CreationStep(action='inspect',record_type='customer'),CreationStep(action='prepare',record_type='customer')]
    result=await CreationEngine(settings,store,ns,llm).process(j)
    assert result['kind']=='clarify' and 'subsidiary' in result['message']


async def test_approved_execution_uses_remote_draft_not_model_and_bypasses_cached_preview(settings, store):
    settings.creation_enabled=True;ns,llm=AsyncMock(),AsyncMock()
    store.complete('1',{'kind':'draft'})
    ns.call.side_effect=[{'draft':{'operation':'record'}},{'result':{'kind':'created','id':'55'}}]
    engine=Engine(settings,store,ns,llm)
    assert (await engine.process(job('execute')))['id']=='55'
    llm.plan.assert_not_called();llm.creation_plan.assert_not_called()
    assert (await engine.process(job('execute')))['id']=='55'
    assert ns.call.call_count==2


async def test_creation_auto_routing(settings, store):
    settings.creation_enabled=True;ns,llm=AsyncMock(),AsyncMock()
    llm.last_metrics={}
    # Auto mode with an obvious create cue should skip the SuiteQL planner entirely.
    llm.creation_plan.return_value=CreationStep(
        action='prepare', record_type='customer',
        payload_json='{"fields":{"companyname":"Acme"}}')
    ns.call.side_effect=[{'fields':[{'id':'companyname','type':'text'}]},
                         {'result':{'kind':'draft','nonce':'n1'}}]
    j=job();j['request']['mode']='auto';j['request']['question']='Create a customer named Acme'
    result=await Engine(settings,store,ns,llm).answer(j)
    assert result['kind']=='draft'
    llm.plan.assert_not_called()
    assert [c.args[0] for c in ns.call.call_args_list] == ['creation_inspect', 'creation_prepare']


def spec():
    return CustomType(script_id='customrecord_inspection',name='Inspection & Checks',fields=[
        {'script_id':'custrecord_inspection_date','label':'Date <required>','field_type':'DATE','mandatory':True}])


def test_sdf_xml_is_escaped_and_cannot_inject_code():
    xml=xml_for(spec())
    assert 'Inspection &amp; Checks' in xml and '&lt;required&gt;' in xml
    assert '<ismandatory>T</ismandatory>' in xml
    assert '<permittedrole>ADMINISTRATOR</permittedrole>' in xml
    with_role=xml_for(spec(), entry_roles=['customrole_nsa_worker', 'ADMINISTRATOR', '1122', 'customrole_nsa_worker'])
    assert '[scriptid=customrole_nsa_worker]' in with_role
    assert '1122' not in with_role and 'accountspecificvalue' not in with_role
    assert with_role.count('<permission>') == 2
    for script in ['../../evil','customrecord_nsa_job','customer']:
        with pytest.raises(ValueError):CustomType(script_id=script,name='bad')
    with pytest.raises(ValueError):CustomType(script_id='customrecord_safe',name='safe',fields=[spec().fields[0]]*2)


async def test_sdf_validation_precedes_approval_and_changed_artifacts_are_rejected(settings, tmp_path):
    settings.sdf_auth_id='agent-deployer';settings.account='TSTDRV_TEST'
    executor=SDFExecutor(settings);executor.run=AsyncMock();executor.ensure_auth=AsyncMock()
    artifact=await executor.prepare('1',spec())
    executor.run.assert_not_awaited()  # Draft preparation is local-only for speed.
    await executor.verify('1',spec(),artifact['digest'])
    executor.ensure_auth.assert_awaited()
    executor.run.assert_awaited_once_with('1','project:validate')
    (executor.folder('1')/'Objects/customrecord_inspection.xml').write_text('<tampered/>')
    with pytest.raises(ValueError,match='changed'):
        await executor.verify('1',spec(),artifact['digest'])
    with pytest.raises(ValueError):executor.folder('../other')


async def test_sdf_bounded_subprocess_verifies_destination_and_status(settings, tmp_path):
    settings.account='ACCOUNT_A';settings.sdf_auth_id='auth'
    binary=tmp_path/'fake-suitecloud'
    settings.suitecloud_bin=str(binary)
    executor=SDFExecutor(settings);executor.folder('1').mkdir(parents=True)
    binary.write_text('#!/bin/sh\necho "Account ID: ACCOUNT_B"\necho "Status: SUCCESS"\n');binary.chmod(0o700)
    with pytest.raises(ValueError,match='target account'):await executor.run('1','project:validate')
    binary.write_text(
        '#!/bin/sh\necho "Account ID: ACCOUNT_A"\necho "Status: FAILED"\n'
        'echo "Details: This custom field already exists"\n')
    with pytest.raises(ValueError, match='already exists'):
        await executor.run('1', 'project:validate')
    binary.write_text('#!/bin/sh\necho "Account ID: ACCOUNT_A"\necho "Status: SUCCESS"\n')
    await executor.run('1','project:validate')


async def test_sdf_executor_never_deploys_before_remote_approval_and_begin(settings, store):
    settings.creation_enabled=True;settings.sdf_enabled=True;settings.sdf_auth_id='agent-sdf'
    ns,llm=AsyncMock(),AsyncMock()
    draft={'operation':'custom_type','specification':spec().model_dump(),'artifact_hash':'a'*64,
           'entry_roles':['customrole_nsa_worker']}
    ns.call.side_effect=[{'draft':draft},{'available':True},{'started':True},{'result':{'kind':'created'}}]
    engine=CreationEngine(settings,store,ns,llm);engine.sdf=AsyncMock()
    result=await engine.process(job('execute'))
    assert result['kind']=='created' and store.get('creation_schema_refresh')
    assert store.get('creation_schema_probe') == ['customrecord_inspection']
    assert 'integration role' in result['message']
    assert [c.args[0] for c in ns.call.call_args_list]==['creation_approved','creation_type_available','creation_begin','creation_receipt']
    engine.sdf.verify.assert_awaited_once()
    assert engine.sdf.verify.await_args.kwargs.get('entry_roles') == ['customrole_nsa_worker']
    engine.sdf.deploy.assert_awaited_once_with('1', ensure_auth=False)


async def test_uncertain_sdf_deployment_is_not_retried(settings, store):
    settings.creation_enabled=True;settings.sdf_enabled=True;settings.sdf_auth_id='agent-sdf'
    ns,llm=AsyncMock(),AsyncMock()
    ns.call.side_effect=[{'draft':{'operation':'custom_type','specification':spec().model_dump(),'artifact_hash':'a'*64}},{},{}]
    engine=CreationEngine(settings,store,ns,llm);engine.sdf=AsyncMock()
    engine.sdf.deploy.side_effect=ValueError(
        'SDF project:deploy failed: This custom field already exists. '
        'Custom field script IDs are account-global; choose unique custrecord_... IDs.')
    result=await engine.process(job('execute'))
    assert result['kind']=='uncertain'
    assert 'already exists' in result['message']
    assert engine.sdf.deploy.await_count==1


async def test_approval_failure_cannot_reach_sdf(settings, store):
    settings.creation_enabled=True;settings.sdf_enabled=True;settings.sdf_auth_id='agent-sdf'
    ns,llm=AsyncMock(),AsyncMock();ns.call.side_effect=NetSuiteError('Explicit approval required')
    engine=CreationEngine(settings,store,ns,llm);engine.sdf=AsyncMock()
    with pytest.raises(NetSuiteError):await engine.process(job('execute'))
    engine.sdf.deploy.assert_not_called()


async def test_premature_model_clarification_is_grounded_in_live_metadata(settings, store):
    settings.creation_enabled=True;ns,llm=AsyncMock(),AsyncMock()
    ns.call.side_effect=[{'fields':[{'id':'companyname','mandatory':True}]},
                         {'result':{'kind':'draft'}}]
    llm.creation_plan.side_effect=[CreationStep(action='clarify',record_type='customer',message='Which subsidiary?'),
        CreationStep(action='prepare',record_type='customer',payload_json='{"fields":{"companyname":"Acme"}}')]
    result=await CreationEngine(settings,store,ns,llm).process(job())
    assert result['kind']=='draft'
    assert ns.call.call_args_list[0].args[0]=='creation_inspect'


async def test_repeated_subsidiary_question_gets_one_grounded_recheck(settings, store):
    settings.creation_enabled = True
    ns, llm = AsyncMock(), AsyncMock()
    ns.call.side_effect = [
        {'capabilities': {'standard': ['salesorder']}},
        {'fields': [{'id': 'subsidiary', 'mandatory': True}]},
        {'result': {'kind': 'draft'}}]
    llm.creation_plan.side_effect = [
        CreationStep(action='inspect', record_type='salesorder'),
        CreationStep(action='clarify', record_type='salesorder', message='Which subsidiary?'),
        CreationStep(action='prepare', record_type='salesorder',
                     payload_json='{"fields":{"subsidiary":{"id":"1"}}}')]
    j = job()
    j['request']['question'] = 'Use subsidiary ID 1'
    j['history'] = [{'question': 'Create a sales order for TEST US Customer'}]
    result = await CreationEngine(settings, store, ns, llm).process(j)
    assert result['kind'] == 'draft'
    context = llm.creation_plan.call_args.args[0]
    assert context['prepare_record_type'] == 'salesorder'
    assert context['history'] == j['history']
    assert ns.call.call_args.kwargs['payload']['fields']['subsidiary'] == {'id': '1'}
    assert all(c.args[0] != 'creation_execute' for c in ns.call.call_args_list)


async def test_name_encoded_as_id_has_payload_context_for_repair(settings, store):
    settings.creation_enabled = True
    ns, llm = AsyncMock(), AsyncMock()
    wrong = '{"fields":{"entity":{"id":"TEST US Customer"}}}'
    corrected = '{"fields":{"entity":{"text":"TEST US Customer"}}}'
    ns.call.side_effect = [
        {'fields': [{'id': 'entity'}]},
        NetSuiteError('entity: invalid internal ID'), {'result': {'kind': 'draft'}}]
    steps = iter([CreationStep(action='prepare', record_type='salesorder', payload_json=wrong),
                  CreationStep(action='prepare', record_type='salesorder', payload_json=corrected)])
    async def plan(context):
        step = next(steps)
        if step.payload_json == corrected:
            assert context['rejected_payload_json'] == wrong
            assert 'names belong in' in context['repair_instruction']
        return step
    llm.creation_plan.side_effect = plan
    j = job()
    j['request']['question'] = 'Create a sales order for customer TEST US Customer'
    result = await CreationEngine(settings, store, ns, llm).process(j)
    assert result['kind'] == 'draft'
    assert ns.call.call_args.kwargs['payload']['fields']['entity'] == {'text': 'TEST US Customer'}


def test_creation_reference_normalization_and_default_placeholders():
    from agent.creation import normalize_creation_payload
    metadata = {'fields': [{'id': 'currency', 'type': 'select'}, {'id': 'entity', 'type': 'select'}],
                'sublists': {'item': [{'id': 'item', 'type': 'select'}]}}
    for empty in [None, '', {}, {'id': ''}, {'text': None}]:
        result = normalize_creation_payload({'fields': {'currency': empty, 'entity': 'TEST US Customer'},
            'sublists': {'item': [{'item': 'F3 PL Hardware', 'quantity': 2}]}}, metadata)
        assert 'currency' not in result['fields']
        assert result['fields']['entity'] == {'text': 'TEST US Customer'}
        assert result['sublists']['item'][0] == {'item': {'text': 'F3 PL Hardware'}, 'quantity': 2}
    assert normalize_creation_payload({'fields': {'currency': 'USD'}}, metadata)['fields']['currency'] == {'text': 'USD'}
    with pytest.raises(ValueError, match='omit unspecified'):
        normalize_creation_payload({'fields': {'currency': {'value': 'USD'}}}, metadata)
