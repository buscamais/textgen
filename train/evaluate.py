# -*- coding: utf-8 -*-
from functools import partial

from collections import Counter
import string
import re
import math
import copy

from utils.writer import ResultWriter

"""
Below codes are originally from TextVAE, multiwords branch, evaluate.py
Some parts are modified
"""

def normalize_answer(s, isRemoveArticle = True):
    """Lower text and remove punctuation, articles and extra whitespace."""
    def remove_articles(text):
        return re.sub(r'\b(a|an|the)\b', ' ', text)

    def white_space_fix(text):
        return ' '.join(text.split())

    def remove_punc(text):
        exclude = set(string.punctuation)
        return ''.join(ch for ch in text if ch not in exclude)

    def lower(text):
        return text.lower()

    if isRemoveArticle:
        return white_space_fix(remove_articles(remove_punc(lower(s))))
    else:
        return white_space_fix(remove_punc(lower(s)))

def f1_score(prediction, ground_truth):
    prediction_tokens = normalize_answer(prediction).split()
    ground_truth_tokens = normalize_answer(ground_truth).split()
    common = Counter(prediction_tokens) & Counter(ground_truth_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0
    precision = 1.0 * num_same / len(prediction_tokens)
    recall = 1.0 * num_same / len(ground_truth_tokens)
    f1 = (2 * precision * recall) / (precision + recall)
    return f1


def exact_match_score(prediction, ground_truth):
    return (normalize_answer(prediction) == normalize_answer(ground_truth))

def ngram(n, iterable):
    iterable = iter(iterable)
    window = [next(iterable) for _ in range(n)]
    while True:
        yield window
        window.append(next(iterable))
        window = window[1:]

def bleu_ngram(n, candidate, references):
    pred = [' '.join(window) for window in ngram(n, candidate)]
    truths = [[' '.join(window) for window in ngram(n, reference)]
              for reference in references]

    ref_counts = Counter()
    for truth in truths:
        ref_counts |= Counter(truth)

    common = Counter(pred) & ref_counts
    num_same = sum(common.values())

    if num_same == 0:
        return 0.0
    return num_same / len(pred)

def bleu_score(prediction, ground_truths, num_ngrams):
    prediction_tokens = normalize_answer(prediction).split()
    ground_truths_tokens = [normalize_answer(ground_truth).split()
                            for ground_truth in ground_truths]

    score = 0
    any_match = 0
    for i in range(1, num_ngrams + 1):
        precision = bleu_ngram(i, prediction_tokens, ground_truths_tokens)
        if precision > 0:
            any_match += 1
            score += math.log(precision)

    if any_match == 0:
        return 0.0

    # brevity penalty
    num_pred = len(prediction_tokens)
    num_truth = min(len(truth) for truth in ground_truths_tokens)
    if 1 <= num_pred <= num_truth:
        penalty = math.exp(1 - 1.0 * num_truth / num_pred)
    else:
        penalty = 1

    # applying geometric mean
    bleu = math.exp(score / num_ngrams)
    return bleu * penalty

def chunk(a, b):
    b = copy.deepcopy(b)
    c, u = 0, 0 # c: number of chunks, u: number of words associated with chunk

    # Find a common sequence (= a chunk)
    def _calc_common_length(x, y):
        n = 0
        for cx, cy in zip(x, y):
            if cx != cy:
                break
            n += 1
        return n

    def _find(corpus, x, start=0):
        for i, word in enumerate(corpus[start:]):
            if word == x:
                return start + i
        return -1

    for i in range(len(a)):
        max_len = 0
        pos = -1
        j = -1

        # Find a common longest sequence
        while True:
            j = _find(b, a[i], j + 1)
            if j < 0:
                break
            common_len = _calc_common_length(a[i:], b[j:])
            if common_len > max_len:
                pos = j
                max_len = common_len

        # replace empty sentence ([0])
        if pos >= 0:
            b[pos:pos+max_len] = [0]
            c += 1
            u += max_len
    return c, u


def meteor_score(prediction, ground_truth):
    # According to the paper of METEOR, stemming process is required.
    prediction_tokens = normalize_answer(prediction).split()
    ground_truth_tokens = normalize_answer(ground_truth).split()

    common = Counter(prediction_tokens) & Counter(ground_truth_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0

    precision = 1.0 * num_same / len(prediction_tokens)
    recall = 1.0 * num_same / len(ground_truth_tokens)
    fmean = 10.0 * precision * recall / (recall + 9 * precision)
    c, u = chunk(prediction_tokens, ground_truth_tokens)
    frag = 1.0 * c / u
    penalty = 0.5 * (frag ** 3)
    return fmean * (1 - penalty)

def metric_max_over_ground_truths(metric_fn, prediction, ground_truths):
    scores_for_ground_truths = []
    for ground_truth in ground_truths:
        score = metric_fn(prediction, ground_truth)
        scores_for_ground_truths.append(score)
    return max(scores_for_ground_truths)


# ref_dir : reference file directory. ex) '/path/to/directory/file.txt'
# predictions : list[string]. for example, ["he is a boy", "she went home"]
# output : for example, {'meteor': 8.3064, 'bleu': 28.43696, 'em': 0.0, 'f1': 14.9253}
# sample usage : print('ae eval:', eval.simple_evaluate(real_data, ae_data))
def evaluate_sents(reference, predictions):
    metrics = { # p = pred, g = ref
        'em': lambda p, g: metric_max_over_ground_truths(exact_match_score, p, g),
        'f1': lambda p, g: metric_max_over_ground_truths(f1_score, p, g),
        'bleu': lambda p, g: bleu_score(p, g, 4),
        'meteor': lambda p, g: metric_max_over_ground_truths(meteor_score, p, g),
    }
    scores = { k: 0 for k in metrics }
    total = 0
    for ref, pred in zip(reference, predictions):
        total += 1
        for k in metrics:
            scores[k] += metrics[k](pred, [ref])
    for k in metrics:
        scores[k] = 100.0 * scores[k] / total
    return ResultWriter('Evaluation', scores)

# references : list[string]. for example, ["he is a boy", "she went home"]
# predictions : list[string]. for example, ["he is a boy", "she went home"]
# output : for example, {'meteor': 8.3064, 'bleu': 28.43696, 'em': 0.0, 'f1': 14.9253}
# sample usage : print('ae eval:', eval.simple_evaluate(real_data, ae_data))
def simple_evaluate(references, predictions):
    metrics = { # p = pred, g = ref
        'em': lambda p, g: metric_max_over_ground_truths(exact_match_score, p, g),
        'f1': lambda p, g: metric_max_over_ground_truths(f1_score, p, g),
        'bleu': lambda p, g: bleu_score(p, g, 4),
        'meteor': lambda p, g: metric_max_over_ground_truths(meteor_score, p, g),
    }

    scores = { k: 0 for k in metrics }
    total = 0
    for ref, pred in zip(references, predictions):
        total += 1
        for k in metrics:
            scores[k] += metrics[k](pred, [ref])
    for k in metrics:
        scores[k] = 100.0 * scores[k] / total
    return scores
