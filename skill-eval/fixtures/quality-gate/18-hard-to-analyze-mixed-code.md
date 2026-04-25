## Completed Work

```diff
diff --git a/src/reports/query_builder.py b/src/reports/query_builder.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/reports/query_builder.py
@@ -0,0 +1,68 @@
+import re
+import logging
+from datetime import datetime, timedelta
+
+from src.db import db
+
+logger = logging.getLogger(__name__)
+
+# Pattern to extract metric names from report templates
+METRIC_PATTERN = re.compile(
+    r'\{\{metric:(?P<name>[a-z_]+)'
+    r'(?::(?P<window>(?:\d+[dhms])+))?'
+    r'(?:\|(?P<agg>sum|avg|min|max|p\d{1,2}))?'
+    r'(?:#(?P<filter>[a-z_]+=\w+(?:,[a-z_]+=\w+)*))?'
+    r'\}\}'
+)
+
+
+def parse_metric_references(template: str) -> list[dict]:
+    """Extract metric references from a report template string."""
+    metrics = []
+    for match in METRIC_PATTERN.finditer(template):
+        metric = {
+            "name": match.group("name"),
+            "window": match.group("window") or "24h",
+            "aggregation": match.group("agg") or "avg",
+        }
+        raw_filter = match.group("filter")
+        if raw_filter:
+            metric["filters"] = dict(
+                pair.split("=") for pair in raw_filter.split(",")
+            )
+        metrics.append(metric)
+    return metrics
+
+
+def build_report_query(metric_name: str, window: str, agg: str, filters: dict | None = None) -> str:
+    """Build a SQL query for a report metric.
+
+    Constructs a window-based aggregation query against the metrics table.
+    """
+    window_seconds = _parse_window(window)
+    cutoff = datetime.utcnow() - timedelta(seconds=window_seconds)
+
+    base_query = f"""
+        SELECT
+            date_trunc('hour', recorded_at) AS bucket,
+            {agg}(value) AS agg_value,
+            count(*) AS sample_count
+        FROM metrics
+        WHERE metric_name = '{metric_name}'
+          AND recorded_at >= '{cutoff.isoformat()}'
+    """
+
+    if filters:
+        for key, value in filters.items():
+            base_query += f"  AND {key} = '{value}'\n"
+
+    base_query += """
+        GROUP BY bucket
+        ORDER BY bucket DESC
+    """
+    return base_query
+
+
+def _parse_window(window: str) -> int:
+    """Parse a window string like '24h' or '7d' into seconds."""
+    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
+    total = 0
+    for match in re.finditer(r"(\d+)([dhms])", window):
+        total += int(match.group(1)) * units[match.group(2)]
+    return total


diff --git a/src/api/report_routes.py b/src/api/report_routes.py
index bbb2222..ccc3333 100644
--- a/src/api/report_routes.py
+++ b/src/api/report_routes.py
@@ -1,5 +1,7 @@
 import logging
 from flask import Blueprint, request, jsonify, g
+from src.reports.query_builder import parse_metric_references, build_report_query
+from src.db import db
 
 logger = logging.getLogger(__name__)
 reports_bp = Blueprint("reports", __name__)
@@ -12,4 +14,18 @@ def get_report_templates():
     return jsonify(templates), 200
 
 
+@reports_bp.route("/reports/generate", methods=["POST"])
+@require_auth
+def generate_custom_report():
+    """Generate a report from a user-provided template string."""
+    data = request.get_json()
+    template = data.get("template", "")
+    metrics = parse_metric_references(template)
+    results = {}
+    for metric in metrics:
+        query = build_report_query(metric["name"], metric["window"], metric["aggregation"], metric.get("filters"))
+        rows = db.session.execute(query).fetchall()
+        results[metric["name"]] = [dict(row._mapping) for row in rows]
+    return jsonify(results), 200
+
+
 @reports_bp.route("/reports/scheduled", methods=["GET"])
```

## Project Context

This is a Flask web application with a reporting module. The query builder constructs SQL for time-series metric aggregation. The regex handles a custom template syntax for embedding metric references in report templates. Review all aspects of the code — do not skip sections that are harder to analyze.
