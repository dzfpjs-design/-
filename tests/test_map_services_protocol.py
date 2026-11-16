import unittest
from generic_agents.map_services_protocol import wmts_catalog, arcgis_catalog, wmts_params, arcgis_params

WMTS=b'''<?xml version="1.0"?><Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1" version="1.0.0"><Contents><Layer><ows:Title>Test</ows:Title><ows:Identifier>forest</ows:Identifier><Style isDefault="true"><ows:Identifier>default</ows:Identifier></Style><Format>image/png</Format><ResourceURL template="https://untrusted.example/{TileRow}"/><TileMatrixSetLink><TileMatrixSet>world</TileMatrixSet></TileMatrixSetLink></Layer><TileMatrixSet><ows:Identifier>world</ows:Identifier><ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS><TileMatrix><ows:Identifier>0</ows:Identifier><TileWidth>256</TileWidth><TileHeight>256</TileHeight><MatrixWidth>1</MatrixWidth><MatrixHeight>1</MatrixHeight></TileMatrix></TileMatrixSet></Contents></Capabilities>'''
ARCGIS=b'{"layers":[{"id":0,"name":"Forest"}],"initialExtent":{"xmin":110,"ymin":20,"xmax":120,"ymax":30,"spatialReference":{"wkid":4326}}}'

class MapsProtocolTest(unittest.TestCase):
    def test_wmts_directory_controls_selection_and_ignores_remote_template(self):
        cat=wmts_catalog(WMTS);self.assertTrue(cat['ignored_catalog_links']);self.assertNotIn('untrusted',str(cat))
        params=wmts_params(cat,{'layer':'forest','style':'default','matrix_set':'world','matrix':'0','row':0,'col':0})
        self.assertEqual(params['REQUEST'],'GetTile');self.assertEqual(params['TILEMATRIX'],'0')
        with self.assertRaises(ValueError):wmts_params(cat,{'layer':'forest','style':'default','matrix_set':'world','matrix':'0','row':0,'col':1})
    def test_wmts_rejects_entities_and_unhandled_restricted_matrix(self):
        with self.assertRaises(ValueError):wmts_catalog(b'<!DOCTYPE x [<!ENTITY x "evil">]>'+WMTS)
        restricted=WMTS.replace(b'<TileMatrixSet>world</TileMatrixSet></TileMatrixSetLink>',b'<TileMatrixSet>world</TileMatrixSet><TileMatrixSetLimits/></TileMatrixSetLink>')
        with self.assertRaises(ValueError):wmts_catalog(restricted)
    def test_arcgis_bounds_and_layer_are_enforced(self):
        cat=arcgis_catalog(ARCGIS)
        out=arcgis_params(cat,{'layer':'0','bbox':[111,21,119,29]});self.assertEqual(out['layers'],'show:0')
        with self.assertRaises(ValueError):arcgis_params(cat,{'layer':'1','bbox':[111,21,119,29]})
        with self.assertRaises(ValueError):arcgis_params(cat,{'layer':'0','bbox':[100,21,119,29]})
    def test_bad_catalogs_are_rejected(self):
        for value in (b'{"error":{"message":"auth"}}',b'[]',ARCGIS.replace(b'4326',b'1234')):
            with self.assertRaises(ValueError):arcgis_catalog(value)
        with self.assertRaises(ValueError):wmts_catalog(WMTS.replace(b'version="1.0.0"',b'version="9.0.0"'))
