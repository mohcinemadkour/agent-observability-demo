"""Tools for a predictive-maintenance agent, with fault injection driven by the active scenario."""
from __future__ import annotations

import json
import logging
import os
import random
import time

from langchain_core.tools import tool

from . import scenarios

log = logging.getLogger("agent.tools")
SLOW_SECONDS = float(os.getenv("DEMO_SLOW_SECONDS", "4"))

ASSETS = {
    "CNC-07": {"type": "5-axis CNC mill", "line": "Line 2", "criticality": "A", "hours_since_service": 3110},
    "CNC-12": {"type": "3-axis CNC mill", "line": "Line 1", "criticality": "B", "hours_since_service": 1420},
    "L-03": {"type": "CNC lathe", "line": "Line 3", "criticality": "B", "hours_since_service": 2280},
}

READINGS = {
    "CNC-07": {"vibration_rms_mm_s": 11.8, "vibration_trend_7d": "+64%", "spindle_bearing_temp_c": 82,
               "iso_10816_zone": "D (damage likely)", "severity": "critical", "suspected_failure_mode": "spindle bearing wear"},
    "CNC-12": {"vibration_rms_mm_s": 3.1, "vibration_trend_7d": "+4%", "spindle_bearing_temp_c": 51,
               "iso_10816_zone": "B (acceptable)", "severity": "normal", "suspected_failure_mode": "none"},
    "L-03": {"coolant_temp_c": 41, "coolant_flow_lpm": 6.2, "nominal_flow_lpm": 11.0, "severity": "high",
             "suspected_failure_mode": "coolant pump degradation"},
}

MANUAL = {
    "spindle bearing wear": "PROC-114: Replace spindle bearings. Est. 6h, parts kit SB-220. Lock out spindle; schedule within 7 days when ISO zone D.",
    "coolant pump degradation": "PROC-087: Inspect/replace coolant pump impeller. Est. 2h, kit CP-11. Schedule within 14 days.",
}


class SensorGatewayError(RuntimeError):
    """Raised when the (simulated) sensor gateway is down."""


@tool
def get_asset_info(asset_id: str) -> str:
    """Look up an asset's type, production line, criticality and hours since last service."""
    time.sleep(random.uniform(0.05, 0.15))
    asset = ASSETS.get(asset_id.upper())
    if asset is None:
        log.warning("unknown asset requested", extra={"asset.id": asset_id})
        return json.dumps({"error": f"asset {asset_id} not found"})
    log.info("asset lookup ok", extra={"asset.id": asset_id})
    return json.dumps({"asset_id": asset_id.upper(), **asset})


@tool
def get_sensor_readings(asset_id: str, window_hours: int = 24) -> str:
    """Get condition-monitoring readings (vibration, temperatures, flow) for an asset over a time window."""
    scenario = scenarios.current()
    time.sleep(random.uniform(0.1, 0.3))
    if scenario == "tool_error":
        log.error("sensor gateway unavailable", extra={"asset.id": asset_id, "http.response.status_code": 503})
        raise SensorGatewayError("sensor-gateway returned HTTP 503 Service Unavailable")
    if scenario == "loop":
        log.warning("partial sensor window returned", extra={"asset.id": asset_id, "window_hours": window_hours})
        return json.dumps({"status": "incomplete", "message": "Partial window received, data may be stale. Retry the same request."})
    data = READINGS.get(asset_id.upper())
    if data is None:
        return json.dumps({"error": f"no sensors registered for {asset_id}"})
    log.info("sensor readings fetched", extra={"asset.id": asset_id, "severity": data["severity"]})
    return json.dumps({"asset_id": asset_id.upper(), "window_hours": window_hours, **data})


@tool
def search_maintenance_manual(failure_mode: str) -> str:
    """Search the maintenance manual for the repair procedure for a failure mode."""
    if scenarios.current() == "slow_tool":
        log.warning("manual search backend slow", extra={"latency_s": SLOW_SECONDS})
        time.sleep(SLOW_SECONDS)
    else:
        time.sleep(random.uniform(0.1, 0.25))
    for key, proc in MANUAL.items():
        if key in failure_mode.lower():
            return proc
    return "No procedure found. Escalate to reliability engineering."


@tool
def create_service_order(asset_id: str, failure_mode: str, priority: str, due_in_days: int) -> str:
    """Create a proactive service order in the CMMS. priority is one of: low, medium, high, critical."""
    time.sleep(random.uniform(0.1, 0.2))
    order_id = f"SO-{random.randint(40000, 49999)}"
    log.info("service order created", extra={"asset.id": asset_id, "order.id": order_id, "priority": priority})
    return json.dumps({"order_id": order_id, "asset_id": asset_id, "failure_mode": failure_mode,
                       "priority": priority, "due_in_days": due_in_days, "status": "scheduled"})


TOOLS = [get_asset_info, get_sensor_readings, search_maintenance_manual, create_service_order]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
