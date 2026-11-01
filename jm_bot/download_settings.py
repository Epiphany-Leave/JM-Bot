DEFAULT_DOWNLOAD_SETTINGS = {
    "mode": "direct", "concurrency": 3, "connect_timeout": 8,
    "image_timeout": 45, "max_sources": 3, "stall_timeout": 150, "job_timeout": 1800,
}


def download_settings(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict) or value.keys() - DEFAULT_DOWNLOAD_SETTINGS.keys():
        raise ValueError("下载设置包含未知字段或格式错误")
    result = {**DEFAULT_DOWNLOAD_SETTINGS, **value}
    if result["mode"] not in ("direct", "mihomo"):
        raise ValueError("下载模式必须是 direct 或 mihomo")
    limits = {"concurrency": 5, "connect_timeout": 60, "image_timeout": 300,
              "max_sources": 6, "stall_timeout": 1800, "job_timeout": 7200}
    for key, maximum in limits.items():
        if type(result[key]) is not int or not 1 <= result[key] <= maximum:
            raise ValueError(f"{key} 必须是 1 到 {maximum} 的整数")
    if result["image_timeout"] < result["connect_timeout"]:
        raise ValueError("图片总超时不能小于连接超时")
    return result
