"""Invariantes do split held-in/held-out (``hermes/platform/evals/holdout_split.py``).

O contrato é o que torna o held-out útil: a pertença de um caso é função SÓ de
``(seed, case_id)`` — logo o split é reproduzível, insensível à ordem da entrada e
não remaneja os casos antigos quando a coleção cresce. E o veredito só aceita quando
os dois conjuntos têm evidência comparável e nenhum caso regrediu em nenhum deles.
"""

from __future__ import annotations

from hermes.platform.evals.holdout_split import (
    HELD_IN,
    HELD_OUT,
    HoldoutSplit,
    bucket_of,
    decide_acceptance,
    split_cases,
)


def test_split_is_reproducible_order_insensitive_and_stable_under_growth():
    ids = [f"caso-{i:03d}" for i in range(200)]
    first = split_cases(ids, seed=7)

    # Mesma entrada + mesma seed ⇒ mesmo split; a ordem da coleção não influencia.
    assert first == split_cases(ids, seed=7)
    assert first == split_cases(reversed(ids), seed=7)
    assert first == split_cases(list(reversed(ids)) + ids[:5], seed=7)  # repetidos idem

    # Disjunção e cobertura: todo caso conhecido está em exatamente um conjunto.
    assert not set(first.held_in) & set(first.held_out)
    assert set(first.held_in) | set(first.held_out) == set(ids)
    assert first.held_in and first.held_out  # nenhum lado vazio (split não degenerado)

    # Crescer a coleção NÃO remaneja casos antigos — é isso que deixa o held-out de
    # hoje ser comparável com o de ontem.
    grown = split_cases(ids + [f"novo-{i:03d}" for i in range(60)], seed=7)
    assert set(first.held_in) <= set(grown.held_in)
    assert set(first.held_out) <= set(grown.held_out)
    assert all(grown.set_of(cid) == first.set_of(cid) for cid in ids)

    # Seed diferente = outro sorteio (o hash não é degenerado em um único conjunto).
    other = split_cases(ids, seed=8)
    assert set(other.held_out) != set(first.held_out)
    assert set(other.held_in) | set(other.held_out) == set(ids)
    # A pertença é o bucket do caso e nada mais — é o que garante a estabilidade.
    assert all(
        other.is_held_out(cid) == (bucket_of(cid, other.seed) < other.holdout_fraction)
        for cid in ids
    )


def test_acceptance_requires_no_regression_in_either_set_and_names_the_evidence():
    ids = [f"caso-{i:03d}" for i in range(120)]
    split = split_cases(ids, seed=11)
    held_in_case, held_out_case = split.held_in[0], split.held_out[0]
    baseline = {cid: {"passed": True, "score": 1.0} for cid in ids}

    # Candidato idêntico: aceito, com a cobertura dos dois conjuntos registrada.
    verdict = decide_acceptance(baseline, dict(baseline), split)
    assert verdict.accepted is True
    assert verdict.regressions == ()
    assert verdict.checked_held_in == len(split.held_in)
    assert verdict.checked_held_out == len(split.held_out)

    # Regressão só no held-in (o conjunto que o otimizador VÊ) → recusa com evidência.
    after = {**baseline, held_in_case: {"passed": False, "score": 0.0}}
    verdict = decide_acceptance(baseline, after, split)
    assert verdict.accepted is False
    assert [r.case_id for r in verdict.held_in_regressions] == [held_in_case]
    assert verdict.held_in_regressions[0].set_name == HELD_IN
    assert verdict.held_out_regressions == ()
    assert held_in_case in verdict.reason and "held_in" in verdict.reason

    # Regressão só no held-out (que o otimizador NÃO vê) → recusa. É o ponto do gate:
    # o conjunto reservado existe para pegar exatamente esta regressão.
    after = {**baseline, held_out_case: {"passed": False, "score": 0.0}}
    verdict = decide_acceptance(baseline, after, split)
    assert verdict.accepted is False
    assert verdict.held_in_regressions == ()
    assert [r.case_id for r in verdict.held_out_regressions] == [held_out_case]
    assert verdict.held_out_regressions[0].set_name == HELD_OUT
    assert held_out_case in verdict.reason and "held_out" in verdict.reason
    assert held_out_case in " ".join(verdict.evidence_lines())

    # Ganho amplo no held-in não compra uma regressão no held-out.
    improved = {
        cid: {"passed": cid != held_out_case, "score": 0.0 if cid == held_out_case else 2.0}
        for cid in ids
    }
    verdict = decide_acceptance(baseline, improved, split)
    assert verdict.accepted is False
    assert [r.case_id for r in verdict.held_out_regressions] == [held_out_case]

    # Caso que sumiu do candidato conta como regressão: apagar o caso que media a
    # falha é a forma mais barata de "melhorar" a nota.
    dropped = {cid: baseline[cid] for cid in ids if cid != held_out_case}
    verdict = decide_acceptance(baseline, dropped, split)
    assert verdict.accepted is False
    assert [r.case_id for r in verdict.held_out_regressions] == [held_out_case]
    assert "ausente" in verdict.held_out_regressions[0].reason

    # Queda de score é regressão só além da tolerância; a forma lista do EvalRunner
    # (hermes/platform/evals/runner.py) é aceita como entrada.
    halved = {cid: {"passed": True, "score": 0.5} for cid in ids}
    assert decide_acceptance(baseline, halved, split).accepted is False
    assert decide_acceptance(baseline, halved, split, tolerance=0.5).accepted is True
    rows = [{"case_id": cid, "passed": True, "score": 1.0} for cid in ids]
    assert decide_acceptance(rows, rows, split).accepted is True

    # Aceitar exige evidência dos DOIS lados: sem held-out não há como detectar
    # regressão nova, e um "aceito" seria carimbo.
    no_holdout = HoldoutSplit(held_in=tuple(ids), held_out=(), seed=1, holdout_fraction=0.3)
    verdict = decide_acceptance(baseline, dict(baseline), no_holdout)
    assert verdict.accepted is False
    assert HELD_OUT in verdict.reason
    # Idem quando o conjunto existe mas nenhum caso dele tem baseline para comparar.
    verdict = decide_acceptance({held_in_case: baseline[held_in_case]}, dict(baseline), split)
    assert verdict.accepted is False
    assert set(split.held_out) <= set(verdict.unbaselined)
