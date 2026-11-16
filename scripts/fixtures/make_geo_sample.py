"""Create an explicitly synthetic georeferenced TIFF for an isolated test gateway."""
import argparse
import numpy as np
import rasterio
from rasterio.transform import from_origin
parser=argparse.ArgumentParser();parser.add_argument('path');args=parser.parse_args()
h=w=256;y,x=np.mgrid[0:h,0:w];bands=np.stack(((50+x//3).astype('uint8'),(100+y//4).astype('uint8'),(60+(x+y)//8).astype('uint8')))
with rasterio.open(args.path,'w',driver='GTiff',width=w,height=h,count=3,dtype='uint8',crs='EPSG:4326',transform=from_origin(117.7,24.6,.0001,.0001)) as dst:dst.write(bands)
print('Synthetic test imagery created:',args.path)
