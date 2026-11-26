import pickle, sys, collections
S = pickle.load(open('/tmp/uboot-delays/scripts/state.pkl', 'rb'))
K = S['KEYS']; R = S['R']
def sig(lst): return [(n, r0, lp, lb) for (s, f, n, r0, lp, lb, reg, post) in lst]
def compare(a, b):
    A, B = K[a], K[b]; out = []
    onlyA = [k for k in A if k not in B]; onlyB = [k for k in B if k not in A]
    changed = [k for k in A if k in B and sig(A[k]) != sig(B[k])]
    # pair unmatched keys whose delay signature is identical (function renamed / different strings or code shape)
    pairs = []
    for ka in list(onlyA):
        for kb in list(onlyB):
            if sig(A[ka]) == sig(B[kb]): pairs.append((ka, kb)); onlyA.remove(ka); onlyB.remove(kb); break
    return onlyA, onlyB, changed, pairs
if __name__ == '__main__':
    a, b = sys.argv[1], sys.argv[2]
    oa, ob, ch, pr = compare(a, b)
    def show(t, k):
        for (s, f, n, r0, lp, lb, reg, post) in K[t][k]: print(f'     {t} {s:#x} in {f:#x} {n} r0={r0} loop={lp} bound={lb} {reg} post_logo={post}')
    print('only in', a); [ (print('  key', k[:4]), show(a, k)) for k in oa]
    print('only in', b); [ (print('  key', k[:4]), show(b, k)) for k in ob]
    print('changed'); [ (print('  key', k[:4]), show(a, k), show(b, k)) for k in ch]
    print('same delays, different function key:', len(pr))
    for ka, kb in pr: print('  ', ka[:2], '<->', kb[:2], [hex(x[1]) for x in K[a][ka]][:1], [hex(x[1]) for x in K[b][kb]][:1])
