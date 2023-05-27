# ----------------------------------------------------------------------------
# Copyright (c) 2022-2023, QIIME 2 development team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file LICENSE, distributed with this software.
# ----------------------------------------------------------------------------

import os
from collections import deque

from q2_types_genomics.kraken2 import Kraken2ReportDirectoryFormat
from q2_types.tree import NewickFormat

import pandas as pd
import skbio


def select_kraken_features(reports: Kraken2ReportDirectoryFormat,
                           coverage_threshold: float = 0.1) \
        -> (pd.DataFrame, pd.DataFrame, NewickFormat):

    taxonomy = pd.DataFrame(columns=['id', 'taxonomy'])
    rows = []
    trees = []
    for relpath, df in reports.reports.iter_views(pd.DataFrame):
        sample_id, _ = os.path.split(relpath)

        filtered = df[df['perc_frags_covered'] >= coverage_threshold]
        tree = _kraken_to_ncbi_tree(filtered)
        tips = _ncbi_tree_to_tips(tree)
        if tips:
            table_row = pd.Series(True, index=_ncbi_tree_to_tips(tree))
            table_row.name = sample_id
            rows.append(table_row)
        trees.append(tree)

    full_tree = _combine_ncbi_trees(trees)

    table = pd.DataFrame(rows).fillna(False)
    taxonomy = _to_taxonomy(full_tree)
    newick = NewickFormat()
    with newick.open() as fh:
        full_tree.write(fh, format='newick')

    return table, taxonomy, newick


def _get_indentation(string, indent=2):
    return (len(string) - len(string.lstrip(' '))) // indent


def _kraken_to_ncbi_tree(df):
    tree = skbio.TreeNode()
    stack = deque([(0, tree)])
    for _, row in df.iterrows():
        r = row['rank']
        label = row['name']
        otu = str(row['ncbi_tax_id'])

        if r in ('U', 'R') or (len(r) > 1 and not r.startswith('S')):
            continue  # unclassified, root, or non-strain infra-clade

        indent = _get_indentation(label)
        name = f"{r.lower()}__{label.strip()}"
        node = skbio.TreeNode(name=name, length=1)
        id_node = skbio.TreeNode(name=otu, length=0)
        id_node.is_actual_tip = False
        node.append(id_node)

        parent_indent, parent_node = stack[-1]
        if parent_indent >= indent:
            parent_node.children[0].is_actual_tip = True

        while parent_indent >= indent:
            stack.pop()
            parent_indent, parent_node = stack[-1]

        parent_node.append(node)
        stack.append((indent, node))

    # last entry is always a tip
    _, parent_node = stack[-1]
    if parent_node.children:
        parent_node.children[0].is_actual_tip = True

    return tree


def _combine_ncbi_trees(trees):
    full_tree = trees[0]
    for tree in trees[1:]:
        for tip in list(tree.tips()):
            try:
                # check if taxid is already in this tree
                full_tree.find(tip.name)
                continue  # for clarity
            except skbio.tree.MissingNodeError:
                parents = list(tip.ancestors())[:-1]  # ignore unnamed root
                matching = full_tree
                while parents:
                    node = parents.pop()
                    try:
                        matching = matching.find(node.name)
                    except skbio.tree.MissingNodeError:
                        matching.append(node)
    return full_tree


def _ncbi_tree_to_tips(tree):
    return [n.name for n in tree.tips() if n.is_actual_tip]


def _pad_ranks(ranks):
    order = ['d', 'k', 'p', 'c', 'o', 'f', 'g', 's']
    available = {}
    taxonomy = []

    for rank in reversed(ranks):
        r, label = rank.split('__', 1)
        available[r] = label
        if len(r) > 1 and r.startswith('s'):
            taxonomy.append(rank)

    for r in reversed(order):
        label = available.get(r)

        if label is not None:
            taxonomy.append(f'{r}__{label}')
            last_good_label = f'{r}__{label}'
        elif taxonomy:
            if r == 'k' and available.get('d') in ('Bacteria', 'Archaea'):
                # it is too strange to propogate upwards for these 'kingdoms'
                taxonomy.append(f"k__{available['d']}")
            else:
                # smear the most specific label we have upwards
                taxonomy.append(f'{r}__containing {last_good_label}')

    return ';'.join(reversed(taxonomy))


def _to_taxonomy(tree):
    rows = [(node.name, _pad_ranks(ranks))
            for node, ranks in tree.to_taxonomy()]
    taxonomy = pd.DataFrame(rows, columns=['Feature ID', 'Taxon'])
    taxonomy = taxonomy.set_index('Feature ID')

    return taxonomy
