import sys
from pathlib import Path

root = Path(SPECPATH).parent
datas = [(str(root/'src/mingjian/resources'), 'mingjian/resources'),
         (str(root/'src/mingjian/web/static'), 'mingjian/web/static'),
         (str(root/'licenses'), 'licenses')]
a = Analysis([str(root/'src/mingjian/web/launcher.py')], pathex=[str(root/'src')], datas=datas,
             hiddenimports=['uvicorn.logging','uvicorn.loops.asyncio','uvicorn.protocols.http.h11_impl','uvicorn.lifespan.on'],
             excludes=['tkinter','PySide6','PyQt6','PyQt5','uvloop','httptools','websockets'],
             hookspath=[], hooksconfig={'matplotlib':{'backends':['Agg']}}, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='明鉴网页',debug=False,strip=False,upx=False,console=False,
          icon=str(root/'packaging/mingjian.icns') if sys.platform=='darwin' else None)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='明鉴网页')
if sys.platform=='darwin':
    app = BUNDLE(coll,name='明鉴网页.app',icon=str(root/'packaging/mingjian.icns'),
                 bundle_identifier='local.mingjian.web',version='1.1.0',
                 info_plist={'CFBundleDisplayName':'明鉴网页','LSUIElement':True,'LSMinimumSystemVersion':'11.0',
                             'NSHighResolutionCapable':True})
