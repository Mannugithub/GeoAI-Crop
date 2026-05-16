import numpy as np
import rasterio
from rasterio.transform import from_origin
import os

data_dir = os.path.join(os.path.dirname(__file__), 'data', 'sentinel2_samples')
os.makedirs(data_dir, exist_ok=True)

# Generate a small 100x100 dummy raster around Jhansi (Lat 25.4484, Lon 78.5685)
# Using roughly those coordinates for bounds
# Pixel size around 10m (approx 0.0001 deg)
lon_min = 78.50
lat_max = 25.50
pixel_size = 0.001

transform = from_origin(lon_min, lat_max, pixel_size, pixel_size)
crs = 'EPSG:4326'

# Let's create an NDVI-like array (values around 0.6 to 0.8)
data = np.random.uniform(0.6, 0.8, (100, 100)).astype(np.float32)

# Multiply by 10000 to simulate typical scaled NDVI
data = (data * 10000).astype(np.int16)

filepath = os.path.join(data_dir, '20230515_sentinel2_dummy.tif')

with rasterio.open(
    filepath, 'w', driver='GTiff',
    height=data.shape[0], width=data.shape[1],
    count=1, dtype=data.dtype,
    crs=crs, transform=transform,
) as dst:
    dst.write(data, 1)

print(f"Created dummy TIF at {filepath}")
