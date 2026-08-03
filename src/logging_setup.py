"""结构化日志设置：控制台 INFO + 文件 DEBUG 双通道。

用法:
    from logging_setup import get_logger
    logger = get_logger()
    logger.info("...")
"""
import logging
import sys
from pathlib import Path


_logger: logging.Logger | None = None


def setup_logging(run_dir: Path | None = None) -> logging.Logger:
    """初始化日志系统。run_dir 若提供，DEBUG 级日志写入该目录下的 run.log。"""
    global _logger

    logger = logging.getLogger("mtpromoter")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # 控制台通道：INFO+
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "[%(levelname)s] %(asctime)s %(message)s", datefmt="%H:%M:%S"
    ))
    logger.addHandler(console)

    # 文件通道：DEBUG+（含行号定位）
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            str(run_dir / "run.log"), encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s:%(lineno)d %(message)s"
        ))
        logger.addHandler(file_handler)

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    """获取 mtpromoter logger；若未调用 setup_logging 则返回默认 logger。"""
    global _logger
    if _logger is not None:
        return _logger
    return logging.getLogger("mtpromoter")
