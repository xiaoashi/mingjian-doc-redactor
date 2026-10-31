from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="明鉴文档敏感信息识别与脱敏系统")
    parser.add_argument("--project", type=Path, help="打开已有项目目录")
    parser.add_argument("--config-root", type=Path, help="指定应用配置目录")
    parser.add_argument("--smoke-test", action="store_true", help="打开真实窗口并于 1 秒后退出")
    parser.add_argument("--self-check", type=Path, help="在指定目录创建合成诊断项目，执行三格式完整链路并退出")
    args = parser.parse_args()
    from PySide6.QtCore import QStandardPaths, QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from mingjian.reporting.writers import font_path
    from mingjian.domain.models import VERSION
    from mingjian.ui.window import MainWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Mingjian")
    app.setOrganizationName("Mingjian")
    app.setApplicationVersion(VERSION)
    font_id = QFontDatabase.addApplicationFont(str(font_path()))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0], 11))
    if args.self_check:
        from mingjian.app.selfcheck import selfcheck
        return 0 if selfcheck(args.self_check) else 1
    root = args.config_root or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    window = MainWindow(root)
    if args.project:
        window.safe(lambda: window.load_project(args.project))
    window.show()
    if args.smoke_test:
        QTimer.singleShot(1000, app.quit)
    result = app.exec()
    if window.project and not window.project.db.closed:
        window.project.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
