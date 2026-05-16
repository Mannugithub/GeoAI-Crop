import requests
import json

url = "http://localhost:8001/fetch-stac"
data = {
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[78.56, 25.44], [78.57, 25.44], [78.57, 25.45], [78.56, 25.45], [78.56, 25.44]]]
    },
    "start_date": "2023-10-01",
    "end_date": "2024-04-30",
    "cloud_cover": 20
}

try:
    resp = requests.post(url, json=data)
    print(json.dumps(resp.json(), indent=2))
except Exception as e:
    print(e)
