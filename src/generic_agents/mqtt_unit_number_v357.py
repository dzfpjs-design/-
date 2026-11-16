"""Read the original JSON numeric lexeme; never reconstruct it from float."""
import json
from fastapi import HTTPException
from .mqtt_messages import _no_duplicate_keys

class OriginalNumber:
    def __init__(self,lexeme):self.lexeme=lexeme


def original_number(raw,values_key,field):
    try:
        value=json.loads(raw.decode('utf-8'),object_pairs_hook=_no_duplicate_keys,
            parse_int=OriginalNumber,parse_float=OriginalNumber,
            parse_constant=lambda _x:(_ for _ in ()).throw(ValueError('non-finite')))
        item=value[values_key][field]
        if not isinstance(item,OriginalNumber):raise ValueError()
        return item.lexeme
    except (KeyError,TypeError,ValueError,UnicodeError,RecursionError):
        raise HTTPException(409,'原始数值无法核对；原报文保留') from None
