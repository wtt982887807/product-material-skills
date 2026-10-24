#!/usr/bin/env python3
"""Run with any Python >=3.11; no installed-project path or cwd assumptions."""
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/src'))
if sys.version_info<(3,11):
    raise SystemExit('Python >=3.11 is required. Run python -m pip install -r <skill-dir>/requirements.txt')
try:
    from material_cli import main
    main()
except ModuleNotFoundError as error:
    print(json.dumps({'error':str(error),'action':'Use this same Python to install '+str(ROOT/'requirements.txt')},ensure_ascii=False),file=sys.stderr)
    raise SystemExit(2)
except (ValueError,OSError) as error:
    print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr)
    raise SystemExit(2)
