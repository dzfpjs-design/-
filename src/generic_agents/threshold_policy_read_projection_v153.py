"""One read assembly only; these pins never authorize execution or a command."""
import hashlib
from .typed_payload_codec_v131 import canonical_json


def _typed_snapshot(row,fields):
    return canonical_json({key:getattr(row,key) for key in fields})


class PolicyReadProjection:
    def __init__(self,db,p,grant,rows,*,_read_inventory=None):
        from . import threshold_policy_store_v153 as store
        from . import threshold_policy_execution_v153 as execution
        self.db=db;self.transaction=db.get_transaction();self.identity=self._identity(p)
        self.grant=grant;self.inventory=_read_inventory
        self.grant_pin=_typed_snapshot(grant,store.SNAPSHOT_KEYS) if self.inventory is None else None
        self.grant_hash=grant.record_hash
        self.rows=tuple(rows);self.row_ids=frozenset(row.id for row in self.rows)
        self.pins=({row.id:(_typed_snapshot(row,execution.FIELDS),row.record_hash) for row in self.rows}
            if self.inventory is None else {})
        self.candidate_ok=None;self.baseline_ok=None
        store.need(len(self.row_ids)==len(self.rows))
        self.check(db,p,grant)

    @staticmethod
    def _identity(p):return (p.user_id,p.workspace_id,p.role,p.session_id)

    def check(self,db,p,grant,row=None):
        from . import threshold_policy_store_v153 as store
        from . import threshold_policy_execution_v153 as execution
        store.need(db is self.db and self.transaction is not None and self.transaction.is_active
            and db.get_transaction() is self.transaction and self._identity(p)==self.identity and grant is self.grant,
            '读取期间策略或身份已变化，请重新读取；显示核对不能用于执行')
        if self.inventory is not None:
            from .threshold_policy_read_inventory_v155 import checked_read_inventory
            store.need(row is None or row.id in self.row_ids)
            checked_read_inventory(self.inventory,db,p,row=row)
            return self
        actual=_typed_snapshot(grant,store.SNAPSHOT_KEYS)
        store.need(actual==self.grant_pin and grant.record_hash==self.grant_hash
            ==hashlib.sha256(actual.encode('utf-8')).hexdigest(),
            '读取期间策略或身份已变化，请重新读取；显示核对不能用于执行')
        for actual in self.rows if row is None else (row,):
            encoded=_typed_snapshot(actual,execution.FIELDS)
            store.need(actual.workspace_id==p.workspace_id and actual.policy_id==grant.id
                and self.pins.get(actual.id)==(encoded,actual.record_hash)
                and actual.record_hash==hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
                '读取期间原输入已变化，请重新读取；原记录保留')
        return self

    def binding(self,db,p,grant,row):
        from . import threshold_policy_store_v153 as store
        store.need(self.inventory is not None and db is self.db and self.transaction is not None
            and self.transaction.is_active and db.get_transaction() is self.transaction
            and self._identity(p)==self.identity and grant is self.grant and row.id in self.row_ids)
        return self.inventory.binding(db,p,grant,row)

    def finish(self,db,p,grant,candidate_ok,baseline_ok):
        from . import threshold_policy_store_v153 as store
        self.check(db,p,grant)
        store.need(self.candidate_ok is None and type(candidate_ok) is bool and type(baseline_ok) is bool)
        self.candidate_ok=candidate_ok;self.baseline_ok=baseline_ok
        return self

    def readable(self,db,p,grant,choice,row=None):
        from . import threshold_policy_store_v153 as store
        self.check(db,p,grant,row)
        store.need(choice in {'candidate','baseline'} and type(self.candidate_ok) is bool and type(self.baseline_ok) is bool)
        return self.candidate_ok if choice=='candidate' else self.baseline_ok
