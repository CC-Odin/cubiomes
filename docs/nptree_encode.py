"""
Encode a DumpTree pre-order dump into a cubiomes tables/btreeXX.h header.

usage: nptree_encode.py DUMP BIOMES_H NAME TITLE [OUT]

DUMP is the output of DumpTree.java (see there for the complete procedure).

Layout follows docs/nptree_bin.py: nodes are stored in pre-order, each 64-bit
node holds 6 bytes indexing into a sorted table of unique (min,max) parameter
pairs (temperature in the lowest byte) and a 16-bit field that is either the
index of the first child (inner node) or 0xFF00 | biome id (leaf). Siblings are
found at a fixed stride per depth (steps[]), which this script verifies.
"""
import re
import sys


def biome_ids(path):
    src = open(path).read()
    body = re.search(r'enum BiomeID\s*\{(.*?)\};', src, re.S).group(1)
    body = re.sub(r'//[^\n]*', '', body)
    ids, cur = {}, -1
    for tok in body.split(','):
        tok = tok.strip()
        if not tok:
            continue
        m = re.fullmatch(r'(\w+)\s*(?:=\s*(.+))?', tok, re.S)
        name, expr = m.group(1), m.group(2)
        if expr is None:
            cur += 1
        else:
            expr = re.sub(r'[A-Za-z_]\w*', lambda x: str(ids[x.group(0)]), expr)
            cur = eval(expr, {'__builtins__': {}})
        ids[name] = cur
    return ids


class Node:
    __slots__ = ('biome', 'ranges', 'children', 'idx', 'depth')


def parse(path):
    it = iter(open(path))

    def rec(depth):
        f = next(it).split()
        n = Node()
        n.depth = depth
        v = list(map(int, f[2:]))
        n.ranges = [(v[2*i], v[2*i+1]) for i in range(6)]  # drop offset
        n.children = []
        n.biome = None
        if f[0] == 'N':
            n.children = [rec(depth + 1) for _ in range(int(f[1]))]
        else:
            n.biome = f[1]
        return n
    return rec(0)


def main():
    dump, biomes_h, name, title = sys.argv[1:5]
    out = open(sys.argv[5], 'w') if len(sys.argv) > 5 else sys.stdout
    ids = biome_ids(biomes_h)
    root = parse(dump)

    order = len(root.children)
    nodes = []
    def walk(n):
        n.idx = len(nodes)
        nodes.append(n)
        for c in n.children:
            walk(c)
    walk(root)
    order = max(len(n.children) for n in nodes)
    maxdepth = max(n.depth for n in nodes)

    # sibling stride per depth: every inner node at depth d (except the trailing
    # partial ones that the lookup handles by truncation) must space its
    # children by the same amount
    steps = []
    for d in range(maxdepth):
        strides = set()
        for n in nodes:
            if n.depth == d and len(n.children) > 1:
                for a, b in zip(n.children, n.children[1:]):
                    strides.add(b.idx - a.idx)
        steps.append(max(strides))
    steps.append(0)
    check_layout(nodes, steps, order)

    pairs = sorted({r for n in nodes for r in n.ranges})
    if len(pairs) > 256:
        sys.exit('too many unique parameter ranges: %d' % len(pairs))
    pidx = {p: i for i, p in enumerate(pairs)}

    w = out.write
    w('#include <inttypes.h>\n\n')
    w('enum {{ {}_order = {} }};\n\n'.format(name, order))
    w('static const uint32_t {}_steps[] = {{ {} }};\n\n'.format(name, ', '.join(map(str, steps))))
    w('static const int32_t {}_param[][2] =\n{{\n'.format(name))
    for i in range(0, len(pairs), 4):
        row = ''.join('{{{:6d},{:6d}}},'.format(*p) for p in pairs[i:i+4])
        if i + 4 <= len(pairs):
            w('    {} // {:02X}-{:02X}\n'.format(row, i, i+3))
        else:
            w('    {}\n'.format(row))
    w('};\n\n')
    w('static const uint64_t {}_nodes[] =\n{{\n'.format(name))
    w('    // Binary encoded biome parameter search tree for {}.\n'.format(title))
    w('''    //
    //   +-------------- If the top byte equals 0xFF, the node is a leaf and the
    //   |               second byte is the biome id, otherwise the two bytes
    //   |               are a short index to the first child node.
    //   |
    //   | +------------ Biome parameter index for 5 (weirdness)
    //   | | +---------- Biome parameter index for 4 (depth)
    //   | | | +-------- Biome parameter index for 3 (erosion)
    //   | | | | +------ Biome parameter index for 2 (continentalness)
    //   | | | | | +---- Biome parameter index for 1 (humidity)
    //   | | | | | | +-- Biome parameter index for 0 (temperature)
    //   | | | | | | |
    //   v v v v v v v
''')
    enc = []
    for n in nodes:
        if n.biome is not None:
            top = 0xFF00 | ids[n.biome]
        else:
            top = n.children[0].idx
            assert top < 0xFF00
        v = top << 48
        # The root's bounds are never used for pruning, but climateToBiome()
        # seeds its warm start with node 0 when *dat == 0. Like the upstream
        # tables, give the root the point {0,0} so it is never a close match.
        ranges = [(0, 0)] * 6 if n is nodes[0] else n.ranges
        for i, r in enumerate(ranges):
            v |= pidx[r] << (8*i)
        enc.append('0x{:016X}'.format(v))
    w('    {},\n'.format(enc[0]))
    for i in range(1, len(enc), 4):
        w('    {},\n'.format(','.join(enc[i:i+4])))
    w('};\n\n')


def check_layout(nodes, steps, order):
    """Replay cubiomes' sibling addressing and make sure it visits exactly the
    real children of every inner node."""
    total = len(nodes)
    for n in nodes:
        if not n.children:
            continue
        # mirrors the do-while in get_resulting_node()
        depth = n.depth
        while True:
            step = steps[depth]
            depth += 1
            if n.idx + step < total:
                break
        got, inner = [], n.children[0].idx
        for _ in range(order):
            got.append(inner)
            inner += step
            if inner >= total:
                break
        want = [c.idx for c in n.children]
        if got != want:
            # the trailing partial subtrees of order 6 trees do not follow the
            # stride layout; this is the same in the original tables
            print('note: stride mismatch at node %d (depth %d): %s != %s' % (n.idx, n.depth, got[:4], want[:4]), file=sys.stderr)


if __name__ == '__main__':
    main()
