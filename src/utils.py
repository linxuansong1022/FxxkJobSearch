"""
工具函数

- LaTeX 特殊字符转义
- 职位去重哈希计算
- 文本清洗
"""

import hashlib
import re


def escape_latex(text: str) -> str:
    """
    转义 LaTeX 特殊字符，防止编译错误。

    需要转义的字符: & % $ # _ { } ~ ^ \\
    注意：这个函数应在 Jinja2 渲染前对所有动态文本调用。
    """
    # 转义映射（顺序重要：先转义反斜杠）
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def compute_job_hash(company: str, title: str) -> str:
    """
    计算职位去重哈希。

    normalize: 小写 + 去除多余空格 + 去除标点
    hash: SHA256 前16位（足够区分）
    """
    normalized = f"{_normalize(company)}|{_normalize(title)}"
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _normalize(text: str) -> str:
    """文本标准化：小写、去标点、压缩空格"""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)  # 去标点
    text = re.sub(r"\s+", " ", text)     # 压缩空格
    return text


def clean_html(text: str) -> str:
    """
    简单的HTML标签清理。
    用于清洗 JD 中可能残留的 HTML 标签。
    """
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean
