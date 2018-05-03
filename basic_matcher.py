import argparse
import json
import os
import pickle
import privileged_residues
import sys

from os import makedirs, path
from multiprocessing import Manager, Pool, freeze_support, cpu_count

from privileged_residues.privileged_residues import _init_pyrosetta as init
from privileged_residues import bidentify
from privileged_residues import position_residue

import pyrosetta
from pyrosetta.rosetta.core.select.residue_selector import \
    ResidueIndexSelector, SecondaryStructureSelector, TrueResidueSelector


_bidentate_types = ['Sc_Sc', 'Sc_ScBb', 'Sc_Bb']
_network_types = ['acceptor_acceptor', 'acceptor_donor', 'donor_acceptor',
                  'donor_donor']

_look_for_interactions = {
    'Sc_Sc': bidentify.look_for_sc_sc_bidentates,
    'Sc_ScBb': bidentify.look_for_sc_scbb_bidentates,
    'Sc_Bb': bidentify.look_for_sc_bb_bidentates,
    'acceptor_acceptor': bidentify.look_up_connected_network,
    'acceptor_donor': bidentify.look_up_connected_network,
    'donor_acceptor': bidentify.look_up_connected_network,
    'donor_donor': bidentify.look_up_connected_network,
    }


def make_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument("PDBFile", type=str, help="pose to match against")

    parser.add_argument("--cart-resl", dest="cart_resl", type=float,
                        default=0.1, help="Cartesian resolution of table")
    parser.add_argument("--ori-resl", dest="ori_resl", type=float,
                        default=2.0, help="Orientation resolution of table")
    parser.add_argument("--cart-bound", dest="cart_bound", type=float,
                        default=16.0, help="Cartesian boundary of table")

    parser.add_argument("--outpath", dest="outpath", type=str, default=None,
                        help="Output path")

    parser.add_argument("--residues", dest="residues", type=int, default=None,
                        nargs="+", help="Residue indices")

    parser.add_argument("--clash-cutoff", dest="clash_cutoff", type=float,
                        default=35., help="Tolerance for clash checking")

    parser.add_argument("--n-best", dest="n_best", type=int, default=10,
                        help="Number of top interactions to isolate")
    parser.add_argument("--params", dest="params", type=str, nargs="*",
                        help="Additional parameter files")

    parser.add_argument("--bidentates", dest="bidentates", type=str,
                        default=_bidentate_types, nargs="*",
                        help="Type of bidentate interactions to consider. " +
                             "Allowed values are: 'Sc_Sc', 'Sc_ScBb', " +
                             "'Sc_Bb'.")
    parser.add_argument("--networks", dest="networks", type=str,
                        default=_network_types, nargs="*",
                        help="Type of network arrangements to consider. " +
                             "Allowed values are: 'acceptor_acceptor', "
                             "'acceptor_donor', 'donor_acceptor', " +
                             "'donor_donor'.")
    return parser


def get_hits_for_interaction_type(ht_name_full, pdb_file, residues,
                                  return_container, idx):

    p = pyrosetta.pose_from_pdb(path.expanduser(pdb_file))
    selector = TrueResidueSelector()
    if residues is not None:
        selector = ResidueIndexSelector()
        for i in residues:
            selector.append_index(i)

    ht_info = position_residue._fname_to_HTD(ht_name_full)
    with open(ht_name_full, 'rb') as f:
        print('Loading table for {} interactions...'.format(ht_info.type))
        ht = pickle.load(f)

    pairs_of_rays = _look_for_interactions[ht_info.type](pose, selector)
    return_container[idx] = list(bidentify.look_up_interactions(pairs_of_rays,
                                 ht, ht_info.cart_resl, ht_info.ori_resl,
                                 ht_info.cart_bound))


def get_hits_from_tables(hash_tables, p, selector):
    hits = []
    for interaction_type in bidentate_interaction_types:
        ht_name_full = path.join(ht_path, interaction_type + suffix)
        hits += get_hits_for_interaction_type(ht_name_full, p, selector)
    return hits


def main(argv):
    parser = make_parser()
    args = parser.parse_args()

    assert(path.exists(path.expanduser(args.PDBFile)))

    for bidentate in args.bidentates:
        if bidentate not in _bidentate_types:
            raise ValueError('"{}" is not a valid option for '.format(
                                bidentate) + ' --bidentates. ' +
                             'Valid options are: "{}"'.format('", "'.join(
                                _bidentate_types)))
    networks = args.networks
    print(networks)
    for n in args.networks:
        try:
            if not json.loads(n.lower()):
                networks = []
        except json.JSONDecodeError:
            pass

    for network in networks:
        if network not in _network_types:
            raise ValueError('"{}" is not a valid option for '.format(
                                network) + ' --networks. ' +
                             'Valid options are: "{}"'.format('", "'.join(
                                _network_types)))

    suffix = '_{:.1f}_{:.1f}_{:.1f}.pkl'.format(args.cart_resl, args.ori_resl,
                                                args.cart_bound)

    # TODO: control this with a flag
    base_path = path.expanduser('~weitzner/digs')
    b_ht_path = path.join(base_path, 'bidentate_hbond_pdbs_2/hash_tables/comb')
    n_ht_path = path.join(base_path, 'three_res_hbnet/hash_tables_merge')

    hash_tables = [path.join(b_ht_path, it + suffix) for it in args.bidentates]
    hash_tables += [path.join(n_ht_path, it + suffix) for it in networks]

    for hash_table in hash_tables:
        assert(path.exists(hash_table))

    init()

    # TODO: allow an XML file that configures a ResidueSelector to be used
    # selector = TrueResidueSelector()

    # this should be trigguered by a flag. Fortunately, by fctoring the
    # business end of this script into its own function, another script could
    # easily call that function and pass a pre-configured ResidueSelector in.
    # selector = SecondaryStructureSelector("L")

    # moving this setup to the get_hits_for_interaction_type function
    # Rosetta objects cannot be pickled/serialized!
    '''
    if args.residues is not None:
        selector = ResidueIndexSelector()
        for idx in args.residues:
            selector.append_index(idx)
    '''

    manager = Manager()
    hits = manager.list([None for _ in range(len(hash_tables))])
    arguments = [(ht, args.PDBFile, args.residues, hits, idx) for idx, ht in
                 enumerate(hash_tables)]
    with Pool(processes=cpu_count()) as pool:
        pool.starmap(get_hits_for_interaction_type, arguments)
        pool.close()
        pool.join()

    if args.outpath:
        out = path.expanduser(args.outpath)
        makedirs(out, exist_ok=True)
        p = pyrosetta.pose_from_pdb(path.expanduser(args.PDBFile))
        p.dump_pdb(path.join(out, "ori.pdb"))

        for it_hits, ht_name in zip(hits, hash_tables):
            ht_info = position_residue._fname_to_HTD(ht_name)
            print('There are {} hits for {} interactions.'.format(len(it_hits,
                  ht_info.type)))
            for i, hit in enumerate(it_hits):
                hit.dump_pdb(path.join(out, '{}_result_{:04d}.pdb'.format(
                    ht_info.type, i + 1)))


if __name__ == "__main__":
    freeze_support()
    main(sys.argv)
