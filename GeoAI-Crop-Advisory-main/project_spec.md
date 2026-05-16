# Project Specification: GeoAI Advisory Portal

## Objective
Calculate NDVI, fAPAR, Soil Moisture Proxy, and Phenology from local Sentinel-2 data, then feed it to Gemini for agricultural advisory.

## Tech Stack
- **Backend:** FastAPI
- **Geospatial Processing:** Rasterio, GeoPandas, Shapely
- **Intelligence:** LangChain, Google Generative AI (Gemini 3.1 Pro)
- **Frontend:** HTML, CSS, JavaScript, Leaflet.js, Leaflet.draw

## Architecture Flow
1. User draws a polygon on the Leaflet map in the Frontend.
2. Frontend sends the GeoJSON polygon to the FastAPI Backend.
3. Backend clips local Sentinel-2 rasters (`data/sentinel2_samples/`) to the polygon.
4. Backend calculates mean NDVI, fAPAR, Soil Moisture proxy.
5. Backend extracts a time-series of Max NDVI to detect 'Green-up' (sowing date) and 'Senescence' (harvest date).
6. Raster statistics are passed to a LangChain pipeline acting as an expert Agronomist.
7. Gemini generates an agricultural advisory based on the metrics.
8. Backend returns the metrics and advisory as JSON to the Frontend.
9. Frontend displays the results in a side panel.
