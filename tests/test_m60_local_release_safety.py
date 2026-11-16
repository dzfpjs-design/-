"""Pure first-slice checks. --noconftest: no DB, service, model or .env access."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


SPEC = importlib.util.spec_from_file_location('m60_release_checker_under_test',
    Path(__file__).parents[1] / 'scripts/m60_local_release_check.py')
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)
SECRET = 'secret-value-NEVER-PUBLISH'
RUN = 'a' * 32
DBNAME = 'ga_test_m60_m59_restore_' + RUN
URL = 'postgresql+psycopg://owner:' + SECRET + '@127.0.0.1:55432/' + DBNAME
MARKER = 'm60-m59-release-owned:' + RUN


@pytest.fixture(autouse=True)
def no_uncontrolled_database(monkeypatch):
    monkeypatch.setattr(m, 'create_engine', lambda *a, **kw: pytest.fail('uncontrolled database connection'))


def contract(**extra):
    return m.OwnedRestore(**{'database_url': URL, 'run_id': RUN, 'oid': 123,
                            'marker': MARKER, **extra})


def identity(**extra):
    return {'database_name': DBNAME, 'oid': 123, 'marker': MARKER,
            'transaction_read_only': 'on', **extra}


def refuses(code, action):
    with pytest.raises(m.CheckError) as caught:
        action()
    assert caught.value.code == code
    assert SECRET not in str(caught.value) and SECRET not in repr(caught.value)


def source_bundle(tmp_path):
    root = tmp_path.resolve()
    source = root / 'candidate'
    package = source / 'generic_agents'
    package.mkdir(parents=True)
    code = package / '__init__.py'
    code.write_bytes(b'value = 1\n')
    manifest = source / 'source-manifest.json'
    manifest.write_text(json.dumps({'files': [{'path': 'generic_agents/__init__.py',
        'sha256': hashlib.sha256(code.read_bytes()).hexdigest(), 'bytes': code.stat().st_size}]}))
    fixed = m.ManifestContract('candidate', 'candidate/source-manifest.json',
                              hashlib.sha256(manifest.read_bytes()).hexdigest())
    return root, source, code, manifest, fixed


def inventory(rows=None, *, files=None, schema=None):
    return m.make_inventory({'old': rows if rows is not None else [{'id': 1}, {'id': 1}]},
        {'old': schema or {'columns': [{'name': 'id', 'type': 'INTEGER', 'nullable': False}]}},
        files or {'original.bin': {'sha256': 'b' * 64, 'bytes': 8}})


def test_valid_explicit_restore_contract_and_private_repr():
    fixed = contract()
    m.verify_restore_identity(fixed, identity())
    assert SECRET not in repr(fixed) and DBNAME not in repr(fixed)


@pytest.mark.parametrize('change', [
    {'database_url': URL.replace(DBNAME, 'generic_agents')},
    {'database_url': URL.replace('127.0.0.1', 'localhost')},
    {'database_url': URL.replace('127.0.0.1', 'remote.example')},
    {'database_url': URL.replace('55432', '5432')},
    {'database_url': URL + '?host=remote.example'},
    {'database_url': URL.replace('postgresql+psycopg', 'sqlite')},
    {'database_url': URL.replace(':' + SECRET, '')},
    {'database_url': 'not a URL ' + SECRET},
    {'run_id': 'b' * 32}, {'run_id': '../' + SECRET}, {'run_id': True},
    {'oid': True}, {'oid': 0}, {'oid': '123'}, {'marker': MARKER + SECRET},
])
def test_nonlocal_normal_or_mismatched_restore_contract_rejected(change):
    refuses(m.Code.CONTRACT, lambda: contract(**change))


@pytest.mark.parametrize('change', [
    {'database_name': 'generic_agents'}, {'database_name': DBNAME.replace(RUN, 'b' * 32)},
    {'oid': 124}, {'oid': True}, {'marker': 'm60-m59-release-owned:' + 'b' * 32},
    {'marker': None}, {'transaction_read_only': 'off'}, {'extra': SECRET},
])
def test_wrong_database_oid_run_comment_or_writable_transaction_rejected(change):
    refuses(m.Code.IDENTITY, lambda: m.verify_restore_identity(contract(), identity(**change)))


def test_manifest_exact_source_bytes_and_file_set(tmp_path):
    root, _, _, _, fixed = source_bundle(tmp_path)
    assert m.verify_manifest(root, fixed) == {
        'generic_agents/__init__.py': {'sha256': hashlib.sha256(b'value = 1\n').hexdigest(), 'bytes': 10}}


@pytest.mark.parametrize('mutation', ['code', 'extra', 'missing', 'manifest', 'symlink_file',
    'symlink_directory', 'symlink_root', 'manifest_symlink', 'fifo'])
def test_changed_manifest_source_or_nonregular_tree_rejected(tmp_path, mutation):
    root, source, code, manifest, fixed = source_bundle(tmp_path)
    outside = root / 'outside'
    outside.mkdir()
    if mutation == 'code': code.write_text(SECRET)
    elif mutation == 'extra': (source / 'unlisted.py').write_text(SECRET)
    elif mutation == 'missing': code.unlink()
    elif mutation == 'manifest': manifest.write_text(SECRET)
    elif mutation == 'symlink_file':
        target = outside / 'code'; target.write_bytes(code.read_bytes()); code.unlink(); code.symlink_to(target)
    elif mutation == 'symlink_directory':
        package = code.parent; target = outside / 'pkg'; package.rename(target); package.symlink_to(target)
    elif mutation == 'symlink_root':
        target = outside / 'source'; source.rename(target); source.symlink_to(target)
    elif mutation == 'manifest_symlink':
        target = outside / 'manifest'; manifest.rename(target); manifest.symlink_to(target)
    else: os.mkfifo(source / 'not-a-file')
    with pytest.raises(m.CheckError) as caught: m.verify_manifest(root, fixed)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize('path', ['../outside', '/etc/passwd', 'candidate/../outside',
    'candidate//source-manifest.json', 'candidate/./source-manifest.json', 'candidate\\secret'])
def test_manifest_contract_rejects_path_traversal_before_reading(tmp_path, path):
    root, _, _, _, fixed = source_bundle(tmp_path)
    refuses(m.Code.PATH, lambda: m.verify_manifest(root, m.ManifestContract(fixed.directory, path, fixed.sha256)))


@pytest.mark.parametrize('mutation', ['duplicate_path', 'duplicate_json_key', 'invalid_hash', 'traversal_entry', 'bad_size'])
def test_ambiguous_manifest_records_rejected_even_with_matching_manifest_sha(tmp_path, mutation):
    root, _, _, manifest, fixed = source_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    if mutation == 'duplicate_path': data['files'].append(data['files'][0])
    elif mutation == 'invalid_hash': data['files'][0]['sha256'] = SECRET
    elif mutation == 'traversal_entry': data['files'][0]['path'] = '../' + SECRET
    elif mutation == 'bad_size': data['files'][0]['bytes'] = True
    if mutation == 'duplicate_json_key': manifest.write_text('{"files":[],"files":[]}')
    else: manifest.write_text(json.dumps(data))
    fixed = m.ManifestContract(fixed.directory, fixed.manifest, hashlib.sha256(manifest.read_bytes()).hexdigest())
    with pytest.raises(m.CheckError) as caught: m.verify_manifest(root, fixed)
    assert SECRET not in str(caught.value)


def test_original_inventory_hashes_raw_bytes_and_nested_file_set(tmp_path):
    root = tmp_path.resolve(); (root / 'nested').mkdir();(root / 'nested/raw.bin').write_bytes(b'\xff\x00\r\n')
    assert m.file_inventory(root) == {'nested/raw.bin': {'sha256': hashlib.sha256(b'\xff\x00\r\n').hexdigest(), 'bytes': 4}}


@pytest.mark.parametrize('mutation', ['missing', 'changed_bytes', 'renamed', 'extra'])
def test_original_file_loss_or_byte_changes_cannot_pass_comparison(tmp_path, mutation):
    root=tmp_path.resolve();original=root/'original';original.write_bytes(b'1234')
    before=inventory(files=m.file_inventory(root))
    if mutation=='missing':original.unlink()
    elif mutation=='changed_bytes':original.write_bytes(b'4321')
    elif mutation=='renamed':original.rename(root/'renamed')
    else:(root/'extra').write_bytes(b'1234')
    after=m.make_inventory({'old':[{'id':1},{'id':1}]}, {'old':before['tables']['old']['schema']}, m.file_inventory(root))
    refuses(m.Code.FILES, lambda:m.compare_inventories(before,after))


def test_duplicate_rows_are_preserved_and_order_is_irrelevant():
    assert m.row_multiset([{'x':1},{'x':1},{'x':2}]) == m.row_multiset([{'x':2},{'x':1},{'x':1}])
    assert len(m.row_multiset([{'x':1},{'x':1}]))==2
    refuses(m.Code.ROWS, lambda:m.compare_inventories(inventory(),inventory([{'id':1}])))


@pytest.mark.parametrize('left,right', [(True,1),(1,1.0),(date(2026,1,1),'2026-01-01'),
    (Decimal('1.2'),'1.2'),(b'abc','abc'),({'x':1},[['x',1]])])
def test_canonical_rows_do_not_conflate_distinct_database_values(left,right):
    assert m.row_multiset([{'value':left}]) != m.row_multiset([{'value':right}])


def test_explicit_new_schema_contract_is_required_and_new_tables_must_be_empty():
    before=inventory();new_schema={'columns':[{'name':'new_field','nullable':False}]}
    after=m.make_inventory({'old':[{'id':1},{'id':1}],'new':[]},
        {'old':before['tables']['old']['schema'],'new':new_schema},before['files'])
    refuses(m.Code.TABLES,lambda:m.compare_inventories(before,after))
    result=m.compare_inventories(before,after,added_empty_schemas={'new':new_schema})
    assert m.public_summary(result)['new_empty_table_count']==1
    refuses(m.Code.SCHEMA,lambda:m.compare_inventories(before,after,added_empty_schemas={'new':{'columns':[]}}))
    after['tables']['new']['row_hashes']=m.row_multiset([{'new_field':1}])
    refuses(m.Code.ROWS,lambda:m.compare_inventories(before,after,added_empty_schemas={'new':new_schema}))


def test_changed_old_schema_and_extra_table_are_rejected():
    before=inventory();after=inventory(schema={'columns':[]})
    refuses(m.Code.SCHEMA,lambda:m.compare_inventories(before,after))
    refuses(m.Code.TABLES,lambda:m.compare_inventories(before,before,added_empty_schemas={'old':{}}))


def test_public_summary_does_not_echo_errors_contracts_names_or_secret_values():
    assert m.public_summary(error=RuntimeError(URL+SECRET)) == {
        'format':'m60-public-check-v1','verified':False,'code':'check_failed'}
    assert m.public_summary(error=m.CheckError(SECRET))['code']=='check_failed'
    assert m.public_summary({'database_url':URL})['verified'] is False
    summary=m.compare_inventories(inventory(),inventory())
    assert m.public_summary(summary)=={'format':'m60-public-check-v1','verified':True,
        'old_table_count':1,'old_record_count':2,'file_count':1,'new_empty_table_count':0}
    assert SECRET not in json.dumps(m.public_summary(summary))
    assert m.public_summary(m.CheckSummary(True,2,1,0))['verified'] is False
    error=m.CheckError(m.Code.IDENTITY);error.code=SECRET
    assert m.public_summary(error=error)['code']=='check_failed'


class FakeResult:
    def __init__(self,value):self.value=value
    def mappings(self):return self
    def one(self):return self.value
    def scalar_one(self):return self.value


def install_fake_engine(monkeypatch, observed, *, error=None):
    events=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @contextmanager
        def begin(self):yield
        def execute(self,sql,params=None):
            statement=str(sql);events.append(statement)
            if error:raise error
            if statement=='SET TRANSACTION READ ONLY':return FakeResult(None)
            if statement.startswith('/* m60_untracked_catalog_guard */'):return FakeResult(False)
            assert 'FROM pg_catalog.pg_database' in statement,'table accessed before identity check'
            return FakeResult(observed)
    class Engine:
        def connect(self):return Connection()
        def dispose(self):events.append('disposed')
    def factory(url,**options):
        assert url==URL and options['isolation_level']=='REPEATABLE READ'
        assert options['connect_args']['options']=='-c default_transaction_read_only=on'
        assert options['connect_args']['hostaddr']=='127.0.0.1'
        return Engine()
    monkeypatch.setattr(m,'create_engine',factory)
    monkeypatch.setattr(m,'inspect',lambda *args:pytest.fail('unverified database inspected'))
    return events


def test_identity_mismatch_stops_before_any_table_inspection(tmp_path,monkeypatch):
    events=install_fake_engine(monkeypatch,identity(oid=999))
    refuses(m.Code.IDENTITY,lambda:m.snapshot_owned_restore(contract(),tmp_path.resolve()))
    assert events[0]=='SET TRANSACTION READ ONLY' and events[-1]=='disposed' and len(events)==3


def test_database_exception_text_is_redacted_and_engine_disposed(tmp_path,monkeypatch):
    events=install_fake_engine(monkeypatch,identity(),error=RuntimeError(URL+SECRET))
    refuses(m.Code.DATABASE,lambda:m.snapshot_owned_restore(contract(),tmp_path.resolve()))
    assert events[-1]=='disposed'


def test_no_raw_url_snapshot_entry_point(tmp_path):
    refuses(m.Code.CONTRACT,lambda:m.snapshot_owned_restore(URL,tmp_path.resolve()))


@pytest.mark.parametrize('unsupported', ['views', 'materialized_views', 'sequences', 'other_schema'])
def test_snapshot_rejects_uncovered_database_objects_after_identity(tmp_path, monkeypatch, unsupported):
    events=install_fake_engine(monkeypatch,identity())
    class Inspector:
        def get_schema_names(self):return ['public','extra'] if unsupported=='other_schema' else ['public']
        def get_view_names(self,**kw):return ['v'] if unsupported=='views' else []
        def get_materialized_view_names(self,**kw):return ['v'] if unsupported=='materialized_views' else []
        def get_sequence_names(self,**kw):return ['s'] if unsupported=='sequences' else []
        def get_table_names(self,**kw):return ['private_table']
    monkeypatch.setattr(m,'inspect',lambda *a:Inspector())
    refuses(m.Code.UNSUPPORTED,lambda:m.snapshot_owned_restore(contract(),tmp_path.resolve()))
    assert events[-1]=='disposed'


def test_positive_snapshot_enters_readonly_transaction_and_preserves_duplicate_rows(tmp_path,monkeypatch):
    events=[]
    class Result(FakeResult):
        def __iter__(self):return iter(self.value)
    class Preparer:
        def quote(self,name):assert name=='records';return '"records"'
    class Dialect:identifier_preparer=Preparer()
    class Connection:
        dialect=Dialect()
        def __enter__(self):return self
        def __exit__(self,*a):pass
        @contextmanager
        def begin(self):events.append('begin');yield
        def execute(self,sql,params=None):
            sql=str(sql);events.append(sql)
            if sql=='SET TRANSACTION READ ONLY':return Result(None)
            if sql.startswith('/* m60_untracked_catalog_guard */'):return Result(False)
            if 'FROM pg_catalog.pg_database' in sql:return Result(identity())
            if sql.startswith('/* m60_relation_properties */'):
                assert params=={'table_name':'records'}
                return Result({'properties':{'owner':'fixture','persistence':'p','replica_identity':'d',
                    'tablespace':'pg_default','options':[],'privileges':[],'columns':[]}})
            assert sql=='SELECT * FROM public."records"'
            return Result([{'value':Decimal('2.30')},{'value':Decimal('2.30')}])
    class Engine:
        def connect(self):return Connection()
        def dispose(self):events.append('disposed')
    class Inspector:
        def get_schema_names(self):return ['information_schema','pg_catalog','public']
        def get_view_names(self,**kw):return []
        def get_materialized_view_names(self,**kw):return []
        def get_sequence_names(self,**kw):return []
        def get_table_names(self,**kw):return ['records']
        def get_columns(self,*a,**kw):return [{'name':'value','type':'NUMERIC','nullable':False}]
        def get_pk_constraint(self,*a,**kw):return {'constrained_columns':[]}
        def get_foreign_keys(self,*a,**kw):return []
        get_unique_constraints=get_foreign_keys
        get_check_constraints=get_foreign_keys
        get_indexes=get_foreign_keys
    monkeypatch.setattr(m,'create_engine',lambda *a,**kw:Engine())
    monkeypatch.setattr(m,'inspect',lambda *a:Inspector())
    (tmp_path/'original').write_bytes(b'original-bytes')
    result=m.snapshot_owned_restore(contract(),tmp_path.resolve())
    assert len(result['tables']['records']['row_hashes'])==2
    assert result['tables']['records']['row_hashes'][0]==result['tables']['records']['row_hashes'][1]
    assert events[0:2]==['begin','SET TRANSACTION READ ONLY'] and events[-1]=='disposed'
    assert m.public_summary(m.compare_inventories(result,result))['old_record_count']==2
