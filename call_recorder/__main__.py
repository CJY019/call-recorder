"""Launch the desktop recorder with ``python -m call_recorder``."""

import sys
import traceback


def launch() -> None:
    try:
        from call_recorder.ui import main

        main()
    except Exception as exc:
        # pythonw has no console; report even dependency/import failures.
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                "录音工具无法启动。\n\n" + str(exc) + "\n\n请检查运行环境，或查看使用说明中的故障排查。",
                "通话录音",
                0x10,
            )
        traceback.print_exc()
        raise


if __name__ == "__main__":
    launch()
