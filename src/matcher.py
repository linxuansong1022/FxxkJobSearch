"""
技能向量匹配模块

职责：
- 从 profile.yaml 加载用户所有经历的 bullet points
- 使用 Vertex AI Embeddings 将 bullet points 和 JD 需求向量化
- 通过余弦相似度选出 Top-N 最相关的经历

原理：
  如果 JD 强调 "API development"，系统自动选取 "FastAPI" 相关经历；
  如果 JD 强调 "data analysis"，则选取 "Pandas/NumPy" 相关经历。
  无需向量数据库，numpy 内存计算即可（数据量 < 100 条）。
"""

import logging
from pathlib import Path

import numpy as np
import yaml
from vertexai.language_models import TextEmbeddingModel

import config

logger = logging.getLogger(__name__)


# ============================================================
# Profile 数据加载
# ============================================================

def load_profile_bullets(profile_path: Path = None) -> list[dict]:
    """
    从 profile.yaml 加载所有经历 bullet points。

    Returns:
        [
            {"text": "bullet原文", "source": "company/project名", "category": "experience/project"},
            ...
        ]
    """
    path = profile_path or config.PROFILE_PATH
    with open(path, "r", encoding="utf-8") as f:
        profile = yaml.safe_load(f)

    bullets = []

    # 加载工作经历
    for exp in profile.get("experiences", []):
        for bullet in exp.get("bullets", []):
            bullets.append({
                "text": bullet,
                "source": exp.get("company", ""),
                "role": exp.get("role", ""),
                "category": "experience",
            })

    # 加载项目经历
    for proj in profile.get("projects", []):
        for bullet in proj.get("bullets", []):
            bullets.append({
                "text": bullet,
                "source": proj.get("name", ""),
                "role": proj.get("role", ""),
                "category": "project",
            })

    logger.info(f"加载了 {len(bullets)} 条经历 bullet points")
    return bullets


def load_profile(profile_path: Path = None) -> dict:
    """加载完整的 profile.yaml 数据"""
    path = profile_path or config.PROFILE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================
# 向量嵌入
# ============================================================

def embed_texts(texts: list[str]) -> np.ndarray:
    """
    使用 Vertex AI Embeddings 批量生成文本向量。

    Args:
        texts: 待嵌入的文本列表

    Returns:
        shape=(len(texts), 768) 的 numpy 数组
    """
    model = TextEmbeddingModel.from_pretrained(config.EMBEDDING_MODEL)

    # Vertex AI 批量嵌入有数量限制，分批处理
    batch_size = 50
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        embeddings = model.get_embeddings(batch)
        all_embeddings.extend([e.values for e in embeddings])

    return np.array(all_embeddings)


# ============================================================
# 余弦相似度匹配
# ============================================================

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    计算两组向量之间的余弦相似度。

    Args:
        a: shape=(m, d)  — 经历向量
        b: shape=(n, d)  — JD需求向量

    Returns:
        shape=(m, n) 的相似度矩阵
    """
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a_norm @ b_norm.T


def match_bullets_to_jd(
    bullets: list[dict],
    jd_analysis: dict,
    top_n: int = None,
) -> list[dict]:
    """
    核心匹配函数：从用户经历中选出与 JD 最相关的 Top-N 条。

    流程：
    1. 将 JD 的 hard_skills 拼接为查询文本
    2. 将所有 bullet texts 向量化
    3. 计算余弦相似度
    4. 返回 Top-N（按相似度降序）

    Args:
        bullets: load_profile_bullets() 的返回值
        jd_analysis: analyzer.py 的输出（包含 hard_skills 等）
        top_n: 选取数量，默认读 config.TOP_N_BULLETS

    Returns:
        排序后的 Top-N bullets（原始 dict + similarity 分数）
    """
    top_n = top_n or config.TOP_N_BULLETS

    # 构造 JD 查询文本：兼容 Gemini 可能输出的多种字段名
    hard_skills = (
        jd_analysis.get("hard_skills")
        or jd_analysis.get("required_skills")
        or jd_analysis.get("skills")
        or []
    )
    domain = (
        jd_analysis.get("company_domain")
        or jd_analysis.get("industry")
        or jd_analysis.get("description_keywords", [""])[0]
        or ""
    )
    # 如果 hard_skills 里的元素也可能是很长的描述，截取前几个词
    skill_texts = [s if len(s) < 50 else s[:50] for s in hard_skills]
    query_text = f"{domain}: {', '.join(skill_texts)}"

    if not query_text.strip(": "):
        logger.warning("JD 分析中无技能信息，无法匹配")
        return bullets[:top_n]  # 降级：返回前N条

    # 向量化
    bullet_texts = [b["text"] for b in bullets]
    all_texts = bullet_texts + [query_text]
    embeddings = embed_texts(all_texts)

    bullet_embeddings = embeddings[:-1]   # shape=(N, 768)
    query_embedding = embeddings[-1:]     # shape=(1, 768)

    # 计算相似度
    similarities = cosine_similarity(bullet_embeddings, query_embedding).flatten()

    # 排序并选取 Top-N
    ranked_indices = np.argsort(similarities)[::-1][:top_n]

    results = []
    for idx in ranked_indices:
        bullet = bullets[idx].copy()
        bullet["similarity"] = float(similarities[idx])
        results.append(bullet)

    logger.info(f"匹配完成: 查询='{query_text}', Top-{top_n} 相似度范围 [{results[-1]['similarity']:.3f}, {results[0]['similarity']:.3f}]")
    return results
