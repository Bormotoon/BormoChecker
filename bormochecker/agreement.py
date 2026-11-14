"""Вычисление интер-разметочного согласия (Cohen's Kappa).
T409 / [E02]: Согласие разметчиков для верификации качества эталона.
"""

from __future__ import annotations


def cohen_kappa(rater1: list[str], rater2: list[str]) -> float:
    """Вычисляет коэффициент каппа Коэна между двумя разметчиками."""
    if len(rater1) != len(rater2):
        raise ValueError(f"Длины списков разметки должны совпадать: {len(rater1)} != {len(rater2)}")
    n = len(rater1)
    if n == 0:
        return 1.0

    categories = sorted(set(rater1) | set(rater2))
    cat_to_idx = {c: i for i, c in enumerate(categories)}
    num_cats = len(categories)

    matrix = [[0] * num_cats for _ in range(num_cats)]
    for r1, r2 in zip(rater1, rater2):
        matrix[cat_to_idx[r1]][cat_to_idx[r2]] += 1

    po = sum(matrix[i][i] for i in range(num_cats)) / n

    pe = 0.0
    for i in range(num_cats):
        row_sum = sum(matrix[i][j] for j in range(num_cats))
        col_sum = sum(matrix[j][i] for j in range(num_cats))
        pe += (row_sum * col_sum) / (n * n)

    if pe >= 1.0:
        return 1.0
    return round((po - pe) / (1.0 - pe), 4)
