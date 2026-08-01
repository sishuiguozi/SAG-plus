class JobPaused(Exception):
    """协作式暂停信号；不是失败，不触发重试。"""
