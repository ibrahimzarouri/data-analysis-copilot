import os
import re

from openai import OpenAI

from agent.prompts import SYSTEM_PROMPT
from tools.code_executor import E2BSandbox, execute_code

_MAX_RETRIES = 2
_CODE_BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def _extract_code(content: str) -> str | None:
    m = _CODE_BLOCK.search(content)
    return m.group(1).strip() if m else None


def _preamble(content: str) -> str:
    """Text before the first code block."""
    idx = content.find("```")
    return content[:idx].strip() if idx != -1 else content.strip()


def _clean_explanation(text: str) -> str:
    """Remove any code blocks the model included despite instructions."""
    cleaned = _CODE_BLOCK.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


class DataAnalystAgent:
    def __init__(self, df, df_info: dict, sandbox: "E2BSandbox | None" = None):
        self.df = df
        self.df_info = df_info
        self.sandbox = sandbox
        self.history: list[dict] = []
        self.client = OpenAI(
            api_key=os.getenv("API_KEY"),
            base_url=os.getenv("BASE_URL", "https://chat-ai.academiccloud.de/v1"),
        )
        self.model = os.getenv("MODEL", "qwen3-coder-next")

    def _system(self) -> str:
        return SYSTEM_PROMPT.format(
            shape=f"{self.df_info['rows']:,} rows × {self.df_info['cols']} columns",
            columns_info=self.df_info["columns_info"],
        )

    def _complete(self, messages: list, temperature: float = 0.1) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    def chat(self, user_message: str) -> dict:
        self.history.append({"role": "user", "content": user_message})
        messages = [{"role": "system", "content": self._system()}, *self.history]

        content = self._complete(messages)
        code = _extract_code(content)
        result = {"explanation": "", "figure_bytes": None, "table": None, "code": None}

        if not code:
            result["explanation"] = content
            self.history.append({"role": "assistant", "content": content})
            return result

        result["code"] = code
        exec_result = self._run_with_retry(code)

        context_parts = []
        if exec_result.get("figure_bytes"):
            context_parts.append("A chart was successfully rendered.")
        if exec_result.get("table") is not None:
            context_parts.append(
                f"A result table with {len(exec_result['table'])} rows was computed:\n"
                f"{exec_result['table'].to_string(max_rows=10)}"
            )
        if exec_result.get("output"):
            context_parts.append(f"Output:\n{exec_result['output']}")
        if exec_result.get("error"):
            context_parts.append(f"Error:\n{exec_result['error']}")

        output = "\n\n".join(context_parts) or "Code ran with no output."

        # Store only the preamble (not the raw code) in history
        self.history.append({"role": "assistant", "content": _preamble(content)})
        self.history.append({
            "role": "user",
            "content": (
                f"[Execution result]\n{output}\n\n"
                "Explain what the results reveal in 2-4 sentences. "
                "Plain language only — no code, no code blocks."
            ),
        })

        explanation = _clean_explanation(self._complete(
            [{"role": "system", "content": self._system()}, *self.history],
            temperature=0.3,
        ))
        self.history.append({"role": "assistant", "content": explanation})

        result["explanation"] = explanation
        result["figure_bytes"] = exec_result.get("figure_bytes")
        result["table"] = exec_result.get("table")
        return result

    def _run_with_retry(self, code: str) -> dict:
        for attempt in range(_MAX_RETRIES + 1):
            exec_result = execute_code(code, self.df, self.sandbox)
            exec_result["code"] = code

            if not exec_result["error"] or attempt == _MAX_RETRIES:
                return exec_result

            # Ask model to fix the broken code
            fix_messages = [
                {"role": "system", "content": self._system()},
                *self.history,
                {
                    "role": "user",
                    "content": (
                        f"The code raised an error:\n{exec_result['error']}\n\n"
                        "Fix it and return the corrected code in a ```python block."
                    ),
                },
            ]
            fixed = self._complete(fix_messages)
            new_code = _extract_code(fixed)
            if new_code:
                code = new_code

        return exec_result

    def reset(self):
        self.history.clear()
