"""Read-only discovery of domain-owned queries through the generic query API.

Domain markers and independent Task/Job/Audit links only restrict access. They
never authorize execution or turn a frozen domain result into shared evidence.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from .models import Audit, DataGap, DataRun, GapCheck, Job, Task

SCHEMA = 'data-query-reference-v144'
TASK_MARKERS = ('native_task_query_contract', 'native_query_contract', 'native_goal_task_id')
TASK_HINTS = (*TASK_MARKERS, 'native_task_query_contract_hash', 'native_query_contract_hash',
    'native_goal_context_hash', 'native_goal_request_hash', 'native_query_job_id')
TASK_JOBS = ('execute_native_task_query', 'execute_native_goal_query')
TASK_AUDITS = {
    'native_task_query.command': ('run_id',),
    'native_task_query.completed': ('query_id',),
    'native_goal.data_delegated': ('query_id', 'previous_query_id'),
    'native_goal.data_completed': ('query_id',),
}
UNVERIFIED = '原查询的范围或依据暂时无法核对；原记录保留，请回原实例或原任务核查。'
_SHAPE_ERRORS = (ValueError, KeyError, TypeError, AttributeError, RecursionError)
MAX_LINEAGE_ROWS = 1000
MAX_LINEAGE_DEPTH = 32


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _unavailable():
    return HTTPException(409, UNVERIFIED)


def _native_task(task):
    return type(task.data) is dict and any(key in task.data for key in
        ('native_goal_context', 'native_goal_context_hash', 'native_goal_query_id'))


class QueryVisibility:
    """Batch the independent classification inventory for at most one API page."""

    def __init__(self, db, p, rows):
        from .native_candidate_queries_v139 import INTENT, SOURCES, OUTCOME
        self.db, self.p = db, p
        self.parents, self.invalid, self.gap_tasks = {}, set(), {}
        self.rows = self._lineage(list(rows))
        self.candidate = set()
        self.native = set()
        self.links = {identity: set() for identity in self.rows}
        self.saved_tasks = {}
        identities = list(self.rows)
        if not identities:
            return
        delegated = {}
        for row in self.rows.values():
            if type(row.data) is not dict:
                raise _unavailable()
            if 'native_candidate_query_v139' in row.data or 'native_candidate_query_v139_hash' in row.data:
                self.candidate.add(row.id)
            if any(key in row.data for key in TASK_HINTS):
                self.native.add(row.id)
            value = row.data.get('goal_delegation')
            if type(value) is dict and _uuid(value.get('task_id')):
                delegated.setdefault(value['task_id'], set()).add(row.id)

        candidate_actions = (INTENT, SOURCES, OUTCOME)
        conditions = [and_(Audit.action.in_(candidate_actions), Audit.object_id.in_(identities))]
        conditions.extend(and_(Audit.action == action,
            or_(*(Audit.data[field].as_string().in_(identities) for field in fields)))
            for action, fields in TASK_AUDITS.items())
        entries = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            or_(*conditions))))
        for entry in entries:
            if entry.action in candidate_actions:
                self.candidate.add(entry.object_id)
            else:
                for field in TASK_AUDITS[entry.action]:
                    identity = entry.data.get(field)
                    if type(identity) is str and identity in self.rows:
                        self._link(identity, entry.object_id)

        jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.data['query_id'].as_string().in_(identities),
            Job.kind.in_((*TASK_JOBS, 'data_query')))))
        for job in jobs:
            identity = job.data['query_id']
            if job.kind in TASK_JOBS:
                self._link(identity, job.data.get('task_id'))
            elif job.idempotency_key.startswith('native-candidate-query-v139:'):
                self.candidate.add(identity)

        # Source-evidence native Goals do not have a per-query Job. Their actual
        # original Task pointer is an independent anchor even if row markers are
        # removed. Older derived generations also retain Task history links.
        task_hints = set(delegated)
        for values in self.gap_tasks.values():
            task_hints.update(value for value in values if _uuid(value))
        tasks = list(db.scalars(select(Task).where(Task.workspace_id == p.workspace_id,
            or_(Task.data['native_goal_query_id'].as_string().in_(identities),
                Task.data['data_query_run_id'].as_string().in_(identities),
                Task.id.in_(task_hints),
                *(Task.data['data_query_history'].contains([{'query_id': identity}])
                    for identity in identities)))))
        for task in tasks:
            if not _native_task(task):
                continue
            # A caller-supplied delegation alone may restrict a query, but
            # cannot grant a Task reference. Actual parent/Gap links below must
            # independently establish that association.
            self.native.update(delegated.get(task.id, ()))
            for identity, values in self.gap_tasks.items():
                if identity in self.rows and task.id in values:
                    self._link(identity, task.id)
            linked = [task.data.get('native_goal_query_id'), task.data.get('data_query_run_id')]
            history = task.data.get('data_query_history', [])
            if type(history) is list:
                linked.extend(value.get('query_id') for value in history if type(value) is dict)
            for identity in linked:
                if type(identity) is str and identity in self.rows:
                    self._link(identity, task.id)
        self._inherit()

    def _lineage(self, rows):
        """Follow actual scoped parents and independent Gap recheck inventory.

        Pending/latest Gap pointers cover a child whose own markers were lost;
        GapCheck's real gap_id preserves earlier recheck generations. Limits
        fail closed, never silently treat an unresolved ancestor as generic.
        """
        known = {row.id: row for row in rows}
        pending = set(known)
        while pending:
            if len(known) > MAX_LINEAGE_ROWS:
                raise _unavailable()
            explicit = {}
            wanted = set()
            for identity in pending:
                row = known[identity]
                if type(row.data) is not dict:
                    raise _unavailable()
                self.parents.setdefault(identity, set())
                if 'parent_query_id' in row.data:
                    parent = row.data['parent_query_id']
                    if not _uuid(parent):
                        self.invalid.add(identity)
                    else:
                        self.parents[identity].add(parent); wanted.add(parent)
                if 'gap_recheck' in row.data:
                    value = row.data['gap_recheck']
                    if type(value) is not dict or not _uuid(value.get('gap_id')):
                        self.invalid.add(identity)
                    else:
                        explicit.setdefault(value['gap_id'], set()).add(identity)

            checks = list(self.db.scalars(select(GapCheck).where(
                GapCheck.workspace_id == self.p.workspace_id,
                GapCheck.data['query_id'].as_string().in_(pending)).limit(MAX_LINEAGE_ROWS + 1)))
            if len(checks) > MAX_LINEAGE_ROWS:
                raise _unavailable()
            checked = {}
            for check in checks:
                checked.setdefault(check.gap_id, set()).add(check.data['query_id'])
            gaps = list(self.db.scalars(select(DataGap).where(DataGap.workspace_id == self.p.workspace_id,
                or_(DataGap.query_id.in_(pending),
                    DataGap.data['pending_recheck_query_id'].as_string().in_(pending),
                    DataGap.data['latest_recheck_query_id'].as_string().in_(pending),
                    DataGap.id.in_(set(explicit) | set(checked)))).limit(MAX_LINEAGE_ROWS + 1)))
            if len(gaps) > MAX_LINEAGE_ROWS:
                raise _unavailable()
            actual = {gap.id: gap for gap in gaps}
            for gap_id in set(explicit) | set(checked):
                if gap_id not in actual:
                    self.invalid.update(explicit.get(gap_id, set()) | checked.get(gap_id, set()))
            for gap in gaps:
                if type(gap.data) is not dict or not _uuid(gap.query_id):
                    raise _unavailable()
                children = set(checked.get(gap.id, ()))
                children.update(value for value in (
                    gap.data.get('pending_recheck_query_id'), gap.data.get('latest_recheck_query_id'))
                    if type(value) is str and value in pending)
                # A row's own gap_id alone is not an independent back-reference.
                self.invalid.update(explicit.get(gap.id, set()) - children)
                for identity in children:
                    declared = self.parents.setdefault(identity, set())
                    if declared and declared != {gap.query_id}:
                        self.invalid.add(identity)
                    declared.add(gap.query_id); wanted.add(gap.query_id)
                if gap.query_id in pending or children:
                    if 'business_task_id' in gap.data:
                        task_id = gap.data['business_task_id']
                        if not _uuid(task_id):
                            self.invalid.add(gap.query_id)
                        else:
                            self.gap_tasks.setdefault(gap.query_id, set()).add(task_id)
            missing = wanted - set(known)
            loaded = list(self.db.scalars(select(DataRun).where(
                DataRun.workspace_id == self.p.workspace_id, DataRun.id.in_(missing)))) if missing else []
            new = {row.id: row for row in loaded}
            absent = missing - set(new)
            for identity, parents in self.parents.items():
                if parents & absent:
                    self.invalid.add(identity)
            known.update(new)
            pending = set(new)
        return known

    def _inherit(self):
        finished = set()

        def visit(identity, trail):
            if identity in finished:
                return
            if identity in trail or len(trail) >= MAX_LINEAGE_DEPTH or identity in self.invalid:
                self.native.add(identity); self.links[identity].add(None)
                return
            for parent in self.parents.get(identity, ()):
                if parent not in self.rows:
                    self.native.add(identity); self.links[identity].add(None)
                    continue
                visit(parent, trail | {identity})
                if parent in self.candidate:
                    self.candidate.add(identity)
                if parent in self.native:
                    self.native.add(identity); self.links[identity].update(self.links[parent])
            finished.add(identity)

        for identity in self.rows:
            visit(identity, set())

    def _link(self, identity, task_id):
        self.native.add(identity)
        # Keep malformed links as an explicit conflict; never drop a broken
        # domain anchor and then return the generic raw DataRun.
        self.links[identity].add(task_id if _uuid(task_id) else None)

    def family(self, row):
        from .goal_evaluation_query_guard_v288 import has_trace as goal_evaluation_trace
        if goal_evaluation_trace(self.db, self.p, row):return 'goal_evaluation_v288'
        from .multimodal_question_service_v262 import has_trace as multimodal_question_trace
        if multimodal_question_trace(self.db,self.p,row.id):return 'multimodal_query_v262'
        for parent in self.parents.get(row.id,()):
            if multimodal_question_trace(self.db,self.p,parent):return 'multimodal_query_v262'
        from .pixel_question_service_v252 import has_trace as pixel_question_trace
        if pixel_question_trace(self.db,self.p,row.id):return 'pixel_question_v252'
        # A derived generic child cannot strip the original family boundary.
        for parent in self.parents.get(row.id,()):
            if pixel_question_trace(self.db,self.p,parent):return 'pixel_question_v252'
        from .source_query_service import is_v2
        if is_v2(self.db, row):
            return 'source_query_v2'
        if row.id in self.candidate:
            return 'native_candidate'
        return 'native_task' if row.id in self.native else None

    def _reference(self, row, family, instance_id, task_id, notice):
        if not _uuid(row.id):
            raise _unavailable()
        return {'schema': SCHEMA, 'id': row.id, 'version': row.version,
            'status': row.status, 'question': row.question, 'created_at': row.created_at.isoformat(),
            'reference_only': True, 'family': family,
            'context': {'instance_id': instance_id, 'task_id': task_id,
                'detail_path': '/api/data/queries/' + row.id}, 'notice': notice}

    def _candidate_reference(self, row):
        if row.owner_id != self.p.user_id:
            raise HTTPException(404, '当前账号没有这份原候选查询')
        from .native_candidate_queries_v139 import INTENT, _cold
        entries = list(self.db.scalars(select(Audit).where(Audit.workspace_id == self.p.workspace_id,
            Audit.action == INTENT, Audit.object_id == row.id).limit(2)))
        instance_id = None
        try:
            if len(entries) != 1:
                raise _unavailable()
            entry = entries[0]
            value = entry.data
            if (type(value) is not dict or not _uuid(value.get('instance_id'))
                or value.get('query_id') != row.id or value.get('actor_id') != row.owner_id
                or entry.actor_id != row.owner_id):
                raise _unavailable()
            # The original dedicated verifier checks the actual scoped instance,
            # release, request, source Audit and registered keyword pins. No GET
            # repairs or re-executes the query.
            _cold(self.db, self.p, row, value['instance_id'])
            instance_id = value['instance_id']
        except (HTTPException, *_SHAPE_ERRORS):
            pass
        return self._reference(row, 'native_candidate', instance_id, None,
            '这是待核查候选的原查询；请在原实例核对候选及人工核查情况，候选不代表对象事实。'
            if instance_id else UNVERIFIED)

    def _task_reference(self, row):
        linked = set(self.links[row.id])
        declared = []
        for key in ('native_task_query_contract', 'native_query_contract'):
            value = row.data.get(key)
            if type(value) is dict and 'task_id' in value:
                declared.append(value['task_id'])
        if 'native_goal_task_id' in row.data:
            declared.append(row.data['native_goal_task_id'])
        delegation = row.data.get('goal_delegation')
        if type(delegation) is dict and 'task_id' in delegation:
            declared.append(delegation['task_id'])
        if (len(linked) != 1 or None in linked
            or any(not _uuid(value) or value not in linked for value in declared)):
            raise _unavailable()
        task_id = next(iter(linked))
        if task_id not in self.saved_tasks:
            # Same historical read authority as the actual workspace Task GET,
            # not the stricter original-owner/admin mutation authority.
            from .workspace_api import saved_execution
            self.saved_tasks[task_id] = saved_execution(self.db, self.p, task_id)
        task, release, _ = self.saved_tasks[task_id]
        if (task.id != task_id or task.workspace_id != row.workspace_id
            or not _uuid(release.instance_id)):
            raise _unavailable()
        return self._reference(row, 'native_task', release.instance_id, task.id,
            '这是原业务任务的查询记录；请在原任务核对结果和来源，通用查询不改写此记录。')

    def reference(self, row):
        try:
            family = self.family(row)
            if family == 'goal_evaluation_v288':
                from .goal_evaluation_query_guard_v288 import visible
                return visible(self.db, self.p, row)
            if family == 'multimodal_query_v262':
                from .multimodal_question_service_v262 import safe_reference
                return safe_reference(self.db,self.p,row)
            if family == 'pixel_question_v252':
                from .pixel_question_service_v252 import safe_reference
                return safe_reference(self.db,self.p,row)
            if family == 'source_query_v2':
                from .source_query_service import safe_reference
                return safe_reference(self.db, self.p, row)
            if family == 'native_candidate':
                return self._candidate_reference(row)
            return self._task_reference(row)
        except _SHAPE_ERRORS:
            raise _unavailable()

    def detail(self, row):
        reference = self.reference(row)
        if reference['family'] == 'native_candidate' and reference['context']['instance_id']:
            from .native_candidate_queries_v139 import detail
            return detail(self.db, self.p, reference['context']['instance_id'], row.id)
        return reference


def reject_generic_write(db, p, row):
    from .goal_evaluation_query_guard_v288 import reject_generic_write as reject_evaluation_write
    reject_evaluation_write(db, p, row)
    family = QueryVisibility(db, p, [row]).family(row)
    if family == 'source_query_v2':
        raise HTTPException(409, '请通过原V2资料查询管理取消和解释；V1不能改写来源范围或原答案')
    if family:
        raise HTTPException(409, '原候选或任务查询请在原实例、原业务任务中管理；不能通过通用查询改写、派生、核查或取消')
