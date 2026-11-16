"""Immutable local-review submissions; private inputs are not public audit data."""
from sqlalchemy import ForeignKey,Integer,String,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .models import Base,Row,Scoped,Payload

class LocalReviewSubmissionV261(Row,Scoped,Payload,Base):
    __tablename__='local_proposal_review_submissions_v261'
    __table_args__=(UniqueConstraint('workspace_id','owner_id','request_id'),
        UniqueConstraint('saved_key'),UniqueConstraint('derived_analysis_id'))
    owner_id:Mapped[str]=mapped_column(ForeignKey('users.id'),index=True)
    draft_id:Mapped[str]=mapped_column(ForeignKey('local_proposal_review_drafts_v261.id'),index=True)
    draft_version:Mapped[int]=mapped_column(Integer)
    source_id:Mapped[str]=mapped_column(ForeignKey('setup_analyses.id'),index=True)
    status:Mapped[str]=mapped_column(String(20))
    saved_key:Mapped[str|None]=mapped_column(String(36),nullable=True)
    derived_analysis_id:Mapped[str|None]=mapped_column(ForeignKey('setup_analyses.id'),index=True,nullable=True)
    request_id:Mapped[str]=mapped_column(String(36))
