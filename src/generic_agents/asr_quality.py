"""Explicit, reproducible ASR metrics; no model or domain acceptance implied."""
import unicodedata

NORMALIZATION = {
    'version': 'asr-normalization-v1',
    'unicode': 'NFKC_casefold',
    'words': 'Unicode letters/numbers; internal straight apostrophes retained; other punctuation separates',
    'characters': 'Unicode letters/numbers only; whitespace/punctuation excluded',
    'numbers': 'no conversion between spoken words and digits',
    'aggregation': 'micro average of edit counts / reference units; WER/CER may exceed 1',
}


def normalized(text):
    if not isinstance(text, str):
        raise ValueError('ASR text must be a string')
    value = unicodedata.normalize('NFKC', text).casefold().replace('’', "'")
    letter = lambda c: unicodedata.category(c)[0] in {'L', 'N'}
    chars = [c for c in value if letter(c)]
    word_text = ''.join(c if letter(c) or (
        c == "'" and i > 0 and i + 1 < len(value)
        and letter(value[i - 1]) and letter(value[i + 1])
    ) else ' ' for i, c in enumerate(value))
    return {'words': word_text.split(), 'characters': chars}


def edits(reference, hypothesis):
    """Levenshtein S/D/I with rolling rows; ties prefer diagonal, delete, insert."""
    if not reference:
        raise ValueError('Empty reference is not a scored transcription case')
    if max(len(reference), len(hypothesis)) > 12000:
        raise ValueError('ASR score exceeds bounded token count')
    # Each cell: total distance, substitutions, deletions, insertions.
    previous = [(i, 0, 0, i) for i in range(len(hypothesis) + 1)]
    for i, expected in enumerate(reference, 1):
        current = [(i, 0, i, 0)]
        for j, actual in enumerate(hypothesis, 1):
            v = previous[j - 1]
            mismatch = int(expected != actual)
            diagonal = (v[0] + mismatch, v[1] + mismatch, v[2], v[3])
            v = previous[j]
            deletion = (v[0] + 1, v[1], v[2] + 1, v[3])
            v = current[j - 1]
            insertion = (v[0] + 1, v[1], v[2], v[3] + 1)
            current.append(min((diagonal, deletion, insertion), key=lambda x: x[0]))
        previous = current
    distance, substitutions, deletions, insertions = previous[-1]
    return {'reference_units': len(reference), 'hypothesis_units': len(hypothesis),
            'substitutions': substitutions, 'deletions': deletions,
            'insertions': insertions, 'errors': distance, 'rate': distance / len(reference)}


def score(reference_text, hypothesis_text):
    reference, hypothesis = normalized(reference_text), normalized(hypothesis_text)
    return {'reference': reference, 'hypothesis': hypothesis,
            'wer': edits(reference['words'], hypothesis['words']),
            'cer': edits(reference['characters'], hypothesis['characters'])}


def aggregate(scores):
    if not scores:
        raise ValueError('No scored cases; cannot report a quality score')
    result = {}
    for key in ('wer', 'cer'):
        total = {field: sum(s[key][field] for s in scores) for field in
                 ('reference_units', 'hypothesis_units', 'substitutions', 'deletions', 'insertions', 'errors')}
        if total['reference_units'] <= 0 or total['errors'] != sum(total[f] for f in ('substitutions', 'deletions', 'insertions')):
            raise ValueError('Invalid ASR score totals')
        total['rate'] = total['errors'] / total['reference_units']
        result[key] = total
    return {'cases': len(scores), **result}
