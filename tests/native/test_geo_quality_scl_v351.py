"""Raster-level adversarial checks, run only in the fixed local geo environment."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from PIL import Image

MODULE=Path(__file__).resolve().parents[2]/'src/generic_agents/geo_quality_scl_runner_v351.py'
spec=importlib.util.spec_from_file_location('quality_runner_v351',MODULE);runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

class SCLTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        for side in ('before','after'):
            self.write_image(side);self.write_quality(side)
    def tearDown(self):self.temporary.cleanup()
    def write_image(self,side,transform=None):
        with rasterio.open(self.root/(side+'.tif'),'w',driver='GTiff',height=32,width=32,count=3,dtype='uint8',crs='EPSG:32650',transform=transform or from_origin(500000,2700000,10,10)) as dst:
            dst.write(np.full((3,32,32),120,dtype='uint8'))
    def write_quality(self,side,code=4,transform=None,cloud=False):
        value=np.full((16,16),code,dtype='uint8')
        if cloud:value[8,8]=9
        with rasterio.open(self.root/(side+'-quality.tif'),'w',driver='GTiff',height=16,width=16,count=1,dtype='uint8',crs='EPSG:32650',transform=transform or from_origin(500000,2700000,20,20)) as dst:dst.write(value,1)
    def run_quality(self):
        output=self.root/'out';output.mkdir();return runner.build([self.root/'before.tif',self.root/'after.tif'],[self.root/'before-quality.tif',self.root/'after-quality.tif'],output)
    def test_public_policy_buffer_and_mask_are_persisted(self):
        self.write_quality('after',cloud=True);value=self.run_quality()
        self.assertEqual(value['source_association'],'user_declared_unverified');self.assertEqual(value['total_pixels'],1024)
        self.assertGreater(value['common_valid_pixels'],0);self.assertGreater(value['unknown_or_excluded_pixels'],0)
        with rasterio.open(self.root/'out/after.tif') as source:
            mask=source.dataset_mask();self.assertEqual(mask[16,16],0);self.assertEqual(mask[0,0],0);self.assertEqual(mask[6,6],255)
            self.assertEqual(int(source.read(1)[16,16]),120)
        with Image.open(self.root/'out/common-valid.png') as mask:self.assertEqual(np.count_nonzero(np.asarray(mask)),value['common_valid_pixels'])
    def test_unknown_class_is_not_silently_accepted(self):
        self.write_quality('after',code=12)
        with self.assertRaisesRegex(ValueError,'类别'):self.run_quality()
    def test_half_pixel_offset_is_rejected(self):
        self.write_quality('after',transform=from_origin(500005,2700000,20,20))
        with self.assertRaisesRegex(ValueError,'未对齐'):self.run_quality()
    def test_two_images_with_same_dimensions_but_shifted_grid_are_rejected(self):
        self.write_image('after',transform=from_origin(500010,2700000,10,10))
        with self.assertRaisesRegex(ValueError,'网格不同'):self.run_quality()
    def test_quality_outside_coverage_is_unknown_not_no_change(self):
        self.write_quality('after',transform=from_origin(500020,2700000,20,20));value=self.run_quality()
        with rasterio.open(self.root/'out/after.tif') as source:self.assertTrue(np.all(source.dataset_mask()[:,:2]==0))
        self.assertGreater(value['unknown_or_excluded_pixels'],0)

if __name__=='__main__':unittest.main()
