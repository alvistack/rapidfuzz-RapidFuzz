import random
from itertools import product
from string import ascii_letters, digits, punctuation

import hypothesis.strategies as st
import numpy as np
import pytest
from hypothesis import assume, given, settings

from rapidfuzz import fuzz, process, utils
from rapidfuzz.distance import Indel as _Indel
from rapidfuzz.distance import Indel_cpp, Indel_py, JaroWinkler_cpp, JaroWinkler_py
from rapidfuzz.distance import Levenshtein as _Levenshtein
from rapidfuzz.distance import Levenshtein_cpp, Levenshtein_py

Levenshtein_cpp.distance._RF_ScorerPy = _Levenshtein.distance._RF_ScorerPy
Levenshtein_cpp.normalized_distance._RF_ScorerPy = (
    _Levenshtein.normalized_distance._RF_ScorerPy
)
Levenshtein_cpp.similarity._RF_ScorerPy = _Levenshtein.similarity._RF_ScorerPy
Levenshtein_cpp.normalized_similarity._RF_ScorerPy = (
    _Levenshtein.normalized_similarity._RF_ScorerPy
)
Levenshtein_py.distance._RF_ScorerPy = _Levenshtein.distance._RF_ScorerPy
Levenshtein_py.normalized_distance._RF_ScorerPy = (
    _Levenshtein.normalized_distance._RF_ScorerPy
)
Levenshtein_py.similarity._RF_ScorerPy = _Levenshtein.similarity._RF_ScorerPy
Levenshtein_py.normalized_similarity._RF_ScorerPy = (
    _Levenshtein.normalized_similarity._RF_ScorerPy
)

Indel_cpp.distance._RF_ScorerPy = _Indel.distance._RF_ScorerPy
Indel_cpp.normalized_distance._RF_ScorerPy = _Indel.normalized_distance._RF_ScorerPy
Indel_cpp.similarity._RF_ScorerPy = _Indel.similarity._RF_ScorerPy
Indel_cpp.normalized_similarity._RF_ScorerPy = _Indel.normalized_similarity._RF_ScorerPy
Indel_py.distance._RF_ScorerPy = _Indel.distance._RF_ScorerPy
Indel_py.normalized_distance._RF_ScorerPy = _Indel.normalized_distance._RF_ScorerPy
Indel_py.similarity._RF_ScorerPy = _Indel.similarity._RF_ScorerPy
Indel_py.normalized_similarity._RF_ScorerPy = _Indel.normalized_similarity._RF_ScorerPy


def isclose(a, b, rel_tol=1e-09, abs_tol=0.0):
    return abs(a - b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)


def levenshtein(s1, s2, weights=(1, 1, 1)):
    """
    python implementation of a generic Levenshtein distance
    this is much less error prone, than the bitparallel C implementations
    and is therefore used to test the C implementation
    However this makes this very slow even for testing purposes
    """

    rows = len(s1) + 1
    cols = len(s2) + 1
    insert, delete, substitute = weights

    dist = [[0 for x in range(cols)] for x in range(rows)]

    for row in range(1, rows):
        dist[row][0] = row * delete

    for col in range(1, cols):
        dist[0][col] = col * insert

    for col in range(1, cols):
        for row in range(1, rows):
            if s1[row - 1] == s2[col - 1]:
                cost = 0
            else:
                cost = substitute

            dist[row][col] = min(
                dist[row - 1][col] + delete,  # deletion
                dist[row][col - 1] + insert,  # insertion
                dist[row - 1][col - 1] + cost,  # substitution
            )

    return dist[-1][-1]


def normalize_distance(dist, s1, s2, weights=(1, 1, 1)):
    insert, delete, substitute = weights
    if len(s1) > len(s2):
        max_dist = min(
            [
                # delete all characters from s1 and insert all characters from s2
                len(s1) * delete + len(s2) * insert,
                # replace all characters and delete the remaining characters from s1
                len(s2) * substitute + (len(s1) - len(s2)) * delete,
            ]
        )
    else:
        max_dist = min(
            [
                # delete all characters from s1 and insert all characters from s2
                len(s1) * delete + len(s2) * insert,
                # replace all characters and insert the remaining characters into s1
                len(s1) * substitute + (len(s2) - len(s1)) * insert,
            ]
        )

    return 1 - 1 * float(dist) / float(max_dist) if max_dist else 1


def jarowinkler_similarity(*args, **kwargs):
    sim1 = JaroWinkler_py.similarity(*args, **kwargs)
    sim2 = JaroWinkler_cpp.similarity(*args, **kwargs)
    assert isclose(sim1, sim2)
    return sim1


def jaro_similarity(pattern, text):
    P_flag = [0] * (len(pattern) + 1)
    T_flag = [0] * (len(text) + 1)

    Bound = max(len(pattern), len(text)) // 2
    Bound = max(Bound - 1, 0)

    CommonChars = 0
    for i in range(len(text)):
        lowlim = i - Bound if i >= Bound else 0
        hilim = i + Bound if i + Bound <= len(pattern) - 1 else len(pattern) - 1

        for j in range(lowlim, hilim + 1):
            if not P_flag[j] and pattern[j] == text[i]:
                T_flag[i] = 1
                P_flag[j] = 1
                CommonChars += 1
                break

    if not CommonChars:
        return 0

    Transpositions = 0
    k = 0
    for i in range(len(text)):
        if T_flag[i]:
            j = k
            while j < len(pattern):
                if P_flag[j]:
                    k = j + 1
                    break
                j += 1

            if text[i] != pattern[j]:
                Transpositions += 1

    Transpositions = Transpositions // 2

    sim = (
        CommonChars / len(pattern)
        + CommonChars / len(text)
        + (CommonChars - Transpositions) / CommonChars
    )
    return sim / 3


def jaro_winkler_similarity(pattern, text, prefix_weight=0.1):
    min_len = min(len(pattern), len(text))
    prefix = 0
    max_prefix = min(min_len, 4)

    while prefix < max_prefix:
        if text[prefix] != pattern[prefix]:
            break
        prefix += 1

    Sim = jaro_similarity(pattern, text)
    if Sim > 0.7:
        Sim += prefix * prefix_weight * (1.0 - Sim)

    return Sim


def partial_ratio_short_needle_impl(s1, s2):
    if not s1 and not s2:
        return 100

    if not s1 or not s2:
        return 0

    if len(s1) > len(s2):
        return partial_ratio_short_needle_impl(s2, s1)
    parts = [
        s2[max(0, i) : min(len(s2), i + len(s1))] for i in range(-len(s1), len(s2))
    ]
    res = 0
    for part in parts:
        res = max(res, fuzz.ratio(s1, part))
    return res


def partial_ratio_short_needle(s1, s2):
    if len(s1) != len(s2):
        return partial_ratio_short_needle_impl(s1, s2)
    else:
        return max(
            partial_ratio_short_needle_impl(s1, s2),
            partial_ratio_short_needle_impl(s2, s1),
        )


def cdist_scorer(queries, choices, scorer):
    matrix = np.zeros((len(queries), len(choices)), dtype=np.uint8)

    for i, query in enumerate(queries):
        for j, choice in enumerate(choices):
            matrix[i, j] = scorer(query, choice)

    return matrix


def cdist_distance(queries, choices, scorer):
    matrix = np.zeros((len(queries), len(choices)), dtype=np.int32)

    for i, query in enumerate(queries):
        for j, choice in enumerate(choices):
            matrix[i, j] = scorer(query, choice)

    return matrix


def extractOne_scorer(s1, s2, scorer, processor=None, **kwargs):
    return process.extractOne(s1, [s2], processor=processor, scorer=scorer, **kwargs)[1]


def extract_scorer(s1, s2, scorer, processor=None, **kwargs):
    return process.extract(s1, [s2], processor=processor, scorer=scorer, **kwargs)[0][1]


def extract_iter_scorer(s1, s2, scorer, processor=None, **kwargs):
    return list(
        process.extract_iter(s1, [s2], processor=processor, scorer=scorer, **kwargs)
    )[0][1]


HYPOTHESIS_ALPHABET = ascii_letters + digits + punctuation

SCORERS = [
    fuzz.ratio,
    fuzz.partial_ratio,
    fuzz.token_set_ratio,
    fuzz.token_sort_ratio,
    fuzz.token_ratio,
    fuzz.partial_token_set_ratio,
    fuzz.partial_token_sort_ratio,
    fuzz.partial_token_ratio,
    fuzz.WRatio,
    fuzz.QRatio,
]

FULL_SCORERS = [fuzz.ratio, fuzz.WRatio, fuzz.QRatio]

PROCESSORS = [lambda x: x, utils.default_process]


@given(s1=st.text(), s2=st.text())
@settings(max_examples=100, deadline=None)
def test_matching_blocks(s1, s2):
    """
    test correct matching block conversion
    """
    ops = Levenshtein_cpp.editops(s1, s2)
    assert ops.as_matching_blocks() == ops.as_opcodes().as_matching_blocks()


@given(s1=st.text(), s2=st.text())
@settings(max_examples=100, deadline=None)
def test_levenshtein_editops(s1, s2):
    """
    test Levenshtein.editops with any sizes
    """
    ops = Levenshtein_cpp.editops(s1, s2)
    assert ops.apply(s1, s2) == s2


@given(s1=st.text(min_size=65), s2=st.text(min_size=65))
@settings(max_examples=50, deadline=None)
def test_levenshtein_editops_block(s1, s2):
    """
    test Levenshtein.editops for long strings
    """
    ops = Levenshtein_cpp.editops(s1, s2)
    assert ops.apply(s1, s2) == s2


@given(s1=st.text(), s2=st.text())
@settings(max_examples=100, deadline=None)
def test_indel_editops(s1, s2):
    """
    test Indel.editops with any sizes
    """
    ops = Indel_cpp.editops(s1, s2)
    assert ops.apply(s1, s2) == s2


@given(s1=st.text(min_size=65), s2=st.text(min_size=65))
@settings(max_examples=50, deadline=None)
def test_indel_editops_block(s1, s2):
    """
    test Indel.editops for long strings
    """
    ops = Indel_cpp.editops(s1, s2)
    assert ops.apply(s1, s2) == s2


@given(s1=st.text(max_size=64), s2=st.text())
@settings(max_examples=50, deadline=1000)
def test_partial_ratio_short_needle(s1, s2):
    """
    test partial_ratio for short needles (needle <= 64)
    """
    assert isclose(fuzz.partial_ratio(s1, s2), partial_ratio_short_needle(s1, s2))


@given(s1=st.text(), s2=st.text())
@settings(max_examples=50, deadline=1000)
def test_token_ratio(s1, s2):
    """
    token_ratio should be max(token_sort_ratio, token_set_ratio)
    """
    assert fuzz.token_ratio(s1, s2) == max(
        fuzz.token_sort_ratio(s1, s2), fuzz.token_set_ratio(s1, s2)
    )


@given(s1=st.text(), s2=st.text())
@settings(max_examples=50, deadline=1000)
def test_partial_token_ratio(s1, s2):
    """
    partial_token_ratio should be max(partial_token_sort_ratio, partial_token_set_ratio)
    """
    assert fuzz.partial_token_ratio(s1, s2) == max(
        fuzz.partial_token_sort_ratio(s1, s2), fuzz.partial_token_set_ratio(s1, s2)
    )


@given(s1=st.text(max_size=64), s2=st.text(max_size=64))
@settings(max_examples=50, deadline=None)
def test_levenshtein_word(s1, s2):
    """
    Test short Levenshtein implementation against simple implementation
    """
    # uniform Levenshtein
    # distance
    reference_dist = levenshtein(s1, s2)
    assert Levenshtein_cpp.distance(s1, s2) == reference_dist
    assert extractOne_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert extract_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert Levenshtein_py.distance(s1, s2) == reference_dist
    assert extractOne_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    assert extract_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    # normalized distance
    reference_sim = normalize_distance(reference_dist, s1, s2)
    assert isclose(
        extractOne_scorer(s1, s2, Levenshtein_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Levenshtein_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Levenshtein_cpp.normalized_similarity),
        reference_sim,
    )
    assert isclose(
        extractOne_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )

    # InDel-Distance
    # distance
    reference_dist = levenshtein(s1, s2, weights=(1, 1, 2))
    assert isclose(extractOne_scorer(s1, s2, Indel_cpp.distance), reference_dist)
    assert isclose(extract_scorer(s1, s2, Indel_cpp.distance), reference_dist)
    assert isclose(extract_iter_scorer(s1, s2, Indel_cpp.distance), reference_dist)
    assert isclose(extractOne_scorer(s1, s2, Indel_py.distance), reference_dist)
    assert isclose(extract_scorer(s1, s2, Indel_py.distance), reference_dist)
    assert isclose(extract_iter_scorer(s1, s2, Indel_py.distance), reference_dist)

    # normalized distance
    reference_sim = normalize_distance(reference_dist, s1, s2, weights=(1, 1, 2))
    assert isclose(
        extractOne_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extractOne_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )


@given(s1=st.text(min_size=65), s2=st.text(min_size=65))
@settings(max_examples=50, deadline=None)
def test_levenshtein_block(s1, s2):
    """
    Test blockwise Levenshtein implementation against simple implementation
    """
    # uniform Levenshtein
    # distance
    reference_dist = levenshtein(s1, s2)
    assert extractOne_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert extract_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert extractOne_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    assert extract_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    # normalized distance
    reference_sim = normalize_distance(reference_dist, s1, s2)
    assert isclose(
        extractOne_scorer(s1, s2, Levenshtein_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Levenshtein_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Levenshtein_cpp.normalized_similarity),
        reference_sim,
    )
    assert isclose(
        extractOne_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )

    # InDel-Distance
    # distance
    reference_dist = levenshtein(s1, s2, weights=(1, 1, 2))
    assert extractOne_scorer(s1, s2, Indel_cpp.distance) == reference_dist
    assert extract_scorer(s1, s2, Indel_cpp.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Indel_cpp.distance) == reference_dist
    assert extractOne_scorer(s1, s2, Indel_py.distance) == reference_dist
    assert extract_scorer(s1, s2, Indel_py.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Indel_py.distance) == reference_dist
    # normalized distance
    reference_sim = normalize_distance(reference_dist, s1, s2, weights=(1, 1, 2))
    assert isclose(
        extractOne_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extractOne_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )


@given(s1=st.text(), s2=st.text())
@settings(max_examples=50, deadline=None)
def test_levenshtein_random(s1, s2):
    """
    Test mixed strings to test through all implementations of Levenshtein
    """
    # uniform Levenshtein
    # distance
    reference_dist = levenshtein(s1, s2)
    assert extractOne_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert extract_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Levenshtein_cpp.distance) == reference_dist
    assert extractOne_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    assert extract_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Levenshtein_py.distance) == reference_dist
    # normalized distance
    reference_sim = normalize_distance(reference_dist, s1, s2)
    assert isclose(
        extractOne_scorer(s1, s2, Levenshtein_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Levenshtein_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Levenshtein_cpp.normalized_similarity),
        reference_sim,
    )
    assert isclose(
        extractOne_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Levenshtein_py.normalized_similarity), reference_sim
    )

    # InDel-Distance
    # distance
    reference_dist = levenshtein(s1, s2, weights=(1, 1, 2))
    assert extractOne_scorer(s1, s2, Indel_cpp.distance) == reference_dist
    assert extract_scorer(s1, s2, Indel_cpp.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Indel_cpp.distance) == reference_dist
    assert extractOne_scorer(s1, s2, Indel_py.distance) == reference_dist
    assert extract_scorer(s1, s2, Indel_py.distance) == reference_dist
    assert extract_iter_scorer(s1, s2, Indel_py.distance) == reference_dist
    # normalized distance
    reference_sim = normalize_distance(reference_dist, s1, s2, weights=(1, 1, 2))
    assert isclose(
        extractOne_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Indel_cpp.normalized_similarity), reference_sim
    )
    assert isclose(
        extractOne_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )
    assert isclose(
        extract_iter_scorer(s1, s2, Indel_py.normalized_similarity), reference_sim
    )


@given(sentence=st.text())
@settings(max_examples=50, deadline=1000)
def test_multiple_processor_runs(sentence):
    """
    Test that running a preprocessor on a sentence
    a second time does not change the result
    """
    assert utils.default_process(sentence) == utils.default_process(
        utils.default_process(sentence)
    )


@pytest.mark.parametrize("scorer,processor", list(product(FULL_SCORERS, PROCESSORS)))
@given(choices=st.lists(st.text(), min_size=1))
@settings(max_examples=50, deadline=1000)
def test_only_identical_strings_extracted(scorer, processor, choices):
    """
    Test that only identical (post processing) strings score 100 on the test.
    If two strings are not identical then using full comparison methods they should
    not be a perfect (100) match.
    :param scorer:
    :param processor:
    :param data:
    :return:
    """
    query = random.choice(choices)
    assume(processor(query) != "")

    matches = process.extract(
        query, choices, scorer=scorer, processor=processor, score_cutoff=100, limit=None
    )

    assert matches != []

    for match in matches:
        assert processor(query) == processor(match[0])


@given(queries=st.lists(st.text(), min_size=1), choices=st.lists(st.text(), min_size=1))
@settings(max_examples=50, deadline=1000)
def test_cdist(queries, choices):
    """
    Test that cdist returns correct results
    """

    reference_matrix = cdist_distance(queries, choices, scorer=Levenshtein_cpp.distance)
    matrix = process.cdist(queries, choices, scorer=Levenshtein_cpp.distance)
    assert (matrix == reference_matrix).all()

    reference_matrix = cdist_distance(queries, queries, scorer=Levenshtein_cpp.distance)
    matrix = process.cdist(queries, queries, scorer=Levenshtein_cpp.distance)
    assert (matrix == reference_matrix).all()


@given(s1=st.text(max_size=64), s2=st.text(max_size=64))
@settings(max_examples=50, deadline=1000)
def test_jaro_winkler_word(s1, s2):
    assert isclose(jaro_winkler_similarity(s1, s2), jarowinkler_similarity(s1, s2))


@given(s1=st.text(min_size=65), s2=st.text(min_size=65))
@settings(max_examples=50, deadline=1000)
def test_jaro_winkler_block(s1, s2):
    assert isclose(jaro_winkler_similarity(s1, s2), jarowinkler_similarity(s1, s2))


@given(s1=st.text(), s2=st.text())
@settings(max_examples=50, deadline=1000)
def test_jaro_winkler_random(s1, s2):
    print(s1, s2)
    assert isclose(jaro_winkler_similarity(s1, s2), jarowinkler_similarity(s1, s2))
