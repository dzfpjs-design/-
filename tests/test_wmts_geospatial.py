import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from generic_agents.wmts_geospatial import (
    EARTH_RADIUS, METRES_PER_DEGREE, PIXEL_METRES, apply_matrix_limits,
    locate_bounds, locate_point, matrix_geometry, supported_crs,
)
from generic_agents.map_services_protocol import wmts_catalog, wmts_params, wmts_locate
from generic_agents.wmts_position_api import Locate, locate
from fastapi import HTTPException
from tests.test_map_services_protocol import WMTS


def matrix(crs, origin, resolution, width, height):
    units = 1 if crs == 'EPSG:3857' else METRES_PER_DEGREE
    scale = resolution * units / PIXEL_METRES
    raw = f'<TileMatrix><TopLeftCorner>{origin}</TopLeftCorner><ScaleDenominator>{scale}</ScaleDenominator></TileMatrix>'
    return {'id':'m','width':width,'height':height,'tile_width':256,'tile_height':256,
            'geometry':matrix_geometry(fromstring(raw),crs)}


class WmtsGeospatialTest(unittest.TestCase):
    def test_epsg_4326_axis_order_and_crs84(self):
        latitude_first = matrix('EPSG:4326','90 -180',180/256,2,1)
        longitude_first = matrix('CRS:84','-180 90',180/256,2,1)
        self.assertEqual(latitude_first['geometry'], longitude_first['geometry'] | {'crs':'EPSG:4326'})
        for m in (latitude_first,longitude_first):
            self.assertEqual(locate_point(m,-100,20),{'row':0,'col':0})
            self.assertEqual(locate_point(m,100,20),{'row':0,'col':1})
            self.assertEqual(locate_bounds(m,-180,-90,180,90)['tile_count'],2)
            self.assertEqual(locate_bounds(m,-180,0,0,90)['tile_count'],1)
            self.assertEqual(locate_bounds(m,0,0,180,90)['tile_count'],1)

    def test_web_mercator_positions_and_extent(self):
        edge = math.pi * EARTH_RADIUS
        m = matrix('EPSG:3857',f'{-edge} {edge}',2*edge/512,2,2)
        self.assertEqual(locate_point(m,-20,20),{'row':0,'col':0})
        self.assertEqual(locate_point(m,20,-20),{'row':1,'col':1})
        r = locate_bounds(m,-20,-20,20,20)
        self.assertEqual(r,{'min_row':0,'max_row':1,'min_col':0,'max_col':1,'tile_count':4})
        self.assertEqual(locate_bounds(m,-20,10,-10,20)['tile_count'],1)
        with self.assertRaises(ValueError):locate_point(m,0,90)
        with self.assertRaises(ValueError):locate_point(m,181,0)

    def test_capabilities_matrix_limits_are_enforced_and_fail_closed(self):
        m=matrix('CRS:84','-180 90',180/256,2,1)
        tile_set={'id':'geo','crs':'CRS:84','matrices':[m]}
        link=fromstring('<TileMatrixSetLink><TileMatrixSetLimits><TileMatrixLimits><TileMatrix>m</TileMatrix><MinTileRow>0</MinTileRow><MaxTileRow>0</MaxTileRow><MinTileCol>1</MinTileCol><MaxTileCol>1</MaxTileCol></TileMatrixLimits></TileMatrixSetLimits></TileMatrixSetLink>')
        limited=apply_matrix_limits(link,tile_set)['matrices'][0]
        self.assertEqual(locate_point(limited,100,20),{'row':0,'col':1})
        with self.assertRaises(ValueError):locate_point(limited,-100,20)
        with self.assertRaises(ValueError):locate_bounds(limited,-100,10,100,20)
        self.assertNotIn('limits',tile_set['matrices'][0])
        with self.assertRaises(ValueError):apply_matrix_limits(fromstring('<TileMatrixSetLink><TileMatrixSetLimits/></TileMatrixSetLink>'),tile_set)
        with self.assertRaises(ValueError):apply_matrix_limits(fromstring('<TileMatrixSetLink><TileMatrixSetLimits><TileMatrixLimits><TileMatrix>m</TileMatrix><MinTileRow>0</MinTileRow><MaxTileRow>0</MaxTileRow><MinTileCol>0</MinTileCol><MaxTileCol>5</MaxTileCol></TileMatrixLimits></TileMatrixSetLimits></TileMatrixSetLink>'),tile_set)

    def test_invalid_geometry_is_never_guessed(self):
        self.assertIsNone(matrix_geometry(fromstring('<TileMatrix><TopLeftCorner>0 0</TopLeftCorner><ScaleDenominator>1</ScaleDenominator></TileMatrix>'),'EPSG:1234'))
        self.assertIsNone(matrix_geometry(fromstring('<TileMatrix><TopLeftCorner>nan 0</TopLeftCorner><ScaleDenominator>1</ScaleDenominator></TileMatrix>'),'EPSG:3857'))
        self.assertIsNone(supported_crs('https://evil.example/EPSG/0/3857'))
        self.assertEqual(supported_crs('urn:ogc:def:crs:EPSG::4326'),'EPSG:4326')
        with self.assertRaises(ValueError):locate_point({'geometry':None},0,0)
        with self.assertRaises(ValueError):locate_point(matrix('CRS:84','-180 90',180/256,2,1),True,0)
        with self.assertRaises(ValueError):locate_bounds(matrix('CRS:84','-180 90',180/256,2,1),170,0,-170,20)

    def test_catalog_enforces_per_layer_limits_and_saved_geometry(self):
        edge=math.pi*EARTH_RADIUS;scale=(2*edge/256)/PIXEL_METRES
        geometry=f'<ScaleDenominator>{scale}</ScaleDenominator><TopLeftCorner>{-edge} {edge}</TopLeftCorner>'.encode()
        xml=WMTS.replace(b'<TileWidth>',geometry+b'<TileWidth>',1)
        source=wmts_catalog(xml)
        self.assertEqual(wmts_locate(source,'forest','world','0',longitude=117.65,latitude=24.51),{'kind':'point','row':0,'col':0})
        old=WMTS
        with self.assertRaisesRegex(ValueError,'原点和比例尺'):
            wmts_locate(wmts_catalog(old),'forest','world','0',longitude=117.65,latitude=24.51)
        limit=b'<TileMatrixSetLimits><TileMatrixLimits><TileMatrix>0</TileMatrix><MinTileRow>0</MinTileRow><MaxTileRow>0</MaxTileRow><MinTileCol>0</MinTileCol><MaxTileCol>0</MaxTileCol></TileMatrixLimits></TileMatrixSetLimits>'
        limited=wmts_catalog(xml.replace(b'<TileMatrixSet>world</TileMatrixSet></TileMatrixSetLink>',b'<TileMatrixSet>world</TileMatrixSet>'+limit+b'</TileMatrixSetLink>'))
        entry=limited['layers'][0]['matrix_sets'][0]['matrices'][0]
        self.assertEqual(entry['limits'],{'min_row':0,'max_row':0,'min_col':0,'max_col':0})
        sel={'layer':'forest','style':'default','matrix_set':'world','matrix':'0','row':0,'col':0}
        self.assertEqual(wmts_params(limited,sel)['REQUEST'],'GetTile')
        limited['layers'][0]['matrix_sets'][0]['matrices'][0]['limits']['max_col']=-1
        with self.assertRaises(ValueError):wmts_params(limited,sel)

    def test_api_checks_saved_catalog_version_and_workspace_scoping(self):
        edge=math.pi*EARTH_RADIUS;scale=(2*edge/256)/PIXEL_METRES
        xml=WMTS.replace(b'<TileWidth>',f'<ScaleDenominator>{scale}</ScaleDenominator><TopLeftCorner>{-edge} {edge}</TopLeftCorner><TileWidth>'.encode(),1)
        row=SimpleNamespace(id='connection',version=3,data={'protocol':'wmts','catalog':wmts_catalog(xml),'catalog_sha256':'a'*64})
        p=SimpleNamespace(workspace_id='workspace');db=object()
        request=Locate(version=3,layer='forest',matrix_set='world',matrix='0',longitude=117.65,latitude=24.51)
        with patch('generic_agents.wmts_position_api._connection',return_value=row) as scoped:
            got=locate('connection',request,p,db)
            scoped.assert_called_once_with(db,p,'connection')
            self.assertEqual((got['row'],got['col']),(0,0))
        with patch('generic_agents.wmts_position_api._connection',return_value=row):
            with self.assertRaises(HTTPException) as error:
                locate('connection',request.model_copy(update={'version':2}),p,db)
            self.assertEqual(error.exception.status_code,409)

    def test_duplicate_matrix_links_cannot_hide_a_restriction(self):
        original=b'<TileMatrixSetLink><TileMatrixSet>world</TileMatrixSet></TileMatrixSetLink>'
        restricted=(b'<TileMatrixSetLink><TileMatrixSet>world</TileMatrixSet><TileMatrixSetLimits>'
                    b'<TileMatrixLimits><TileMatrix>0</TileMatrix><MinTileRow>0</MinTileRow><MaxTileRow>0</MaxTileRow>'
                    b'<MinTileCol>0</MinTileCol><MaxTileCol>0</MaxTileCol></TileMatrixLimits>'
                    b'</TileMatrixSetLimits></TileMatrixSetLink>')
        self.assertIn(original,WMTS)
        for duplicate in (original+restricted,restricted+original):
            with self.subTest(restricted_first=duplicate.startswith(restricted)):
                with self.assertRaisesRegex(ValueError,'重复'):
                    wmts_catalog(WMTS.replace(original,duplicate).replace(b'<MatrixWidth>1</MatrixWidth>',b'<MatrixWidth>2</MatrixWidth>'))


if __name__=='__main__':unittest.main()
