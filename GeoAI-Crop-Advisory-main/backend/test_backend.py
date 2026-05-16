import pytest
from fastapi.testclient import TestClient
from main import app
from phenology import extract_phenology_from_rasters
import os

client = TestClient(app)

# Dummy polygon for testing
TEST_POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [78.5, 25.5],
        [78.6, 25.5],
        [78.6, 25.6],
        [78.5, 25.6],
        [78.5, 25.5]
    ]]
}

def test_analyze_plot_endpoint():
    response = client.post("/analyze-plot", json={"geometry": TEST_POLYGON})
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "success"
    assert "metrics" in data
    assert "advisory" in data
    
    metrics = data["metrics"]
    assert "mean_ndvi" in metrics
    assert "sowing_date" in metrics

def test_extract_phenology_from_rasters():
    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'sentinel2_samples')
    phenology = extract_phenology_from_rasters(data_dir, TEST_POLYGON)
    assert "sowing_date" in phenology
    assert "harvest_date" in phenology

