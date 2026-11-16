import json
import unittest
from generic_agents.map_services_protocol import arcgis_image_catalog,arcgis_params

class ImageServerTest(unittest.TestCase):
    def test_catalog_and_export_are_bounded_to_the_declared_extent(self):
        catalog=arcgis_image_catalog(json.dumps({'fullExtent':{'xmin':100,'ymin':20,'xmax':120,'ymax':30,'spatialReference':{'wkid':4326}}}).encode())
        result=arcgis_params(catalog,{'layer':'image','bbox':[101,21,102,22]})
        self.assertNotIn('layers',result);self.assertEqual(result['imageSR'],'4326')
        with self.assertRaises(ValueError):arcgis_params(catalog,{'layer':'image','bbox':[99,21,102,22]})
        with self.assertRaises(ValueError):arcgis_params(catalog,{'layer':'arbitrary','bbox':[101,21,102,22]})

    def test_unknown_crs_is_rejected(self):
        with self.assertRaises(ValueError):arcgis_image_catalog(json.dumps({'fullExtent':{'xmin':0,'ymin':0,'xmax':1,'ymax':1,'spatialReference':{'wkid':9999}}}).encode())

    def test_full_extent_takes_precedence_over_viewport(self):
        extent=lambda a,b:{'xmin':a,'ymin':a,'xmax':b,'ymax':b,'spatialReference':{'wkid':4326}}
        cat=arcgis_image_catalog(json.dumps({'initialExtent':extent(1,2),'fullExtent':extent(0,5)}).encode())
        self.assertEqual(cat['bounds'],[0,0,5,5])
