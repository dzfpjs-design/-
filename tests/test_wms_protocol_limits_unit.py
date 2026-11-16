"""Isolated parser limits; no network, database, or project credentials."""
import unittest
from generic_agents import wms_protocol as wms


def document(layers):
    return (b'<WMS_Capabilities version="1.3.0"><Capability><Request><GetMap>'
            b'<Format>image/png</Format></GetMap></Request><Layer>' + layers
            + b'</Layer></Capability></WMS_Capabilities>')


class WmsCatalogLimits(unittest.TestCase):
    def test_large_legitimate_layer_catalog_is_supported(self):
        layer = lambda i: ('<Layer><Name>layer_%d</Name><Title>layer</Title></Layer>' % i).encode()
        data = document(b''.join(layer(i) for i in range(1608)))
        self.assertEqual(len(wms.catalog(data, '1.3.0')['layers']), 1608)

    def test_layer_node_size_and_entity_limits_still_enforced(self):
        layer = lambda i: ('<Layer><Name>layer_%d</Name></Layer>' % i).encode()
        with self.assertRaisesRegex(ValueError, '目录超过'):
            wms.catalog(document(b''.join(layer(i) for i in range(wms.LAYER_LIMIT + 1))), '1.3.0')
        with self.assertRaisesRegex(ValueError, '元素过多'):
            wms.catalog(document(b'<Layer><Name>a</Name>' + b'<Title>x</Title>' * wms.XML_NODE_LIMIT + b'</Layer>'), '1.3.0')
        with self.assertRaisesRegex(ValueError, '大小不支持'):
            wms.catalog(b'x' * (wms.XML_LIMIT + 1), '1.3.0')
        with self.assertRaisesRegex(ValueError, '禁止实体'):
            wms.catalog(b'<!DOCTYPE x [<!ENTITY x "unsafe">]>' + document(b'<Layer><Name>&x;</Name></Layer>'), '1.3.0')


if __name__ == '__main__':
    unittest.main()
