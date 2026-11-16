"""Static integration seams only; these do not prove an HTTP/PG journey.

Read only the explicit public modules below. In particular, never import the
application/API merely to check branch registration or transaction placement.
"""
import ast
from pathlib import Path
import symtable


PUBLIC = Path(__file__).resolve().parents[1] / 'src' / 'generic_agents'
READ, PROCESS = 'read_http_observation_receipt_v177', 'process_http_mapped_observation_v177'


def source(name): return (PUBLIC / (name + '.py')).read_text(encoding='utf-8')
def tree(name): return ast.parse(source(name))
def function(name, identity): return next(node for node in tree(name).body if isinstance(node, ast.FunctionDef) and node.name == identity)
def constants(node): return [part.value for part in ast.walk(node) if isinstance(part, ast.Constant)]
def call_name(node):
    if not isinstance(node, ast.Call): return None
    if isinstance(node.func, ast.Name): return node.func.id
    if isinstance(node.func, ast.Attribute): return node.func.attr
    return None
def calls(node, names): return [part for part in ast.walk(node) if isinstance(part, ast.Call) and call_name(part) in names]
def imported(node, module, identity):
    return {alias.asname or alias.name for part in ast.walk(node)
        if isinstance(part, ast.ImportFrom) and part.module == module
        for alias in part.names if alias.name == identity}
def block(nodes): return ast.Module(body=nodes, type_ignores=[])


def test_read_worker_acquisition_reference_is_global_not_an_unbound_local_import():
    symbols = symtable.symtable(source('worker'), 'public_worker.py', 'exec')
    process = next(item for item in symbols.get_children() if item.get_name() == 'process_once')
    # The new early READ branch precedes legacy processing branches. A later
    # local import shadows the global for the WHOLE function, even if untaken.
    assert process.lookup('Acquisition').is_global()
    assert not process.lookup('Acquisition').is_local()


def test_both_new_kinds_are_claimable_and_exchange_model_is_registered_before_schema_creation():
    module = tree('worker')
    kinds = next(node.value for node in module.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'SETUP_KINDS' for target in node.targets))
    assert {READ, PROCESS} <= set(ast.literal_eval(kinds))
    migration = tree('db')
    imports = [node for node in ast.walk(migration) if isinstance(node, ast.ImportFrom)
        and any(alias.name == 'http_observation_exchange_models_v177' for alias in node.names)]
    creations = calls(migration, {'create_all'})
    assert imports and creations and min(n.lineno for n in imports) < min(n.lineno for n in creations)


def test_mapped_post_uses_frozen_utf8_content_in_one_exclusive_branch_without_retry_loop():
    dispatch = function('perception', 'dispatch_acquisition')
    posts = calls(dispatch, {'post'})
    alternatives = [node for node in ast.walk(dispatch) if isinstance(node, ast.If)
        and calls(block(node.body), {'post'}) and calls(block(node.orelse), {'post'})]
    exact = []
    for alternative in alternatives:
        mapped = [call for call in calls(block(alternative.body), {'post'})
            if any(key.arg == 'content' for key in call.keywords)]
        legacy = [call for call in calls(block(alternative.orelse), {'post'})
            if any(key.arg == 'json' for key in call.keywords)]
        if mapped and legacy: exact.append((alternative, mapped, legacy))
    assert len(exact) == 1
    alternative, mapped, legacy = exact[0]
    assert len(mapped) == len(legacy) == 1 and set(map(id, posts)) == {id(mapped[0]), id(legacy[0])}
    content = next(key.value for key in mapped[0].keywords if key.arg == 'content')
    assert call_name(content) == 'encode' and {'wire_frozen', 'body', 'utf-8'} <= set(constants(content))
    assert not any(key.arg == 'json' for key in mapped[0].keywords)
    for loop in ast.walk(dispatch):
        if isinstance(loop, (ast.For, ast.While)):
            assert not calls(loop, {'post'})


def test_mapped_post_settles_before_any_generic_terminal_handler_and_legacy_keeps_handler():
    dispatch = function('perception', 'dispatch_acquisition')
    settles = imported(dispatch, 'http_observation_worker_v177', 'settle_receipt')
    assert settles and len(calls(dispatch, settles)) == 1
    generic = imported(dispatch, 'sensing_selection', 'after_receipt')
    choices = [node for node in ast.walk(dispatch) if isinstance(node, ast.If)
        and calls(block(node.body), settles) and calls(block(node.orelse), generic)]
    assert len(choices) == 1
    choice = choices[0]
    assert not calls(block(choice.body), generic)
    # No generic terminal/release handler may execute before the shared mapped
    # settlement's first strict-packet checkpoint.
    assert {id(call) for call in calls(dispatch, generic)} == {
        id(call) for call in calls(block(choice.orelse), generic)}
    assert not imported(dispatch, 'http_observation_transport_v177', 'receive')


def test_mapped_reconcile_queues_and_returns_without_synchronous_network():
    reconcile = function('perception', 'reconcile_acquisition')
    queue_names = imported(reconcile, 'http_observation_worker_v177', 'queue_read')
    branches = [node for node in ast.walk(reconcile) if isinstance(node, ast.If)
        and calls(block(node.body), queue_names)]
    assert len(branches) == 1
    branch = branches[0]
    assert any(isinstance(node, ast.Return) for node in branch.body)
    assert not calls(block(branch.body), {'get', 'post', 'bounded_response', 'query_receipt'})


def test_final_parent_uses_real_actor_and_child_activation_and_parent_status_share_final_session():
    process = function('worker', 'process_once')
    final_names = imported(process, 'http_observation_worker_v177', 'finalize_parent')
    final_calls = calls(process, final_names)
    assert len(final_calls) == 1
    final = final_calls[0]
    sessions = [node for node in ast.walk(process) if isinstance(node, ast.With)
        and final in list(ast.walk(node)) and calls(node, {'commit'})]
    session = min(sessions, key=lambda node: node.end_lineno - node.lineno)
    actors = [node for node in ast.walk(session) if isinstance(node, ast.Assign)
        and call_name(node.value) == 'actor' and 'worker_actor' in constants(node)]
    succeeded = [node for node in ast.walk(session) if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Attribute) and target.attr == 'status'
            and isinstance(target.value, ast.Name) and target.value.id == 'job' for target in node.targets)]
    commits = calls(session, {'commit'})
    assert actors and succeeded and commits
    assert max(node.lineno for node in actors) < final.lineno < min(node.lineno for node in succeeded)
    assert min(node.lineno for node in succeeded) < max(node.lineno for node in commits)
    assert not calls(block([node for node in session.body if node.lineno <= final.lineno]), {'checkpoint'})


def test_origin_dispatcher_preserves_sample_signature_and_passes_result_only_to_new_guard():
    guard = function('simulation_observation_origin_v177', 'guard')
    mapped = imported(guard, 'mapped_acquisition_origin_v177', 'guard')
    sample = imported(guard, 'simulation_origin_v176', 'guard')
    assert len(calls(guard, mapped)) == len(calls(guard, sample)) == 1
    assert {'execution', 'result'} <= {item.arg for item in calls(guard, mapped)[0].keywords}
    assert 'result' not in {item.arg for item in calls(guard, sample)[0].keywords}
    branches = [node for node in ast.walk(guard) if isinstance(node, ast.If)
        and any(isinstance(part, ast.Return) for part in node.body)]
    assert branches and calls(guard, mapped)[0].lineno < branches[0].lineno < calls(guard, sample)[0].lineno
