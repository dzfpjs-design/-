"""Independent synthetic fixtures; no user documents or database required."""
import io
import json
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from generic_agents import docx_structure_audit as audit
from generic_agents.document_parsers import ParseProblem, parse_document


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
WPS = 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape'


def document(body, extra=''):
    xml = (f'<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:wps="{WPS}" {extra}>'
           f'<w:body>{body}</w:body></w:document>').encode()
    return archive(xml)


def archive(xml):
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as z:
        z.writestr('word/document.xml', xml)
    return buffer.getvalue()


class StructureAuditTest(unittest.TestCase):
    def test_separate_directory_and_single_visible_textbox_without_renumbering_v1(self):
        data = document('''<w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/>
            </w:docPartObj></w:sdtPr><w:sdtContent><w:p><w:r><w:t>Index entry</w:t></w:r></w:p>
            </w:sdtContent></w:sdt><w:p><w:r><w:t>ordinary</w:t></w:r><mc:AlternateContent>
            <mc:Choice Requires="wps"><w:txbxContent><w:p><w:r><w:t>box</w:t></w:r></w:p></w:txbxContent></mc:Choice>
            <mc:Fallback><w:txbxContent><w:p><w:r><w:t>box</w:t></w:r></w:p></w:txbxContent></mc:Fallback>
            </mc:AlternateContent></w:p>''')
        before = parse_document(data, 'example.docx')
        summary = audit.audit_docx_structure(data)
        self.assertNotIn('candidates', summary)
        self.assertNotIn('ordinary', json.dumps(summary))
        self.assertEqual(summary['counts']['top_level_directories'], 1)
        self.assertEqual(summary['counts']['textboxes_all_branches'], 2)
        self.assertEqual(summary['status'], 'review_required')
        full = audit.audit_docx_structure(data, include_candidates=True)
        self.assertEqual([(x['kind'], x['text']) for x in full['candidates']],
                         [('directory_entry', 'Index entry'), ('textbox', 'box')])
        for c in full['candidates']:
            self.assertTrue(c['review_required'])
            self.assertEqual(c['location']['scheme'], 'docx-audit-v1')
            self.assertTrue(all(i > 0 for i in c['location']['child_path']))
        self.assertEqual(parse_document(data, 'example.docx'), before)
        self.assertEqual(before['blocks'][0]['id'], 'b1')

    def test_unknown_choice_uses_one_fallback_and_ignores_deleted_text(self):
        data = document('''<w:p><mc:AlternateContent><mc:Choice Requires="future">
          <w:txbxContent><w:p><w:r><w:t>wrong</w:t></w:r></w:p></w:txbxContent></mc:Choice>
          <mc:Fallback><w:txbxContent><w:p><w:ins><w:r><w:t>new</w:t></w:r></w:ins>
          <w:del><w:r><w:t>old</w:t></w:r></w:del></w:p></w:txbxContent></mc:Fallback>
          </mc:AlternateContent></w:p>''')
        result = audit.audit_docx_structure(data, include_candidates=True)
        self.assertEqual([c['text'] for c in result['candidates']], ['new'])
        self.assertIn('tracked_changes_require_review', {x['code'] for x in result['issues']})
        self.assertEqual(result['counts']['insertions'], 1)

    def test_unavailable_branch_and_other_top_level_control_are_not_silently_extracted(self):
        data = document('''<w:sdt><w:sdtContent><w:p><w:r><w:t>other</w:t></w:r></w:p></w:sdtContent></w:sdt>
          <w:p><mc:AlternateContent><mc:Choice Requires="future"><w:txbxContent>
          <w:p><w:r><w:t>hidden</w:t></w:r></w:p></w:txbxContent></mc:Choice></mc:AlternateContent></w:p>''')
        result = audit.audit_docx_structure(data, include_candidates=True)
        self.assertEqual(result['counts']['top_level_other_content_controls'], 1)
        self.assertEqual(result['counts']['alternate_without_supported_branch'], 1)
        self.assertEqual(result['candidates'], [])

    def test_namespace_rebinding_on_choice_does_not_select_incompatible_branch(self):
        data = document('''<w:p><mc:AlternateContent><mc:Choice xmlns:wps="urn:not-the-word-shape-namespace" Requires="wps">
          <w:txbxContent><w:p><w:r><w:t>wrong</w:t></w:r></w:p></w:txbxContent></mc:Choice>
          <mc:Fallback><w:txbxContent><w:p><w:r><w:t>fallback</w:t></w:r></w:p></w:txbxContent></mc:Fallback>
          </mc:AlternateContent></w:p>''')
        result = audit.audit_docx_structure(data, include_candidates=True)
        self.assertEqual([c['text'] for c in result['candidates']], ['fallback'])

    def test_skipped_top_level_wrapper_and_nested_table_are_reported(self):
        data = document('''<w:customXml><w:p><w:r><w:t>not native</w:t></w:r></w:p></w:customXml>
          <w:tbl><w:tr><w:tc><w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>''')
        result = audit.audit_docx_structure(data)
        self.assertEqual(result['counts']['other_skipped_top_level_with_text'], 1)
        self.assertEqual(result['counts']['nested_tables'], 1)
        codes = {x['code'] for x in result['issues']}
        self.assertIn('other_top_level_text_not_in_native_blocks', codes)
        self.assertIn('nested_table_requires_review', codes)

    def test_nested_toc_does_not_relabel_outer_control_or_extract_its_content(self):
        data = document('''<w:sdt><w:sdtContent><w:p><w:r><w:t>ordinary</w:t></w:r></w:p>
          <w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/>
          </w:docPartObj></w:sdtPr><w:sdtContent><w:p><w:r><w:t>nested index</w:t></w:r></w:p>
          </w:sdtContent></w:sdt></w:sdtContent></w:sdt>''')
        result = audit.audit_docx_structure(data, include_candidates=True)
        self.assertEqual(result['counts']['top_level_directories'], 0)
        self.assertEqual(result['counts']['top_level_other_content_controls'], 1)
        self.assertEqual(result['candidates'], [])

    def test_nested_textbox_gets_distinct_candidate_without_duplicate_outer_text(self):
        data = document('''<w:p><w:txbxContent><w:p><w:r><w:t>outer</w:t></w:r>
          <w:txbxContent><w:p><w:r><w:t>inner</w:t></w:r></w:p></w:txbxContent>
          </w:p></w:txbxContent></w:p>''')
        result = audit.audit_docx_structure(data, include_candidates=True)
        self.assertEqual([c['text'] for c in result['candidates']], ['outer', 'inner'])
        self.assertNotEqual(result['candidates'][0]['location'], result['candidates'][1]['location'])

    def test_malformed_and_hostile_xml_are_rejected_without_leaking_content(self):
        for xml in (b'<broken', b'<!DOCTYPE a [<!ENTITY x "secret">]><a>&x;</a>'):
            with self.assertRaises(ParseProblem) as error:
                audit.audit_docx_structure(archive(xml))
            self.assertNotIn('secret', str(error.exception))

    def test_damaged_crc_and_unsupported_compression_return_controlled_error(self):
        original = document('<w:p><w:r><w:t>synthetic</w:t></w:r></w:p>')
        damaged = original.replace(b'synthetic', b'SYNTHETIC', 1)
        unsupported = bytearray(original)
        local = unsupported.find(b'PK\x03\x04')
        central = unsupported.find(b'PK\x01\x02')
        self.assertGreaterEqual(local, 0); self.assertGreaterEqual(central, 0)
        unsupported[local+8:local+10] = (99).to_bytes(2, 'little')
        unsupported[central+10:central+12] = (99).to_bytes(2, 'little')
        for data in (damaged, bytes(unsupported)):
            with self.assertRaises(ParseProblem) as error:
                audit.audit_docx_structure(data)
            self.assertNotIn('synthetic', str(error.exception))

    def test_document_root_and_single_direct_body_are_required(self):
        bad_root = f'<evil xmlns:w="{W}"><w:body/></evil>'.encode()
        two_bodies = f'<w:document xmlns:w="{W}"><w:body/><w:body/></w:document>'.encode()
        for xml in (bad_root, two_bodies):
            with self.assertRaises(ParseProblem):
                audit.audit_docx_structure(archive(xml))

    def test_explicit_size_and_candidate_limits_fail_closed(self):
        data = document('<w:sdt><w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/></w:docPartObj></w:sdtPr>'
                        '<w:sdtContent><w:p><w:r><w:t>abcdef</w:t></w:r></w:p></w:sdtContent></w:sdt>')
        with patch.object(audit, 'MAX_XML_BYTES', 10), self.assertRaises(ParseProblem):
            audit.audit_docx_structure(data)
        with patch.object(audit, 'MAX_CANDIDATE_CHARS', 3), self.assertRaises(ParseProblem):
            audit.audit_docx_structure(data, include_candidates=True)
        with patch.object(audit, 'MAX_ELEMENTS', 3), self.assertRaises(ParseProblem):
            audit.audit_docx_structure(data)

    def test_deeply_nested_directory_paragraphs_have_total_work_budget(self):
        nested = '<w:r><w:t>candidate</w:t></w:r>'
        for _ in range(15):
            nested = '<w:p>' + nested + '</w:p>'
        data = document('<w:sdt><w:sdtPr><w:docPartObj>'
                        '<w:docPartGallery w:val="Table of Contents"/>'
                        '</w:docPartObj></w:sdtPr><w:sdtContent>' + nested +
                        '</w:sdtContent></w:sdt>')
        with patch.object(audit, 'MAX_EXTRACTION_VISITS', 30):
            self.assertEqual(audit.audit_docx_structure(data)['counts']['top_level_directories'], 1)
            with self.assertRaises(ParseProblem):
                audit.audit_docx_structure(data, include_candidates=True)


if __name__ == '__main__':
    unittest.main()
