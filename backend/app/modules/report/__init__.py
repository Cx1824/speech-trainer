"""报告模块对外接口。"""

from app.modules.report.generator import generate_report, get_report
from app.modules.report.manual_analysis import build_manual_analysis_package

__all__ = ["build_manual_analysis_package", "generate_report", "get_report"]
