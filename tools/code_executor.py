import base64
import io
import os
import traceback
from contextlib import redirect_stdout

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

# ── E2B sandbox ───────────────────────────────────────────────────────────────

_SANDBOX_SETUP = """
import pandas as pd, numpy as np, base64, io, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

df = pd.read_csv('/home/user/data.csv')
for col in df.columns:
    if not (pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_datetime64_any_dtype(df[col])):
        try:
            df[col] = pd.to_datetime(df[col])
        except Exception:
            pass
print(f"Loaded: {df.shape[0]} rows x {df.shape[1]} cols")
"""

# Appended after user code to capture figure and result table
_CAPTURE = """
try:
    import matplotlib.pyplot as _plt, io as _io, base64 as _b64
    if _plt.get_fignums():
        _buf = _io.BytesIO()
        _plt.gcf().savefig(_buf, format='png', bbox_inches='tight', dpi=150)
        with open('/home/user/_fig.b64', 'w') as _f:
            _f.write(_b64.b64encode(_buf.getvalue()).decode('ascii'))
        _plt.show()
        _plt.close('all')
except Exception as _e:
    print(f"[figure capture error: {_e}]")
try:
    if 'result' in dir() and hasattr(result, 'to_csv'):
        result.to_csv('/home/user/_result.csv', index=False)
except Exception:
    pass
"""


class E2BSandbox:
    _TIMEOUT = 600  # seconds of inactivity before E2B kills the sandbox

    def __init__(self, df: pd.DataFrame):
        self._df = df
        self._start()

    def _start(self):
        from e2b_code_interpreter import Sandbox as _Sandbox
        self._sbx = _Sandbox.create(timeout=self._TIMEOUT)
        self._sbx.files.write("/home/user/data.csv", self._df.to_csv(index=False))
        self._sbx.run_code(_SANDBOX_SETUP)

    def run(self, code: str) -> dict:
        from e2b.exceptions import SandboxException

        try:
            # Reset the inactivity timer so the sandbox survives long chats
            self._sbx.set_timeout(self._TIMEOUT)
            execution = self._sbx.run_code(code + "\n" + _CAPTURE)
        except SandboxException:
            # Sandbox expired while idle — spin up a fresh one and retry once
            self._start()
            execution = self._sbx.run_code(code + "\n" + _CAPTURE)

        result = {
            "output": "\n".join(execution.logs.stdout),
            "figure_bytes": None,
            "table": None,
            "error": None,
            "code": code,
        }

        stderr = "\n".join(execution.logs.stderr)
        if stderr.strip():
            result["error"] = stderr

        # Figure via base64 file (more reliable than inline PNG)
        try:
            b64 = self._sbx.files.read("/home/user/_fig.b64")
            if b64:
                result["figure_bytes"] = base64.b64decode(b64.strip())
                self._sbx.run_code(
                    "import os; os.path.exists('/home/user/_fig.b64') and os.remove('/home/user/_fig.b64')"
                )
        except Exception:
            # Fallback: inline PNG from execution results
            for r in execution.results:
                if hasattr(r, "png") and r.png:
                    result["figure_bytes"] = base64.b64decode(r.png)
                    break

        # Result table
        try:
            csv = self._sbx.files.read("/home/user/_result.csv")
            result["table"] = pd.read_csv(io.StringIO(csv))
            self._sbx.run_code(
                "import os; os.path.exists('/home/user/_result.csv') and os.remove('/home/user/_result.csv')"
            )
        except Exception:
            pass

        return result

    def close(self):
        try:
            self._sbx.kill()
        except Exception:
            pass


# ── Local fallback ────────────────────────────────────────────────────────────

def _local_exec(code: str, df: pd.DataFrame) -> dict:
    plt.close("all")
    buf = io.StringIO()
    namespace = {"df": df.copy(), "pd": pd, "np": np, "plt": plt, "sns": sns}
    result = {"output": "", "figure_bytes": None, "table": None, "error": None, "code": code}

    try:
        with redirect_stdout(buf):
            exec(code, namespace)  # noqa: S102
        result["output"] = buf.getvalue()

        if plt.get_fignums():
            fig = plt.gcf()
            img_buf = io.BytesIO()
            fig.savefig(img_buf, format="png", bbox_inches="tight", dpi=150)
            img_buf.seek(0)
            result["figure_bytes"] = img_buf.getvalue()
            plt.close("all")

        for var in ("result", "result_df", "output_df"):
            if var in namespace and isinstance(namespace[var], pd.DataFrame):
                result["table"] = namespace[var]
                break

    except Exception:
        result["error"] = traceback.format_exc()
        result["output"] = result["error"]

    return result


# ── Public interface ──────────────────────────────────────────────────────────

def execute_code(code: str, df: pd.DataFrame, sandbox: "E2BSandbox | None" = None) -> dict:
    if sandbox is not None:
        return sandbox.run(code)
    return _local_exec(code, df)
