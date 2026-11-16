"""Check the synthetic OPC fixture with the real inert reader before PG work."""
from generic_agents.native_report_reading import read_docx
from tests.test_report_quote_binding import QUOTE, word_bytes


def test_synthetic_word_has_readable_literal_body_without_hiding_its_image_gap():
    reading = read_docx(word_bytes())
    assert sum(QUOTE in block['text'] for block in reading['blocks']) == 1
    # The body reader has not interpreted the embedded image as report meaning.
    assert reading['coverage']['gaps']
