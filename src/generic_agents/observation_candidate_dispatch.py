"""Typed candidate origins retain their own proof and never become facts."""
from .models import Capability
from .services import scoped


def is_audio(db,p,row,extractor_id=None):
    from .audio_observation_model import observation_marker,ADAPTER
    marked=observation_marker(db,p,row)
    identity=extractor_id or row.data.get('extractor_id')
    typed=bool(identity and scoped(db,Capability,identity,p).data.get('execution',{}).get('adapter')==ADAPTER)
    return bool(marked or typed)


def verify_candidate(db,p,row,extractor_id,*,execution=True):
    audio=is_audio(db,p,row,extractor_id)
    if audio:
        from .audio_observation_model import verify_observation
        verify_observation(db,p,row,extractor_id,execution=execution)
    elif row.data.get('candidate_only'):
        from .local_observations import verify
        verify(db,p,row,extractor_id,execution=execution)
    return audio
