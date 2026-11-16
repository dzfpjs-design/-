"""Real native document bytes, no database, model, HTTP or external files."""
import io
import json
import zipfile
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from generic_agents import business_data_reader as reader


def parsed(raw, kind='json'):
    return reader.parse_dataset(raw, 'source.' + kind, kind)


def test_geojson_keeps_values_pointer_identity_and_exact_large_integer():
    value = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'id': 'us-1',
        'properties': {'mag': 0, 'reviewed': False, 'place': None, 'number': 9007199254740993},
        'geometry': {'coordinates': [117.5, 24.5, 0]}}]}
    result = parsed(json.dumps(value).encode())
    assert result['engine'] == 'native-json-v204'
    dataset = result['record_sets'][0]
    assert dataset['key'] == '/features' and dataset['count'] == 1
    record = dataset['records'][0]
    assert record['location'] == {'pointer': '/features/0'}
    assert record['value'] == value['features'][0]
    assert type(record['value']['properties']['mag']) is int
    assert record['value']['properties']['reviewed'] is False
    assert record['value']['properties']['place'] is None
    assert record['value']['properties']['number'] == 9007199254740993
    fields = {field['path']: field for field in dataset['fields']}
    assert fields['/properties/mag']['types'] == ['integer']
    assert fields['/geometry/coordinates']['types'] == ['array']


def test_pointer_escaping_empty_sets_and_root_record_are_distinct():
    root = parsed(b'{"a/b":{"~name":false}}')['record_sets'][0]
    assert root['key'] == '' and root['records'][0]['location'] == {'pointer': ''}
    assert '/a~1b/~0name' in {field['path'] for field in root['fields']}
    empty = parsed(b'{"features":[]}')['record_sets'][0]
    assert empty['key'] == '/features' and empty['count'] == 0 and empty['records'] == []
    assert parsed(b'[]')['record_sets'][0]['count'] == 0


@pytest.mark.parametrize('raw', [b'{"id":1,"id":2}', b'[{"id":NaN}]',
    b'[{"id":Infinity}]', b'[{"id":1e999}]', b'[{"a":1},{"a":{"b":2}}]',
    b'{"rows":[{},1]}', b'42', b'{'])
def test_json_ambiguity_nonfinite_shapes_and_malformed_bytes_are_rejected(raw):
    with pytest.raises(reader.ReaderError): parsed(raw)


def test_csv_keeps_lexical_values_multiline_position_and_header_only_columns():
    result = parsed(b'identifier,a/b,flag,blank\r\n001,"line1\nline2",false,\r\n', 'csv')
    assert result['engine'] == 'native-csv-v204'
    dataset = result['record_sets'][0]
    record = dataset['records'][0]
    assert record['value'] == {'identifier': '001', 'a/b': 'line1\nline2', 'flag': 'false', 'blank': ''}
    assert record['location'] == {'row': 2, 'end_row': 3}
    assert '/a~1b' in {field['path'] for field in dataset['fields']}
    empty = parsed(b'id,value\n', 'csv')['record_sets'][0]
    assert empty['count'] == 0 and [field['path'] for field in empty['fields']] == ['/id', '/value']


@pytest.mark.parametrize('raw', [b'a,a\n1,2\n', b'a,\n1,2\n', b'a,b\n1\n',
    b'a\n1,2\n', b'a\n"not closed', b'\xff', b'a\n\x00'])
def test_csv_duplicate_headers_partial_rows_and_invalid_encoding_are_not_repaired(raw):
    with pytest.raises(reader.ReaderError): parsed(raw, 'csv')


def workbook_bytes():
    book = Workbook(); sheet = book.active; sheet.title = '原始资料'
    sheet.append(['id', 'zero', 'flag', 'nullable', 'formula', 'date'])
    sheet.append(['001', 0, False, None, '=SUM(1,2)', datetime(2026, 10, 25, 8, 30)])
    second = book.create_sheet('空清单'); second.append(['stable key', 'value'])
    stream = io.BytesIO(); book.save(stream); book.close(); return stream.getvalue()


def rewrite_zip(raw, replacements=None, extras=None):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, 'w') as target:
        for name in source.namelist(): target.writestr(name, (replacements or {}).get(name, source.read(name)))
        for name, value in (extras or {}).items(): target.writestr(name, value)
    return output.getvalue()


def test_native_xlsx_values_dates_formulas_and_empty_columns_remain_structured():
    result = parsed(workbook_bytes(), 'xlsx'); first, second = result['record_sets']
    assert result['engine'] == 'native-xlsx-v204-2'
    assert first['key'] == 'sheet:原始资料'
    row = first['records'][0]
    assert row['location'] == {'sheet': '原始资料', 'row': 2}
    assert row['value'] == {'id': '001', 'zero': 0, 'flag': False, 'nullable': None,
        'formula': '=SUM(1,2)', 'date': '2026-10-25T08:30:00'}
    assert any('公式' in warning for warning in result['warnings'])
    assert any('时区' in warning for warning in result['warnings'])
    assert second['count'] == 0 and len(second['fields']) == 2


@pytest.mark.parametrize('value,number_format,expected', [
    (date(2026, 10, 25), 'yyyy-mm-dd', '2026-10-25'),
    (datetime(2026, 10, 25), 'yyyy-mm-dd hh:mm:ss', '2026-10-25T00:00:00'),
    (datetime(2026, 10, 25, 8, 30), 'yyyy-mm-dd hh:mm:ss', '2026-10-25T08:30:00'),
    (datetime(2026, 10, 25, 0, 0, 1), 'yyyy-mm-dd', '2026-10-25T00:00:01'),
    (datetime(2026, 10, 25, 0, 0, 0, 123000), 'yyyy-mm-dd', '2026-10-25T00:00:00.123000'),
])
def test_xlsx_date_only_format_preserves_date_and_never_discards_hidden_time(value, number_format, expected):
    book = Workbook(); sheet = book.active; sheet.title = '日期资料'
    sheet.append(['id', 'observed_on']); sheet.append(['001', value])
    sheet['B2'].number_format = number_format
    stream = io.BytesIO(); book.save(stream); book.close()
    result = parsed(stream.getvalue(), 'xlsx')
    assert result['engine'] == 'native-xlsx-v204-2'
    record = result['record_sets'][0]['records'][0]
    assert record['location'] == {'sheet': '日期资料', 'row': 2}
    assert record['value'] == {'id': '001', 'observed_on': expected}
    assert any('未补充时区' in warning for warning in result['warnings'])


def test_xlsx_macro_and_repeated_cell_cannot_be_hidden_in_a_normal_extension():
    raw = workbook_bytes()
    with pytest.raises(reader.ReaderError):
        parsed(rewrite_zip(raw, extras={'xl/vbaProject.bin': b'not executed'}), 'xlsx')
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        sheet = source.read('xl/worksheets/sheet1.xml')
    duplicated = sheet.replace(b'</row>', b'<c r="A1" t="n"><v>9</v></c></row>', 1)
    with pytest.raises(reader.ReaderError):
        parsed(rewrite_zip(raw, {'xl/worksheets/sheet1.xml': duplicated}), 'xlsx')
    with pytest.raises(reader.ReaderError):
        parsed(rewrite_zip(raw, extras={'xl/sharedStrings.xml':
            b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///must-not-read">]><a>&x;</a>'}), 'xlsx')


def test_limits_fail_instead_of_returning_first_records_or_truncated_content(monkeypatch):
    monkeypatch.setattr(reader, 'MAX_RECORDS', 1)
    with pytest.raises(reader.ReaderError): parsed(b'[{"id":"a"},{"id":"b"}]')
    monkeypatch.setattr(reader, 'MAX_PARSED_BYTES', 30)
    with pytest.raises(reader.ReaderError): parsed(b'[{"id":"a"}]')
    monkeypatch.setattr(reader, 'MAX_EXPANDED_BYTES', 5)
    with pytest.raises(reader.ReaderError): parsed(workbook_bytes(), 'xlsx')


def test_errors_do_not_echo_raw_cells_or_parser_exception_text():
    with pytest.raises(reader.ReaderError) as caught:
        parsed(b'{"secret-private-cell":"do-not-echo",')
    assert 'do-not-echo' not in str(caught.value)
