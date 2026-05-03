---
# Fixture metadata (stripped by loader)
planted_issues:
  - hardcoded_api_key: "WEATHER_API_KEY is hardcoded as a string literal"
  - unbounded_list: "cities parameter from user input is iterated without length limit"
  - missing_content_type: "No Content-Type validation on incoming request"
  - untested_error_handling: "ConnectionError branch is correct but has no test coverage"
clean_distractors:
  - "The retry logic with exponential backoff is correctly implemented"
  - "The response schema is well-structured"
  - "The timeout parameter on requests.get is appropriate"
---

```diff
diff --git a/src/api/weather.py b/src/api/weather.py
new file mode 100644
index 0000000..a1b2c3d
--- /dev/null
+++ b/src/api/weather.py
@@ -0,0 +1,62 @@
+"""Weather aggregation endpoint for multi-city forecasts."""
+
+import logging
+import time
+
+import requests
+from flask import Blueprint, jsonify, request
+
+logger = logging.getLogger(__name__)
+
+weather_bp = Blueprint("weather", __name__, url_prefix="/api/weather")
+
+WEATHER_API_KEY = "sk-weather-4f8a2b1c9d3e7f6a5b0c8d2e1f4a7b3c"
+WEATHER_BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
+MAX_RETRIES = 3
+
+
+def fetch_with_retry(url: str, params: dict, retries: int = MAX_RETRIES) -> dict | None:
+    """Fetch URL with exponential backoff retry.
+
+    Args:
+        url: The endpoint URL.
+        params: Query parameters.
+        retries: Number of retry attempts.
+
+    Returns:
+        Parsed JSON response or None on failure.
+    """
+    for attempt in range(retries):
+        try:
+            resp = requests.get(url, params=params, timeout=10)
+            resp.raise_for_status()
+            return resp.json()
+        except requests.exceptions.ConnectionError:
+            logger.error("Connection failed for %s (attempt %d/%d)", url, attempt + 1, retries)
+            if attempt < retries - 1:
+                time.sleep(2 ** attempt)
+        except requests.exceptions.HTTPError as exc:
+            logger.error("HTTP error for %s: %s", url, exc)
+            return None
+    return None
+
+
+@weather_bp.route("/forecast", methods=["POST"])
+def get_forecast():
+    """Return weather forecasts for a list of cities."""
+    data = request.get_json(force=True)
+    cities = data.get("cities", [])
+
+    results = []
+    for city in cities:
+        weather = fetch_with_retry(
+            WEATHER_BASE_URL,
+            {"q": city, "appid": WEATHER_API_KEY, "units": "metric"},
+        )
+        if weather:
+            results.append({
+                "city": city,
+                "temp": weather.get("main", {}).get("temp"),
+                "description": weather.get("weather", [{}])[0].get("description"),
+            })
+        else:
+            results.append({"city": city, "temp": None, "description": "unavailable"})
+
+    return jsonify({"forecasts": results}), 200
diff --git a/tests/test_weather.py b/tests/test_weather.py
new file mode 100644
index 0000000..b2c3d4e
--- /dev/null
+++ b/tests/test_weather.py
@@ -0,0 +1,25 @@
+import pytest
+from unittest.mock import patch, MagicMock
+
+from src.api.weather import fetch_with_retry, weather_bp
+
+
+@pytest.fixture
+def client(app):
+    app.register_blueprint(weather_bp)
+    return app.test_client()
+
+
+def test_forecast_single_city(client):
+    mock_resp = MagicMock()
+    mock_resp.json.return_value = {
+        "main": {"temp": 22.5},
+        "weather": [{"description": "clear sky"}],
+    }
+    mock_resp.raise_for_status.return_value = None
+
+    with patch("src.api.weather.requests.get", return_value=mock_resp):
+        resp = client.post("/api/weather/forecast", json={"cities": ["London"]})
+        assert resp.status_code == 200
+        data = resp.get_json()
+        assert len(data["forecasts"]) == 1
+        assert data["forecasts"][0]["temp"] == 22.5
```
