"""Internal, one-shot bridge from a fenced worker to the delegated gateway.

This bridge cannot create a reservation. It only hands an already committed,
stage-bound call through the transport admission code. The production dispatch
gate is independent and remains closed until the complete path is approved.
"""
from __future__ import annotations
from threading import Lock

from .contracts import digest
from .leases import fenced, ownership
from .security import Principal


class PreboundGatewayDenied(ValueError):
    """The already reserved call is not safe to send or retry."""


class PreboundGatewayCall:
    def __init__(self, db, principal, capability_id, stage_id, call_id):
        if (not fenced(db) or type(principal) is not Principal
                or db.info.get('worker_actor') != principal
                or not ownership(db).get('job_id')
                or any(type(value) is not str or not value for value in
                       (capability_id, stage_id, call_id))):
            raise PreboundGatewayDenied('受托调用缺少可信后台任务或原预留')
        self._db = db
        self._principal = principal
        self._capability_id = capability_id
        self._stage_id = stage_id
        self.call_id = call_id
        self._used = False
        self._lock = Lock()

    def acquire(self, principal, capability_id, messages, options):
        """Create one sink and one committed intent, then consume its witness.

        Failure never releases the existing reservation and never grants a
        second attempt. A committed intent is conservatively an unknown send.
        """
        with self._lock:
            if self._used:
                raise PreboundGatewayDenied('原调用交接已使用，不会再次发送')
            self._used = True
        if (principal != self._principal or capability_id != self._capability_id
                or not fenced(self._db) or self._db.info.get('worker_actor') != principal):
            raise PreboundGatewayDenied('原调用身份已变化，不会发送')
        if self._stage_id == 'reduce':
            from .report_evidence_reduce_transport import prepare_prebound_capture, commit_transport_intent
        else:
            from .report_evidence_prebound_transport import prepare_prebound_capture, commit_transport_intent
        sink = prepare_prebound_capture(self._db, principal, self._stage_id, self.call_id, messages, options)
        try:
            witness = commit_transport_intent(self._db, principal, self._stage_id, self.call_id,
                                              messages, options, sink)
            result = witness.consume(principal, capability_id, self._stage_id, self.call_id,
                                     messages, options, sink)
            if (result.get('call_id') != self.call_id or result.get('sink') is not sink
                    or digest(result.get('messages')) != digest(messages)
                    or digest(result.get('options')) != digest(options)):
                raise PreboundGatewayDenied('原调用交接范围不一致，不会发送')
            return result
        except BaseException:
            sink.close()
            raise
