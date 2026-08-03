"""重试工具：指数退避重试装饰器，用于瞬时故障恢复。"""
import functools
import time
import traceback


def retry_on_failure(max_attempts: int = 3, base_delay: float = 1.0,
                     backoff: float = 2.0, exceptions=(Exception,)):
    """装饰器：遇到指定异常时指数退避重试。

    Args:
        max_attempts: 最大尝试次数（含首次）
        base_delay: 首次重试延迟（秒）
        backoff: 延迟倍增因子
        exceptions: 触发重试的异常类型元组
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        # 获取函数名用于日志
                        func_name = getattr(func, '__name__', str(func))
                        print(f"  [RETRY] {func_name} 第{attempt}次失败: {e}. "
                              f"{delay:.1f}s 后重试 (共{max_attempts}次)...")
                        time.sleep(delay)
                        delay *= backoff
                    else:
                        print(f"  [FAIL] {func.__name__} 重试{max_attempts}次全部失败",
                              flush=True)
                        traceback.print_exc()
            raise last_exc
        return wrapper
    return decorator
