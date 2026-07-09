import io
import pandas as pd
from typing import Optional


def load_data(uploaded_file) -> tuple[Optional[pd.DataFrame], dict]:
    try:
        name = uploaded_file.name.lower()
        if name.endswith(".csv"):
            content = uploaded_file.read()
            df = None
            for enc in ["utf-8", "latin-1", "iso-8859-1"]:
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                return None, {"error": "Could not decode CSV file"}
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded_file)
        else:
            return None, {"error": "Unsupported file type"}

        df = _try_parse_dates(df)
        return df, _build_info(df)

    except Exception as e:
        return None, {"error": str(e)}


def _try_parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        if any(kw in col.lower() for kw in ("date", "time", "created", "updated", "timestamp")):
            try:
                df[col] = pd.to_datetime(df[col])
            except Exception:
                pass
    return df


def _build_info(df: pd.DataFrame) -> dict:
    lines = []
    numeric_cols, categorical_cols, datetime_cols = [], [], []

    for col in df.columns:
        s = df[col]
        n_null = int(s.isna().sum())
        n_unique = int(s.nunique())

        if pd.api.types.is_datetime64_any_dtype(s):
            datetime_cols.append(col)
            lines.append(f"  - {col} (datetime, {n_null} nulls) {s.min()} → {s.max()}")
        elif pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            numeric_cols.append(col)
            lines.append(
                f"  - {col} (numeric, min={s.min():.4g}, max={s.max():.4g}, {n_null} nulls)"
            )
        else:
            categorical_cols.append(col)
            samples = s.dropna().unique()[:5].tolist()
            lines.append(f"  - {col} (text, {n_unique} unique, {n_null} nulls) e.g. {samples}")

    return {
        "rows": len(df),
        "cols": len(df.columns),
        "columns_info": "\n".join(lines),
        "numeric_cols": numeric_cols,
        "categorical_cols": categorical_cols,
        "datetime_cols": datetime_cols,
        "missing_total": int(df.isna().sum().sum()),
    }


def build_welcome_summary(df: pd.DataFrame, info: dict) -> str:
    lines = [
        f"Dataset loaded: **{info['rows']:,} rows × {info['cols']} columns**",
        "",
    ]

    if info["missing_total"] > 0:
        pct = info["missing_total"] / (info["rows"] * info["cols"]) * 100
        lines.append(f"Missing values: {info['missing_total']:,} ({pct:.1f}% of all cells)")
    else:
        lines.append("No missing values.")

    lines.append("")
    lines.append(f"**Numeric columns ({len(info['numeric_cols'])}):** {', '.join(info['numeric_cols']) or 'none'}")
    lines.append(f"**Text columns ({len(info['categorical_cols'])}):** {', '.join(info['categorical_cols']) or 'none'}")
    if info["datetime_cols"]:
        lines.append(f"**Date columns ({len(info['datetime_cols'])}):** {', '.join(info['datetime_cols'])}")

    lines.append("")
    lines.append("Ask me anything about your data — I'll write and run the code for you.")

    return "\n".join(lines)


def suggest_questions(info: dict) -> list[str]:
    questions = ["Show me the first 10 rows"]
    if info["numeric_cols"]:
        questions.append("Show descriptive statistics for all numeric columns")
        questions.append(f"Plot the distribution of {info['numeric_cols'][0]}")
    if info["missing_total"] > 0:
        questions.append("Which columns have missing values and how many?")
    if info["categorical_cols"]:
        questions.append(f"What are the most common values in {info['categorical_cols'][0]}?")
    if len(info["numeric_cols"]) >= 2:
        questions.append(f"Is there a correlation between {info['numeric_cols'][0]} and {info['numeric_cols'][1]}?")
    if info["datetime_cols"] and info["numeric_cols"]:
        questions.append(f"Plot {info['numeric_cols'][0]} over time")
    return questions[:5]
