"""电商 Mock 数据：订单、商品、物流"""

ORDERS = {
    "ORD-20240115-001": {
        "order_id": "ORD-20240115-001",
        "user": "小明",
        "status": "shipped",
        "items": [
            {"name": "Nike Air Max 270 运动鞋", "sku": "SHOE-270-BK-42", "quantity": 1, "price": 899.00}
        ],
        "total": 899.00,
        "created_at": "2024-01-15 10:30:00",
        "shipped_at": "2024-01-16 14:20:00",
        "tracking_number": "SF1234567890",
        "carrier": "顺丰速运",
        "estimated_delivery": "2024-01-19",
    },  
    "ORD-20240120-002": {
        "order_id": "ORD-20240120-002",
        "user": "小红",
        "status": "pending",
        "items": [
            {"name": "Apple AirPods Pro 2", "sku": "ELEC-APP-002", "quantity": 1, "price": 1799.00},
            {"name": "AirPods 保护壳（透明）", "sku": "ACC-AP-CASE-01", "quantity": 1, "price": 29.90},
        ],
        "total": 1828.90,
        "created_at": "2024-01-20 09:15:00",
        "shipped_at": None,
        "tracking_number": None,
        "carrier": None,
        "estimated_delivery": None,
    },
    "ORD-20240110-003": {
        "order_id": "ORD-20240110-003",
        "user": "大壮",
        "status": "delivered",
        "items": [
            {"name": "小米14 Ultra 手机", "sku": "PHONE-MI14U-BK", "quantity": 1, "price": 5999.00}
        ],
        "total": 5999.00,
        "created_at": "2024-01-10 16:00:00",
        "shipped_at": "2024-01-11 08:00:00",
        "tracking_number": "JD9876543210",
        "carrier": "京东物流",
        "estimated_delivery": "2024-01-13",
        "delivered_at": "2024-01-13 11:30:00",
    },
    "ORD-20240118-004": {
        "order_id": "ORD-20240118-004",
        "user": "小丽",
        "status": "refund_processing",
        "items": [
            {"name": "Levi's 501 经典牛仔裤", "sku": "CLOTH-LEVI-501-30", "quantity": 1, "price": 699.00}
        ],
        "total": 699.00,
        "created_at": "2024-01-18 12:00:00",
        "shipped_at": "2024-01-19 09:00:00",
        "tracking_number": "YT6655443322",
        "carrier": "圆通速递",
        "estimated_delivery": "2024-01-22",
        "delivered_at": "2024-01-21 15:00:00",
        "refund_reason": "尺码不合适",
        "refund_status": "审核中",
        "refund_requested_at": "2024-01-22 10:00:00",
    },
    "ORD-20240122-005": {
        "order_id": "ORD-20240122-005",
        "user": "阿杰",
        "status": "pending",
        "items": [
            {"name": "戴森 V15 吸尘器", "sku": "HOME-DYSON-V15", "quantity": 1, "price": 4299.00},
            {"name": "戴森 V15 替换滤芯", "sku": "HOME-DYSON-FLTR", "quantity": 2, "price": 199.00},
        ],
        "total": 4697.00,
        "created_at": "2024-01-22 20:00:00",
        "shipped_at": None,
        "tracking_number": None,
        "carrier": None,
        "estimated_delivery": None,
    },
}

LOGISTICS = {
    "SF1234567890": {
        "tracking_number": "SF1234567890",
        "carrier": "顺丰速运",
        "status": "in_transit",
        "events": [
            {"time": "2024-01-16 14:20", "location": "深圳南山区", "description": "快件已揽收"},
            {"time": "2024-01-17 06:00", "location": "广州转运中心", "description": "已到达"},
            {"time": "2024-01-17 22:00", "location": "上海转运中心", "description": "已到达"},
            {"time": "2024-01-18 08:30", "location": "上海浦东区", "description": "正在派送中"},
        ],
    },
    "JD9876543210": {
        "tracking_number": "JD9876543210",
        "carrier": "京东物流",
        "status": "delivered",
        "events": [
            {"time": "2024-01-11 08:00", "location": "北京亦庄仓库", "description": "快件已出库"},
            {"time": "2024-01-12 10:00", "location": "北京海淀区", "description": "正在派送中"},
            {"time": "2024-01-13 11:30", "location": "北京海淀区", "description": "已签收"},
        ],
    },
    "YT6655443322": {
        "tracking_number": "YT6655443322",
        "carrier": "圆通速递",
        "status": "delivered",
        "events": [
            {"time": "2024-01-19 09:00", "location": "杭州余杭区", "description": "快件已揽收"},
            {"time": "2024-01-20 06:00", "location": "杭州转运中心", "description": "已发出"},
            {"time": "2024-01-20 18:00", "location": "上海转运中心", "description": "已到达"},
            {"time": "2024-01-21 09:00", "location": "上海普陀区", "description": "正在派送中"},
            {"time": "2024-01-21 15:00", "location": "上海普陀区", "description": "已签收"},
        ],
    },
}