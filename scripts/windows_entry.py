"""Frozen entry point; all assets and profiles remain in the existing root."""
def run():
    try:
        from src.app import main
        return main()
    except Exception:
        import sys,traceback
        from pathlib import Path
        root=Path(sys.executable).resolve().parent
        path=root/'debug/rc1/windows_build/startup_error.txt';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(traceback.format_exc(),encoding='utf-8')
        raise

if __name__=='__main__':
    raise SystemExit(run())
