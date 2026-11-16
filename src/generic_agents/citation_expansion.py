"""Expand only unambiguous omitted spans in the declared source block.

This does not make inferred or contradictory content valid evidence. The regular
schema, exact-quote and field/unit checks must still run after this pure step.
The paid wire answer is never mutated.
"""
import copy
import re
from .contracts import digest

POLICY = 'same-block-unique-ellipsis-v1'
ELLIPSIS = re.compile(r'\u2026+|\.{3,}')


def expand_citations(value, blocks):
    sources = {}
    for block in blocks:
        key = (block['material_id'], block['block_id'])
        # No source variant may be chosen merely because it fits the answer.
        sources[key] = None if key in sources else block
    audit = []

    def citation(item, path):
        result = copy.deepcopy(item)
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ('material_id', 'block_id', 'quote')):
            return result
        block = sources.get((item['material_id'], item['block_id']))
        quote = item['quote']
        if block is None or not isinstance(block.get('text'), str) or not 1 <= len(quote) <= 250:
            return result
        text = block['text']
        if quote in text:
            return result
        parts = ELLIPSIS.split(quote)
        if len(parts) < 2 or any(len(part) < 4 for part in parts):
            return result
        positions = []
        previous_end = -1
        for part in parts:
            start = text.find(part)
            # Start+1 detects overlapping alternative matches as well.
            if start < 0 or text.find(part, start + 1) != -1 or start < previous_end:
                return result
            previous_end = start + len(part)
            positions.append((start, previous_end))
        expanded = text[positions[0][0]:positions[-1][1]]
        if len(expanded) > 250:
            return result
        result['quote'] = expanded
        audit.append({'policy': POLICY, 'path': path,
                      'material_id': item['material_id'], 'block_id': item['block_id'],
                      'original_quote': quote, 'expanded_quote': expanded,
                      'source_block_hash': digest(block)})
        return result

    def walk(item, path=''):
        if isinstance(item, dict):
            out = {}
            for key, child in item.items():
                pointer = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
                if key == 'citations' and isinstance(child, list):
                    out[key] = [citation(ref, pointer + '/' + str(i)) for i, ref in enumerate(child)]
                else:
                    out[key] = walk(child, pointer)
            return out
        if isinstance(item, list):
            return [walk(child, path + '/' + str(i)) for i, child in enumerate(item)]
        return copy.deepcopy(item)

    return walk(value), audit
