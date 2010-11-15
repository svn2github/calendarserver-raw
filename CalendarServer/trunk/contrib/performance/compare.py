import sys

import stats

from benchlib import load_stats

try:
    from scipy.stats import ttest_1samp
except ImportError:
    from math import pi
    from ctypes import CDLL, c_double
    for lib in ['libc.dylib', 'libm.so']:
        try:
            libc = CDLL(lib)
        except OSError:
            pass
        else:
            break
    gamma = libc.tgamma
    gamma.argtypes = [c_double]
    gamma.restype = c_double
    def ttest_1samp(a, popmean):
        # T statistic - http://mathworld.wolfram.com/Studentst-Distribution.html
        t = (stats.mean(a) - popmean) / (stats.stddev(a) / len(a) ** 0.5)
        v = len(a) - 1.0
        p = gamma((v + 1) / 2) / ((v * pi) ** 0.5 * gamma(v / 2)) * (1 + t ** 2 / v) ** (-(v + 1) / 2)
        return (t, p)


def trim(sequence, amount):
    sequence.sort()
    n = len(sequence)
    t = int(n * amount / 2.0)
    if t:
        del sequence[:t]
        del sequence[-t:]
    else:
        raise RuntimeError(
            "Cannot trim length %d sequence by %d%%" % (n, int(amount * 100)))
    return sequence


def main():
    [(stat, first), (stat, second)] = load_stats(sys.argv[1:])

    # Attempt to increase robustness by dropping the outlying 10% of values.
    first = trim(first, 0.1)
    second = trim(second, 0.1)

    fmean = stats.mean(first)
    smean = stats.mean(second)
    p = ttest_1samp(second, fmean)[1]
    if p >= 0.95:
        # rejected the null hypothesis
        print sys.argv[1], 'mean of', fmean, 'differs from', sys.argv[2], 'mean of', smean, '(%2.0f%%)' % (p * 100,)
    else:
        # failed to reject the null hypothesis
        print 'cannot prove means (%s, %s) differ (%2.0f%%)' % (fmean, smean, p * 100,)
