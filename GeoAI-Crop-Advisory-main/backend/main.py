import os
from dotenv import load_dotenv
load_dotenv()
import io
import glob
import asyncio
import numpy as np
from datetime import datetime

# Allow rioxarray / GDAL to read public AWS COGs without credentials
os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", "tif,tiff,jp2")

import rasterio
import rasterio.transform
import rasterio.features
import rasterio.crs
from rasterio.io import MemoryFile
from shapely.geometry import shape, box, mapping
import pystac_client
import rioxarray

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import CharacterTextSplitter
from langchain_classic.chains import LLMChain
from langchain_classic.chains import SequentialChain

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'sentinel2_samples')
os.makedirs(DATA_DIR, exist_ok=True)

@app.get("/data/{filename}")
def get_data_file(filename: str):
    file_path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Requested raster asset not found on server")
    return FileResponse(file_path)

# ─── AWS Earth Search STAC endpoint ───────────────────────────────────────────
AWS_STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION   = "sentinel-2-l2a"

# ─── LLM ──────────────────────────────────────────────────────────────────────
api_key = os.environ.get("GOOGLE_API_KEY")       
llm = None
if api_key:
    try:
        llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite", temperature=0.7)
    except Exception as e:
        print("Failed to initialise LLM:", e)

# ─── RAG Setup ──────────────────────────────────────────────────────────────
embeddings = None
vectorstore = None

def init_rag():
    global embeddings, vectorstore
    if not api_key:
        return
    
    try:
        embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-2")
        knowledge_path = os.path.join(os.path.dirname(__file__), 'agronomy_knowledge.txt')
        
        if os.path.exists(knowledge_path):
            loader = TextLoader(knowledge_path)
            documents = loader.load()
            text_splitter = CharacterTextSplitter(chunk_size=500, chunk_overlap=50)
            docs = text_splitter.split_documents(documents)
            vectorstore = FAISS.from_documents(docs, embeddings)
            print("RAG Knowledge Base initialised successfully.")
        else:
            print("Knowledge base file not found. RAG disabled.")
    except Exception as e:
        print(f"Failed to initialise RAG: {e}")

init_rag()

# ─── Chains ──────────────────────────────────────────────────────────────────

# Chain 1: Retrieve context and generate Agronomy Advisory
agronomy_template = """You are an expert Agronomist. Use the following context and satellite metrics to provide a detailed Farmer Advisory.
Your response MUST be concise and strictly limited to exactly 10 lines.

Context from Knowledge Base:
{context}

Satellite Metrics:
- Mean NDVI: {ndvi}
- Mean LSWI: {lswi}
- Soil Moisture Proxy: {soil_moisture}
- Est. Sowing: {sowing_date}
- Est. Harvest: {harvest_date}

Advisory:
Provide a clear agronomy assessment, irrigation needs, and immediate action items for the farmer.
STRICT REQUIREMENT: YOUR RESPONSE MUST BE EXACTLY 10 LINES LONG.
"""

agronomy_prompt = PromptTemplate(input_variables=["context", "ndvi", "lswi", "soil_moisture", "sowing_date", "harvest_date"], template=agronomy_template)
agronomy_chain = LLMChain(llm=llm, prompt=agronomy_prompt, output_key="agronomy_advisory") if llm else None

# Chain 2: Generate Insurance Risk Assessment based on metrics and the agronomy advisory
insurance_template = """You are an Agricultural Insurance Underwriter. Based on the satellite metrics and the following Agronomy Advisory, provide an Insurance Risk Assessment.
Your response MUST be concise and strictly limited to exactly 10 lines.

Agronomy Advisory:
{agronomy_advisory}

Satellite Metrics:
- Mean NDVI: {ndvi}
- Mean LSWI: {lswi}
- Soil Moisture Proxy: {soil_moisture}

Risk Assessment:
Provide yield prospects, risk analysis, and claim eligibility status.
STRICT REQUIREMENT: YOUR RESPONSE MUST BE EXACTLY 10 LINES LONG.
"""

insurance_prompt = PromptTemplate(input_variables=["agronomy_advisory", "ndvi", "lswi", "soil_moisture"], template=insurance_template)
insurance_chain = LLMChain(llm=llm, prompt=insurance_prompt, output_key="insurance_advisory") if llm else None

# Sequential Chain
if agronomy_chain and insurance_chain:
    overall_chain = SequentialChain(
        chains=[agronomy_chain, insurance_chain],
        input_variables=["context", "ndvi", "lswi", "soil_moisture", "sowing_date", "harvest_date"],
        output_variables=["agronomy_advisory", "insurance_advisory"],
        verbose=True
    )
else:
    overall_chain = None

# ─── Request models ────────────────────────────────────────────────────────────
class PolygonRequest(BaseModel):
    geometry: dict
    scene_id: str = None

class StacRequest(BaseModel):
    geometry: dict
    start_date: str
    end_date: str
    cloud_cover: int = 20   # default max cloud cover %

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _open_aws_band(href: str, geom: dict):
    """Open a single COG band from AWS (unsigned, no credentials needed)
    and clip it to the supplied GeoJSON geometry."""
    import rioxarray as rxr
    da = rxr.open_rasterio(
        href,
        chunks={"x": 1024, "y": 1024}
    )
    # Clip to AOI (geometry is in EPSG:4326; reproject to match raster)
    da = da.rio.clip([geom], crs="EPSG:4326", from_disk=True)
    return da.squeeze()            # drop the 'band' dimension


def _ndvi_from_aws(item, geom: dict) -> float:
    """Compute mean NDVI over the AOI using real nir and red COGs from AWS Earth Search."""
    try:
        nir = _open_aws_band(item.assets["nir"].href, geom).values.astype(float)
        red = _open_aws_band(item.assets["red"].href, geom).values.astype(float)
        denom = nir + red
        with np.errstate(invalid="ignore", divide="ignore"):
            ndvi = np.where(denom > 0, (nir - red) / denom, np.nan)
        valid = ndvi[~np.isnan(ndvi) & (ndvi >= -1) & (ndvi <= 1)]
        return float(np.mean(valid)) if len(valid) > 0 else 0.0
    except Exception as e:
        print(f"NDVI computation error: {e}")
        return 0.5          # safe fallback


def _lswi_from_aws(item, geom: dict) -> float:
    """Compute mean LSWI over the AOI using real nir and swir16 COGs from AWS Earth Search."""
    try:
        nir_da = _open_aws_band(item.assets["nir"].href, geom)
        swir_da = _open_aws_band(item.assets["swir16"].href, geom)
        
        # Resample swir to match nir if shapes differ
        if nir_da.shape != swir_da.shape:
            swir_da = swir_da.rio.reproject_match(nir_da)
            
        nir = nir_da.values.astype(float)
        swir = swir_da.values.astype(float)
        
        denom = nir + swir
        with np.errstate(invalid="ignore", divide="ignore"):
            lswi = np.where(denom > 0, (nir - swir) / denom, np.nan)
        valid = lswi[~np.isnan(lswi) & (lswi >= -1) & (lswi <= 1)]
        return float(np.mean(valid)) if len(valid) > 0 else 0.0
    except Exception as e:
        print(f"LSWI computation error: {e}")
        return 0.2          # safe fallback



def _read_band_window(href: str, geom_shape, target_crs="EPSG:4326") -> tuple:
    """Read only the AOI window from a COG href. Returns (arr_2d, transform, crs)."""
    from shapely.ops import transform as shp_transform
    import pyproj

    with rasterio.open(href) as src:
        src_crs = src.crs
        # Project geometry bbox into raster CRS for windowed read
        if str(src_crs) != "EPSG:4326":
            project = pyproj.Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
            geom_projected = shp_transform(project, geom_shape)
        else:
            geom_projected = geom_shape
        window = rasterio.features.geometry_window(src, [geom_projected])
        transform_win = src.window_transform(window)
        arr = src.read(1, window=window)
        win_crs = src.crs
    return arr, transform_win, win_crs


def _reproject_to_4326(arr, transform_src, src_crs, geom_shape):
    """Reproject a 2D uint16 array to EPSG:4326 and mask to polygon."""
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    from rasterio.mask import mask as rio_mask
    import json

    # Compute output transform in EPSG:4326
    dst_crs = rasterio.crs.CRS.from_epsg(4326)
    h, w = arr.shape
    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs, w, h, transform=transform_src
    )
    dst_arr = np.zeros((dst_h, dst_w), dtype=arr.dtype)
    reproject(
        source=arr,
        destination=dst_arr,
        src_transform=transform_src,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.bilinear
    )
    return dst_arr, dst_transform, dst_crs


def _save_tci(item, geom: dict) -> str:
    """Download TCI (visual) COG windowed to AOI, clip, save locally."""
    import hashlib
    from shapely.geometry import shape as shp_shape
    from rasterio.warp import calculate_default_transform, reproject, Resampling

    geom_hash = hashlib.md5(str(geom).encode("utf-8")).hexdigest()[:6]
    tci_path = os.path.join(DATA_DIR, f"{item.id}_{geom_hash}_TCI.tif")
    if os.path.exists(tci_path) and os.path.getsize(tci_path) > 10000:
        return tci_path

    geom_shape = shp_shape(geom)
    href = item.assets["visual"].href
    dst_crs = rasterio.crs.CRS.from_epsg(4326)

    from shapely.ops import transform as shp_transform
    import pyproj

    bands_out = []
    with rasterio.open(href) as src:
        src_crs = src.crs
        if str(src_crs) != "EPSG:4326":
            project = pyproj.Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
            geom_proj = shp_transform(project, geom_shape)
        else:
            geom_proj = geom_shape
        window = rasterio.features.geometry_window(src, [geom_proj])
        transform_win = src.window_transform(window)
        raw = src.read(window=window)   # shape: (3, H, W)

        # Compute window bounds explicitly to satisfy calculate_default_transform
        width = raw.shape[2]
        height = raw.shape[1]
        if width <= 1 or height <= 1:
            width = max(width, 2)
            height = max(height, 2)
        left = transform_win.c
        top = transform_win.f
        right = left + width * transform_win.a
        bottom = top + height * transform_win.e

        # Reproject all 3 bands to EPSG:4326
        dst_transform, dst_w, dst_h = calculate_default_transform(
            src_crs, dst_crs, width, height, left=left, bottom=bottom, right=right, top=top
        )
        for i in range(3):
            dst_band = np.zeros((dst_h, dst_w), dtype=raw.dtype)
            reproject(
                source=raw[i],
                destination=dst_band,
                src_transform=transform_win,
                src_crs=src_crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear
            )
            bands_out.append(dst_band)

    arr = np.stack(bands_out, axis=0)
    # Clip values to 0-255
    arr = np.clip(arr, 0, 255).astype(np.uint8)

    # Tight polygon geometry masking to zero out non-AOI region pixels
    poly_mask = rasterio.features.geometry_mask([geom_shape], out_shape=(dst_h, dst_w), transform=dst_transform, invert=False)
    arr[:, poly_mask] = 0

    with rasterio.open(
        tci_path, "w",
        driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=3,
        dtype=np.uint8, crs=dst_crs, transform=dst_transform, nodata=0
    ) as dst:
        dst.write(arr)

    return tci_path


def _save_fcc(item, geom: dict) -> str:
    """Build FCC (NIR, Red, Green) COG windowed to AOI, clip, save locally."""
    import hashlib
    from shapely.geometry import shape as shp_shape
    from shapely.ops import transform as shp_transform
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    import pyproj

    geom_hash = hashlib.md5(str(geom).encode("utf-8")).hexdigest()[:6]
    fcc_path = os.path.join(DATA_DIR, f"{item.id}_{geom_hash}_FCC.tif")
    if os.path.exists(fcc_path) and os.path.getsize(fcc_path) > 10000:
        return fcc_path

    geom_shape = shp_shape(geom)
    dst_crs = rasterio.crs.CRS.from_epsg(4326)
    band_keys = ["nir", "red", "green"]
    bands_out = []
    dst_transform = dst_w = dst_h = None

    for key in band_keys:
        href = item.assets[key].href
        with rasterio.open(href) as src:
            src_crs = src.crs
            if str(src_crs) != "EPSG:4326":
                project = pyproj.Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
                geom_proj = shp_transform(project, geom_shape)
            else:
                geom_proj = geom_shape
            window = rasterio.features.geometry_window(src, [geom_proj])
            transform_win = src.window_transform(window)
            arr = src.read(1, window=window).astype(float)

        if dst_transform is None:
            width = arr.shape[1]
            height = arr.shape[0]
            if width <= 1 or height <= 1:
                width = max(width, 2)
                height = max(height, 2)
            left = transform_win.c
            top = transform_win.f
            right = left + width * transform_win.a
            bottom = top + height * transform_win.e
            dst_transform, dst_w, dst_h = calculate_default_transform(
                src_crs, dst_crs, width, height, left=left, bottom=bottom, right=right, top=top
            )

        dst_band = np.zeros((dst_h, dst_w), dtype=np.float32)
        reproject(
            source=arr.astype(np.float32),
            destination=dst_band,
            src_transform=transform_win,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear
        )
        bands_out.append(dst_band)

    stack = np.stack(bands_out, axis=0)  # (3, H, W)
    arr_uint8 = np.clip((stack / 3000.0) * 255, 0, 255).astype(np.uint8)
    
    # Tight polygon geometry masking
    poly_mask = rasterio.features.geometry_mask([geom_shape], out_shape=(dst_h, dst_w), transform=dst_transform, invert=False)
    nodata_mask = (arr_uint8[0] == 0) & (arr_uint8[1] == 0) & (arr_uint8[2] == 0) | poly_mask
    arr_uint8[:, nodata_mask] = 0

    with rasterio.open(
        fcc_path, "w",
        driver="GTiff", height=arr_uint8.shape[1], width=arr_uint8.shape[2], count=3,
        dtype=np.uint8, crs=dst_crs, transform=dst_transform, nodata=0
    ) as dst:
        dst.write(arr_uint8)

    return fcc_path


def _ndvi_to_rgb(ndvi_arr):
    arr = np.nan_to_num(ndvi_arr, nan=-1.0)
    r = np.zeros_like(arr, dtype=np.uint8)
    g = np.zeros_like(arr, dtype=np.uint8)
    b = np.zeros_like(arr, dtype=np.uint8)

    c0 = arr <= 0.1
    c1 = (arr > 0.1) & (arr <= 0.3)
    c2 = (arr > 0.3) & (arr <= 0.5)
    c3 = (arr > 0.5) & (arr <= 0.7)
    c4 = arr > 0.7

    # c0: Brown (139, 69, 19)
    r[c0], g[c0], b[c0] = 139, 69, 19

    # c1: interp Brown to Orange (255, 170, 0)
    t1 = (arr[c1] - 0.1) / 0.2
    r[c1] = (139 + t1 * (255 - 139)).astype(np.uint8)
    g[c1] = (69 + t1 * (170 - 69)).astype(np.uint8)
    b[c1] = (19 + t1 * (0 - 19)).astype(np.uint8)

    # c2: interp Orange to Yellow-Green (170, 255, 0)
    t2 = (arr[c2] - 0.3) / 0.2
    r[c2] = (255 + t2 * (170 - 255)).astype(np.uint8)
    g[c2] = (170 + t2 * (255 - 170)).astype(np.uint8)
    b[c2] = 0

    # c3: interp Yellow-Green to Bright Green (0, 204, 0)
    t3 = (arr[c3] - 0.5) / 0.2
    r[c3] = (170 + t3 * (0 - 170)).astype(np.uint8)
    g[c3] = (255 + t3 * (204 - 255)).astype(np.uint8)
    b[c3] = 0

    # c4: interp Bright Green to Deep Green (0, 100, 0)
    t4 = np.clip((arr[c4] - 0.7) / 0.2, 0, 1)
    r[c4] = 0
    g[c4] = (204 + t4 * (100 - 204)).astype(np.uint8)
    b[c4] = 0

    return np.stack([r, g, b], axis=0)


def _save_ndvi_raster(item, geom: dict) -> str:
    """Windowed COG read of NIR+Red, clip to polygon, colormap NDVI, save GeoTIFF."""
    import hashlib
    from shapely.geometry import shape as shp_shape
    from shapely.ops import transform as shp_transform
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    import pyproj

    geom_hash = hashlib.md5(str(geom).encode("utf-8")).hexdigest()[:6]
    ndvi_path = os.path.join(DATA_DIR, f"{item.id}_{geom_hash}_NDVI.tif")
    if os.path.exists(ndvi_path) and os.path.getsize(ndvi_path) > 10000:
        return ndvi_path

    geom_shape = shp_shape(geom)
    dst_crs = rasterio.crs.CRS.from_epsg(4326)
    dst_transform = dst_w = dst_h = None
    nir_proj = red_proj = None

    for key in ["nir", "red"]:
        href = item.assets[key].href
        with rasterio.open(href) as src:
            src_crs = src.crs
            if str(src_crs) != "EPSG:4326":
                project = pyproj.Transformer.from_crs("EPSG:4326", src_crs, always_xy=True).transform
                geom_proj = shp_transform(project, geom_shape)
            else:
                geom_proj = geom_shape
            window = rasterio.features.geometry_window(src, [geom_proj])
            transform_win = src.window_transform(window)
            arr = src.read(1, window=window).astype(np.float32)

        if dst_transform is None:
            width = arr.shape[1]
            height = arr.shape[0]
            if width <= 1 or height <= 1:
                width = max(width, 2)
                height = max(height, 2)
            left = transform_win.c
            top = transform_win.f
            right = left + width * transform_win.a
            bottom = top + height * transform_win.e
            dst_transform, dst_w, dst_h = calculate_default_transform(
                src_crs, dst_crs, width, height, left=left, bottom=bottom, right=right, top=top
            )

        dst_band = np.zeros((dst_h, dst_w), dtype=np.float32)
        reproject(
            source=arr,
            destination=dst_band,
            src_transform=transform_win,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear
        )
        if key == "nir":
            nir_proj = dst_band
        else:
            red_proj = dst_band

    denom = nir_proj + red_proj
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi_arr = np.where(denom > 0, (nir_proj - red_proj) / denom, np.nan)

    # NDVI → RGB colormap
    rgb_stack = _ndvi_to_rgb(ndvi_arr)

    # Tight polygon geometry masking to restrict raster generation strictly inside AOI border
    poly_mask = rasterio.features.geometry_mask([geom_shape], out_shape=(dst_h, dst_w), transform=dst_transform, invert=False)
    nodata_mask = np.isnan(nir_proj) | (nir_proj <= 0) | poly_mask
    rgb_stack[:, nodata_mask] = 0

    with rasterio.open(
        ndvi_path, "w",
        driver="GTiff", height=rgb_stack.shape[1], width=rgb_stack.shape[2], count=3,
        dtype=np.uint8, crs=dst_crs, transform=dst_transform, nodata=0
    ) as dst:
        dst.write(rgb_stack)

    return ndvi_path



def _get_best_item(catalog, geom: dict, start: str, end: str, cloud_pct: int = 20):
    """Query AWS STAC and return items sorted by cloud cover (least cloudy first)."""
    search = catalog.search(
        collections=[COLLECTION],
        intersects=geom,
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": cloud_pct}},
        max_items=20
    )
    items = list(search.items())
    # Sort by cloud cover ascending so first item is clearest
    items.sort(key=lambda i: i.properties.get("eo:cloud_cover", 100))
    return items


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/search-stac")
def search_stac(req: StacRequest):
    """
    Lightweight query: return list of available scenes (no downloads).
    Returns scene metadata: id, date, cloud cover, thumbnail URL.
    """
    try:
        catalog = pystac_client.Client.open(AWS_STAC_URL)
        items = _get_best_item(catalog, req.geometry, req.start_date, req.end_date, req.cloud_cover)

        scenes = []
        for item in items:
            scenes.append({
                "scene_id":    item.id,
                "date":        item.properties["datetime"][:10],
                "cloud_cover": round(item.properties.get("eo:cloud_cover", 0), 1),
                "thumbnail":   item.assets["thumbnail"].href if "thumbnail" in item.assets else None,
            })

        phenology = None
        if items:
            from phenology import extract_phenology_from_rasters
            phenology = extract_phenology_from_rasters(DATA_DIR, req.geometry, reference_date=items[0].properties["datetime"])

        return {
            "count": len(scenes),
            "scenes": scenes,
            "best_phenology": phenology
        }

    except Exception as e:
        print("STAC search error:", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/fetch-stac")
def fetch_stac(req: StacRequest, request: Request):
    """
    1. Query AWS Earth Search STAC for sentinel-2-l2a items.
    2. Download and clip TCI + FCC COGs to the AOI.
    3. Compute mean NDVI over the AOI from real B08/B04 bands.
    """
    try:
        catalog = pystac_client.Client.open(AWS_STAC_URL)
        items = _get_best_item(catalog, req.geometry, req.start_date, req.end_date, req.cloud_cover)

        if not items:
            raise HTTPException(
                status_code=404,
                detail=f"No Sentinel-2 scenes found with <{req.cloud_cover}% cloud cover in this area/date range. "
                       "Try widening your date range or increasing the cloud cover limit."
            )

        item = items[0]
        cloud = round(item.properties.get("eo:cloud_cover", 0), 1)
        scene_date = item.properties["datetime"][:10]
        thumbnail_url = item.assets["thumbnail"].href if "thumbnail" in item.assets else None

        # Save rasters and compute metrics in parallel to speed up initial fetch
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            fut_tci  = executor.submit(_save_tci, item, req.geometry)
            fut_fcc  = executor.submit(_save_fcc, item, req.geometry)
            fut_ndvi = executor.submit(_save_ndvi_raster, item, req.geometry)
            fut_mean_ndvi = executor.submit(_ndvi_from_aws, item, req.geometry)
            fut_mean_lswi = executor.submit(_lswi_from_aws, item, req.geometry)
            
            tci_path  = fut_tci.result()
            fcc_path  = fut_fcc.result()
            ndvi_path = fut_ndvi.result()
            mean_ndvi = fut_mean_ndvi.result()
            mean_lswi = fut_mean_lswi.result()

        from phenology import extract_phenology_from_rasters
        phenology = extract_phenology_from_rasters(DATA_DIR, req.geometry, reference_date=item.properties["datetime"])

        # Base URL construction
        base_url = str(request.base_url).rstrip("/")

        return {
            "status": "success",
            "date": scene_date,
            "scene_id": item.id,
            "cloud_cover": cloud,
            "mean_ndvi": round(mean_ndvi, 3),
            "mean_lswi": round(mean_lswi, 3),
            "sowing_date": phenology["sowing_date"],
            "harvest_date": phenology["harvest_date"],
            "tci_file": f"{base_url}/data/{os.path.basename(tci_path)}",
            "fcc_file": f"{base_url}/data/{os.path.basename(fcc_path)}",
            "ndvi_file": f"{base_url}/data/{os.path.basename(ndvi_path)}",
            "thumbnail": thumbnail_url,
        }

    except HTTPException:
        raise
    except Exception as e:
        print("STAC fetch error:", e)
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ndvi-time-series")
def ndvi_time_series(req: StacRequest):
    """
    Query all low-cloud scenes in the date range, compute real NDVI per scene,
    aggregate into fortnightly bins (14-day), and return:
      - fortnight labels  (e.g. "Oct 01–14")
      - peak_ndvi per bin (max NDVI observed in that fortnight)
      - min_ndvi  per bin (min NDVI observed in that fortnight)
      - season_peak  (overall max)
      - season_min   (overall min)
    """
    from datetime import datetime as dt, timedelta
    from collections import defaultdict

    try:
        catalog = pystac_client.Client.open(AWS_STAC_URL)

        search = catalog.search(
            collections=[COLLECTION],
            intersects=req.geometry,
            datetime=f"{req.start_date}/{req.end_date}",
            query={"eo:cloud_cover": {"lt": req.cloud_cover}},
            max_items=50
        )
        items = list(search.items())
        items.sort(key=lambda i: i.properties["datetime"])

        if not items:
            return {"labels": [], "peak_ndvi": [], "min_ndvi": [],
                    "season_peak": None, "season_min": None}

        # ── Build fortnightly bins ────────────────────────────────────────
        start = dt.strptime(req.start_date, "%Y-%m-%d")
        end   = dt.strptime(req.end_date,   "%Y-%m-%d")
        bins  = []
        cur   = start
        while cur < end:
            nxt = min(cur + timedelta(days=14), end)
            bins.append((cur, nxt))
            cur = nxt

        # ── Compute NDVI & LSWI for every scene in parallel ──────────────────
        import concurrent.futures
        
        def process_item(item):
            scene_dt = dt.strptime(item.properties["datetime"][:10], "%Y-%m-%d")
            ndvi = _ndvi_from_aws(item, req.geometry)
            lswi = _lswi_from_aws(item, req.geometry)
            return {
                "date": scene_dt,
                "ndvi": round(ndvi, 3),
                "lswi": round(lswi, 3)
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            scene_data = list(executor.map(process_item, items))
        
        scene_data.sort(key=lambda x: x["date"])

        # ── Aggregate into bins ───────────────────────────────────────────
        bin_ndvi: dict[int, list] = defaultdict(list)
        bin_lswi: dict[int, list] = defaultdict(list)
        
        for entry in scene_data:
            scene_dt = entry["date"]
            for idx, (b_start, b_end) in enumerate(bins):
                if b_start <= scene_dt < b_end:
                    bin_ndvi[idx].append(entry["ndvi"])
                    bin_lswi[idx].append(entry["lswi"])
                    break
                    
        labels, peak_ndvi, peak_lswi = [], [], []
        for idx, (b_start, b_end) in enumerate(bins):
            n_vals = bin_ndvi.get(idx, [])
            l_vals = bin_lswi.get(idx, [])
            label = f"{b_start.strftime('%b %d')}–{(b_end - timedelta(days=1)).strftime('%d')}"
            labels.append(label)
            
            peak_ndvi.append(round(max(n_vals), 3) if n_vals else None)
            peak_lswi.append(round(max(l_vals), 3) if l_vals else None)

        all_ndvi = [v for v in peak_ndvi if v is not None]
        return {
            "labels":      labels,
            "peak_ndvi":   peak_ndvi,
            "peak_lswi":   peak_lswi,
            "season_peak": round(max(all_ndvi), 3) if all_ndvi else None,
            "season_min":  round(min(all_ndvi), 3) if all_ndvi else None,
        }

    except Exception as e:
        print("Time series error:", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze-plot")
def analyze_plot(req: PolygonRequest):
    """
    Run full analysis: fetch best recent scene from AWS, compute zonal stats,
    then generate a Gemini advisory.
    """
    try:
        from phenology import extract_phenology_from_rasters
        geom = req.geometry

        if req.scene_id:
            catalog = pystac_client.Client.open(AWS_STAC_URL)
            items = [catalog.get_collection(COLLECTION).get_item(req.scene_id)]
        else:
            # Pull most-recent low-cloud scene (last 90 days)
            from datetime import timedelta
            end_date   = datetime.utcnow().strftime("%Y-%m-%d")
            start_date = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")

            catalog = pystac_client.Client.open(AWS_STAC_URL)
            items = _get_best_item(catalog, geom, start_date, end_date)

        if items and items[0]:
            item = items[0]
            mean_ndvi = _ndvi_from_aws(item, geom)
            mean_lswi = _lswi_from_aws(item, geom)
            soil_proxy = round(max(0.0, 0.8 - mean_ndvi), 3)  # inverse proxy
        else:
            mean_ndvi, mean_lswi, soil_proxy = 0.55, 0.25, 0.35

        phenology = extract_phenology_from_rasters(DATA_DIR, geom, reference_date=(items[0].properties["datetime"] if items else None))

        phenology = extract_phenology_from_rasters(DATA_DIR, geom, reference_date=(items[0].properties["datetime"] if items else None))

        # ── RAG Context Retrieval ──
        context = ""
        if vectorstore:
            try:
                # Query vectorstore based on metrics description
                query = f"NDVI is {mean_ndvi}, LSWI is {mean_lswi}, soil moisture is {soil_proxy}"
                relevant_docs = vectorstore.similarity_search(query, k=2)
                context = "\n".join([d.page_content for d in relevant_docs])
            except Exception as e:
                print(f"RAG Retrieval Error: {e}")

        if overall_chain:
            try:
                # Run the Sequential RAG Chain
                chain_results = overall_chain({
                    "context": context,
                    "ndvi": round(mean_ndvi, 2),
                    "lswi": round(mean_lswi, 2),
                    "soil_moisture": round(soil_proxy, 2),
                    "sowing_date": phenology["sowing_date"],
                    "harvest_date": phenology["harvest_date"]
                })
                advisory = chain_results["agronomy_advisory"]
                insurance_advisory = chain_results["insurance_advisory"]
            except Exception as e:
                print(f"Chain Error: {e}")
                advisory = "Error generating advisory with RAG pipeline."
                insurance_advisory = "Insurance assessment failed."
        else:
            advisory = "[Google AI Config Error] Farmer Advisory: Based on NDVI={:.2f}, crop appears {}.".format(
                mean_ndvi, "healthy" if mean_ndvi > 0.4 else "stressed"
            )
            insurance_advisory = "Insurance data currently unavailable."

        return {
            "status": "success",
            "metrics": {
                "mean_ndvi": round(mean_ndvi, 3),
                "mean_lswi": round(mean_lswi, 3),
                "soil_moisture_proxy": round(soil_proxy, 3),
                **phenology
            },
            "advisory": advisory,
            "insurance_advisory": insurance_advisory
        }

    except Exception as e:
        print("Analyze error:", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/list-rasters")
async def list_rasters():
    tif_files = glob.glob(os.path.join(DATA_DIR, '*.tif'))
    return {"files": [os.path.basename(f) for f in tif_files]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
