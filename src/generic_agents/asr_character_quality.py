"""Character-only ASR scoring; does not claim Chinese lexical segmentation."""
from .asr_quality import NORMALIZATION, normalized, edits

CHARACTER_PROTOCOL = {
    'version': 'asr-character-quality-v1',
    'normalization': {k: v for k, v in NORMALIZATION.items() if k != 'words'},
    'primary_metric': 'CER: NFKC/casefold Unicode letters/numbers; no word segmentation',
    'reference': 'original frozen author transcription; no corrections using model output',
    'acceptance': 'not a domain acceptance, model selection or production gate',
}


def character_score(reference_text, hypothesis_text):
    reference = normalized(reference_text)['characters']
    hypothesis = normalized(hypothesis_text)['characters']
    return {'reference_characters': reference, 'hypothesis_characters': hypothesis,
            'cer': edits(reference, hypothesis)}


def aggregate_characters(scores):
    if not scores:
        raise ValueError('No scored character cases')
    total = {key: sum(s['cer'][key] for s in scores) for key in (
        'reference_units', 'hypothesis_units', 'substitutions', 'deletions', 'insertions', 'errors')}
    if total['reference_units'] <= 0 or total['errors'] != sum(total[k] for k in ('substitutions', 'deletions', 'insertions')):
        raise ValueError('Invalid character score totals')
    total['rate'] = total['errors'] / total['reference_units']
    return {'cases': len(scores), 'cer': total}
