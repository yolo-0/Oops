from mcp.mock_data import ORDERS, LOGISTICS
import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

async def query_order(order_id: str) -> Any:
    """查询订单详情"""

    order = ORDERS.get(order_id)
    # Mock data
    await asyncio.sleep(0.5) 
    if not order:
        return {"success": False, "error": f"未找到订单 {order_id}，请核实订单号"}
    return {"success": True,
     "order": {
        "order_id": order["order_id"],
        "user": order["user"],
        "status": order["status"],
        "items": order["items"],
        "total": order["total"],
        "created_at": order["created_at"],
        "shipped_at": order["shipped_at"],
        "tracking_number": order["tracking_number"],
        "carrier": order["carrier"],
        "estimated_delivery": order["estimated_delivery"],
    }}


async def query_logistics(order_id: str) -> Any:
    """查询物流详情"""
    order = ORDERS.get(order_id)
    if not order:
        return {"success": False, "error": f"未找到订单 {order_id}，请核实订单号"}

    tracking_number = order.get("tracking_number")
    if not tracking_number:
        return {"success": False, "error": "该订单尚未发货，暂无物流信息"}

    logistics = LOGISTICS.get(tracking_number)
    if not logistics:
        return {"success": False, "error": f"物流单号 {tracking_number} 暂无轨迹信息"}

    return {"success": True, 
    "logistics": {
        "tracking_number": logistics["tracking_number"],
        "carrier": logistics["carrier"],
        "status": logistics["status"],
        "events": logistics["events"],
    }}