#!/usr/bin/env python
# coding: utf-8

import os
import argparse
import logging

import numpy as np
from astropy.table import Table, vstack
from matplotlib import pyplot as plt

from pycorr import TwoPointCorrelationFunction, TwoPointEstimator, KMeansSubsampler, utils, setup_logging
from LSS.tabulated_cosmo import TabulatedDESI


logger = logging.getLogger('xirunpc')


def get_zlims(tracer, tracer2=None, option=None):

    if tracer2 is not None:
        zlims1 = get_zlims(tracer, option=option)
        zlims2 = get_zlims(tracer2, option=option)
        return [zlim for zlim in zlims1 if zlim in zlims2]

    if tracer.startswith('LRG'):
        zlims = [0.4, 0.6, 0.8, 1.1]

    if tracer.startswith('ELG'):# or type == 'ELG_HIP':
        zlims = [0.8, 1.1, 1.6]
        if option:
            if option == 'safez':
                zlims = [0.9, 1.48]
            if 'extended' in option:
                print('extended is no longer a meaningful option')
                #zlims = [0.8, 1.1, 1.6]
            if 'smallshells' in option:
                zlims = [0.8, 0.9,1.0,1.1,1.2,1.3,1.4,1.5,1.6]    

    if tracer.startswith('QSO'):
        zlims = [0.8, 1.1, 1.6, 2.1,3.5]
        if option == 'highz':
            zlims = [2.1, 3.5]
        if option == 'lowz':
            zlims = [0.8, 2.1]

    if tracer.startswith('BGS'):
        zlims = [0.1, 0.3, 0.5]
        if option == 'lowz':
            zlims = [0.1, 0.3]
        if option == 'highz':
            zlims = [0.3, 0.5]

    if option == 'fullonly':
        zlims = [zlims[0], zlims[-1]]

    return zlims


def get_regions(survey, rec=False):
    regions = ['N', 'S']#, '']
    #if survey in ['main', 'DA02']:
    #    regions = ['DN', 'DS', 'N', 'S']
    #    if rec: regions = ['DN', 'N']
    return regions


def select_region(ra, dec, region):
    mask_ra = (ra > 100 - dec)
    mask_ra &= (ra < 280 + dec)
    if region == 'DN':
        mask = dec < 32.375
        mask &= mask_ra
    elif region == 'DS':
        mask = dec > -25
        mask &= ~mask_ra
    else:
        raise ValueError('Input region must be one of ["DN", "DS"].')
    return mask


def catalog_dir(survey='main', verspec='guadalupe', version='test', base_dir='/global/cfs/cdirs/desi/survey/catalogs'):
    return os.path.join(base_dir, survey, 'LSS', verspec, 'LSScats', version)


def catalog_fn(tracer='ELG', region='', ctype='clustering', name='data', rec_type=False, nrandoms=4, cat_dir=None, survey='main', **kwargs):
    if cat_dir is None:
        cat_dir = catalog_dir(survey=survey, **kwargs)
    if survey in ['main', 'DA02']:
        tracer += 'zdone'
    if ctype == 'full':
        region = ''
    dat_or_ran = name[:3]
    if name == 'randoms' and tracer == 'LRG_main' and ctype == 'full':
        tracer = 'LRG'
    if region: region = '_' + region
    if rec_type:
        dat_or_ran = '{}.{}'.format(rec_type, dat_or_ran)
    if name == 'data':
        return os.path.join(cat_dir, '{}{}_{}.{}.fits'.format(tracer, region, ctype, dat_or_ran))
    return [os.path.join(cat_dir, '{}{}_{:d}_{}.{}.fits'.format(tracer, region, iran, ctype, dat_or_ran)) for iran in range(nrandoms)]


def get_clustering_positions_weights(catalog, distance, zlim=(0., np.inf), weight_type='default', name='data', return_mask=False, option=None):

    mask = (catalog['Z'] >= zlim[0]) & (catalog['Z'] < zlim[1])
    if option:
        if 'elgzmask' in option:
            zmask = ((catalog['Z'] >= 1.49) & (catalog['Z'] < 1.52))
            mask &= ~zmask
    logger.info('Using {:d} rows for {}.'.format(mask.sum(), name))
    positions = [catalog['RA'][mask], catalog['DEC'][mask], distance(catalog['Z'][mask])]
    weights = np.ones_like(positions[0])

    if 'completeness_only' in weight_type and 'bitwise' in weight_type:
        raise ValueError('inconsistent choices were put into weight_type')

    if name == 'data':
        if 'zfail' in weight_type:
            weights *= catalog['WEIGHT_ZFAIL'][mask]
        if 'default' in weight_type and 'bitwise' not in weight_type:
            weights *= catalog['WEIGHT'][mask]
        if 'RF' in weight_type:
            weights *= catalog['WEIGHT_RF'][mask]*catalog['WEIGHT_COMP'][mask]
        if 'completeness_only' in weight_type:
            weights = catalog['WEIGHT_COMP'][mask]
        if 'FKP' in weight_type:
            weights *= catalog['WEIGHT_FKP'][mask]
        if 'bitwise' in weight_type:
            if catalog['BITWEIGHTS'].ndim == 2: weights = list(catalog['BITWEIGHTS'][mask].T) + [weights]
            else: weights = [catalog['BITWEIGHTS'][mask]] + [weights]

    if name == 'randoms':
        if 'default' in weight_type:
            weights *= catalog['WEIGHT'][mask]
        if 'RF' in weight_type:
            weights *= catalog['WEIGHT_RF'][mask]*catalog['WEIGHT_COMP'][mask]
        if 'zfail' in weight_type:
            weights *= catalog['WEIGHT_ZFAIL'][mask]
        if 'completeness_only' in weight_type:
            weights = catalog['WEIGHT_COMP'][mask]
        if 'FKP' in weight_type:
            weights *= catalog['WEIGHT_FKP'][mask]

    if return_mask:
        return positions, weights, mask
    return positions, weights


def read_clustering_positions_weights(distance, zlim=(0., np.inf), weight_type='default', name='data', concatenate=False, option=None, **kwargs):

    cat_fns = catalog_fn(ctype='clustering', name=name, **kwargs)
    logger.info('Loading {}.'.format(cat_fns))
    isscalar = not isinstance(cat_fns, (tuple, list))
    if isscalar:
        cat_fns = [cat_fns]

    toret = [get_clustering_positions_weights(Table.read(cat_fn), distance, zlim=zlim, weight_type=weight_type, name=name, option=option) for cat_fn in cat_fns]
    if isscalar:
        return toret[0]
    positions_weights = [[tmp[0] for tmp in toret], [tmp[1] for tmp in toret]]
    if concatenate:
        for iarray, arrays in enumerate(positions_weights):
            if isinstance(arrays[0], (tuple, list)):  # e.g., list of bitwise weights
                array = [np.concatenate([arr[iarr] for arr in arrays], axis=0) for iarr in range(len(arrays[0]))]
            else:
                array = np.concatenate(arrays, axis=0)
            positions_weights[iarray] = array
    return positions_weights


def get_full_positions_weights(catalog, name='data', weight_type='default', fibered=False, region='', return_mask=False):

    mask = np.ones(len(catalog), dtype='?')
    if region in ['DS', 'DN']:
        mask &= select_region(catalog['RA'], catalog['DEC'], region)
    elif region:
        mask &= catalog['PHOTSYS'] == region.strip('_')

    if fibered: mask &= catalog['LOCATION_ASSIGNED']
    positions = [catalog['RA'][mask], catalog['DEC'][mask], catalog['DEC'][mask]]
    if fibered and 'bitwise' in weight_type:
        if catalog['BITWEIGHTS'].ndim == 2: weights = list(catalog['BITWEIGHTS'][mask].T)
        else: weights = [catalog['BITWEIGHTS'][mask]]
    else: weights = np.ones_like(positions[0])
    if return_mask:
        return positions, weights, mask
    return positions, weights


def read_full_positions_weights(name='data', weight_type='default', fibered=False, region='', **kwargs):

    cat_fn = catalog_fn(ctype='full', name=name, **kwargs)
    logger.info('Loading {}.'.format(cat_fn))
    if isinstance(cat_fn, (tuple, list)):
        catalog = vstack([Table.read(fn) for fn in cat_fn])
    else:
        catalog = Table.read(cat_fn)
    return get_full_positions_weights(catalog, name=name, weight_type=weight_type, fibered=fibered, region=region)


def compute_angular_weights(nthreads=8, dtype='f8', tracer='ELG', tracer2=None, mpicomm=None, mpiroot=None, **kwargs):

    autocorr = tracer2 is None
    catalog_kwargs = kwargs

    fibered_data_positions1, fibered_data_weights1, fibered_data_positions2, fibered_data_weights2 = None, None, None, None
    parent_data_positions1, parent_data_weights1, parent_data_positions2, parent_data_weights2 = None, None, None, None
    parent_randoms_positions1, parent_randoms_weights1, parent_randoms_positions2, parent_randoms_weights2 = None, None, None, None

    if mpicomm is None or mpicomm.rank == mpiroot:

        fibered_data_positions1, fibered_data_weights1 = read_full_positions_weights(name='data', fibered=True, tracer=tracer, **catalog_kwargs)
        parent_data_positions1, parent_data_weights1 = read_full_positions_weights(name='data', fibered=False, tracer=tracer, **catalog_kwargs)
        parent_randoms_positions1, parent_randoms_weights1 = read_full_positions_weights(name='randoms', tracer=tracer, **catalog_kwargs)
        if not autocorr:
            fibered_data_positions2, fibered_data_weights2 = read_full_positions_weights(name='data', fibered=True, tracer=tracer2, **catalog_kwargs)
            parent_data_positions2, parent_data_weights2 = read_full_positions_weights(name='data', fibered=False, tracer=tracer2, **catalog_kwargs)
            parent_randoms_positions2, parent_randoms_weights2 = read_full_positions_weights(name='randoms', tracer=tracer2, **catalog_kwargs)

    tedges = np.logspace(-4., 0.5, 41)
    # First D1D2_parent/D1D2_PIP angular weight
    wangD1D2 = TwoPointCorrelationFunction('theta', tedges, data_positions1=fibered_data_positions1, data_weights1=fibered_data_weights1,
                                            data_positions2=fibered_data_positions2, data_weights2=fibered_data_weights2,
                                            randoms_positions1=parent_data_positions1, randoms_weights1=parent_data_weights1,
                                            randoms_positions2=parent_data_positions2, randoms_weights2=parent_data_weights2,
                                            estimator='weight', engine='corrfunc', position_type='rdd', nthreads=nthreads,
                                            dtype=dtype, mpicomm=mpicomm, mpiroot=mpiroot)

    # First D1R2_parent/D1R2_IIP angular weight
    # Input bitwise weights are automatically turned into IIP
    if autocorr:
         parent_randoms_positions2, parent_randoms_weights2 = parent_randoms_positions1, parent_randoms_weights1
    wangD1R2 = TwoPointCorrelationFunction('theta', tedges, data_positions1=fibered_data_positions1, data_weights1=fibered_data_weights1,
                                            data_positions2=parent_randoms_positions2, data_weights2=parent_randoms_weights2,
                                            randoms_positions1=parent_data_positions1, randoms_weights1=parent_data_weights1,
                                            randoms_positions2=parent_randoms_positions2, randoms_weights2=parent_randoms_weights2,
                                            estimator='weight', engine='corrfunc', position_type='rdd', nthreads=nthreads,
                                            dtype=dtype, mpicomm=mpicomm, mpiroot=mpiroot)
    wangR1D2 = None
    if not autocorr:
        wangR1D2 = TwoPointCorrelationFunction('theta', tedges, data_positions1=parent_randoms_positions1, data_weights1=parent_randoms_weights1,
                                               data_positions2=fibered_data_positions2, data_weights2=fibered_data_weights2,
                                               randoms_positions1=parent_randoms_positions1, randoms_weights1=parent_randoms_weights1,
                                               randoms_positions2=parent_data_positions2, randoms_weights2=parent_data_weights2,
                                               estimator='weight', engine='corrfunc', position_type='rdd', nthreads=nthreads,
                                               dtype=dtype, mpicomm=mpicomm, mpiroot=mpiroot)

    wang = {}
    wang['D1D2_twopoint_weights'] = wangD1D2
    wang['D1R2_twopoint_weights'] = wangD1R2
    wang['R1D2_twopoint_weights'] = wangR1D2

    return wang


def compute_correlation_function(corr_type, edges, distance, nthreads=8, dtype='f8', wang=None, split_randoms_above=30., weight_type='default', tracer='ELG', tracer2=None, rec_type=None, njack=120,option=None, mpicomm=None, mpiroot=None, **kwargs):

    autocorr = tracer2 is None
    catalog_kwargs = kwargs.copy()
    catalog_kwargs['weight_type'] = weight_type
    with_shifted = rec_type is not None

    if 'angular' in weight_type and wang is None:

        wang = compute_angular_weights(nthreads=nthreads, dtype=dtype, weight_type=weight_type, tracer=tracer, tracer2=tracer2, mpicomm=mpicomm, mpiroot=mpiroot, **kwargs)

    data_positions1, data_weights1, data_samples1, data_positions2, data_weights2, data_samples2 = None, None, None, None, None, None
    randoms_positions1, randoms_weights1, randoms_samples1, randoms_positions2, randoms_weights2, randoms_samples2 = None, None, None, None, None, None
    shifted_positions1, shifted_weights1, shifted_samples1, shifted_positions2, shifted_weights2, shifted_samples2 = None, None, None, None, None, None
    jack_positions = None

    if mpicomm is None or mpicomm.rank == mpiroot:

        if with_shifted:
            data_positions1, data_weights1 = read_clustering_positions_weights(distance, name='data', rec_type=rec_type, tracer=tracer,option=option, **catalog_kwargs)
            shifted_positions1, shifted_weights1 = read_clustering_positions_weights(distance, name='randoms', rec_type=rec_type, tracer=tracer,option=option, **catalog_kwargs)
        else:
            data_positions1, data_weights1 = read_clustering_positions_weights(distance, name='data', rec_type=rec_type, tracer=tracer,option=option, **catalog_kwargs)
        randoms_positions1, randoms_weights1 = read_clustering_positions_weights(distance, name='randoms', rec_type=rec_type, tracer=tracer,option=option, **catalog_kwargs)
        jack_positions = data_positions1

        if not autocorr:
            if with_shifted:
                data_positions2, data_weights2 = read_clustering_positions_weights(distance, name='data', rec_type=rec_type, tracer=tracer2,option=option, **catalog_kwargs)
                shifted_positions2, shifted_weights2 = read_clustering_positions_weights(distance, name='randoms', rec_type=rec_type, tracer=tracer2,option=option, **catalog_kwargs)
            else:
                data_positions2, data_weights2 = read_clustering_positions_weights(distance, name='data', rec_type=rec_type, tracer=tracer2,option=option, **catalog_kwargs)
            randoms_positions2, randoms_weights2 = read_clustering_positions_weights(distance, name='randoms', rec_type=rec_type, tracer=tracer2,option=option, **catalog_kwargs)
            jack_positions = [np.concatenate([p1, p2], axis=0) for p1, p2 in zip(jack_positions, data_positions2)]

    if njack >= 2:
        subsampler = KMeansSubsampler('angular', positions=jack_positions, nsamples=njack, nside=512, random_state=42, position_type='rdd',
                                      dtype=dtype, mpicomm=mpicomm, mpiroot=mpiroot)

        if mpicomm is None or mpicomm.rank == mpiroot:
            data_samples1 = subsampler.label(data_positions1)
            randoms_samples1 = [subsampler.label(p) for p in randoms_positions1]
            if with_shifted:
                shifted_samples1 = [subsampler.label(p) for p in shifted_positions1]
            if not autocorr:
                data_samples2 = subsampler.label(data_positions2)
                randoms_samples2 = [subsampler.label(p) for p in randoms_positions2]
                if with_shifted:
                    shifted_samples2 = [subsampler.label(p) for p in shifted_positions2]

    kwargs = {}
    kwargs.update(wang or {})
    randoms_kwargs = dict(randoms_positions1=randoms_positions1, randoms_weights1=randoms_weights1, randoms_samples1=randoms_samples1,
                          randoms_positions2=randoms_positions2, randoms_weights2=randoms_weights2, randoms_samples2=randoms_samples2,
                          shifted_positions1=shifted_positions1, shifted_weights1=shifted_weights1, shifted_samples1=shifted_samples1,
                          shifted_positions2=shifted_positions2, shifted_weights2=shifted_weights2, shifted_samples2=shifted_samples2)

    zedges = np.array(list(zip(edges[0][:-1], edges[0][1:])))
    mask = zedges[:,0] >= split_randoms_above
    zedges = [zedges[~mask], zedges[mask]]
    split_edges, split_randoms = [], []
    for ii, zedge in enumerate(zedges):
        if zedge.size:
            split_edges.append([np.append(zedge[:,0], zedge[-1,-1])] + list(edges[1:]))
            split_randoms.append(ii > 0)

    results = []
    if mpicomm is None:
        nran = len(randoms_positions1)
    else:
        nran = mpicomm.bcast(len(randoms_positions1) if mpicomm.rank == mpiroot else None, root=mpiroot)
    for i_split_randoms, edges in zip(split_randoms, split_edges):
        result = 0
        D1D2 = None
        for iran in range(nran if i_split_randoms else 1):
            tmp_randoms_kwargs = {}
            if i_split_randoms:
                # On scales above split_randoms_above, sum correlation function over multiple randoms
                for name, arrays in randoms_kwargs.items():
                    if arrays is None:
                        continue
                    else:
                        tmp_randoms_kwargs[name] = arrays[iran]
            else:
                # On scales below split_randoms_above, concatenate randoms
                for name, arrays in randoms_kwargs.items():
                    if arrays is None:
                        continue
                    elif isinstance(arrays[0], (tuple, list)):  # e.g., list of bitwise weights
                        array = [np.concatenate([arr[iarr] for arr in arrays], axis=0) for iarr in range(len(arrays[0]))]
                    else:
                        array = np.concatenate(arrays, axis=0)
                    tmp_randoms_kwargs[name] = array
            tmp = TwoPointCorrelationFunction(corr_type, edges, data_positions1=data_positions1, data_weights1=data_weights1, data_samples1=data_samples1,
                                              data_positions2=data_positions2, data_weights2=data_weights2, data_samples2=data_samples2,
                                              engine='corrfunc', position_type='rdd', nthreads=nthreads, dtype=dtype, **tmp_randoms_kwargs, **kwargs,
                                              D1D2=D1D2, mpicomm=mpicomm, mpiroot=mpiroot)
            D1D2 = tmp.D1D2
            result += tmp
        results.append(result)
    return results[0].concatenate_x(*results), wang


def get_edges(corr_type='smu', bin_type='lin'):

    if bin_type == 'log':
        sedges = np.geomspace(0.01, 100., 49)
    elif bin_type == 'lin':
        sedges = np.linspace(0., 200, 201)
    else:
        raise ValueError('bin_type must be one of ["log", "lin"]')
    if corr_type == 'smu':
        edges = (sedges, np.linspace(-1., 1., 201)) #s is input edges and mu evenly spaced between -1 and 1
    elif corr_type == 'rppi':
        if bin_type == 'lin':
            edges = (sedges, sedges) #transverse and radial separations are coded to be the same here
        else:
            edges = (sedges, np.linspace(0., 40., 41))
    elif corr_type == 'theta':
        edges = np.linspace(0., 4., 101)
    else:
        raise ValueError('corr_type must be one of ["smu", "rppi", "theta"]')
    return edges


def corr_fn(file_type='npy', region='', tracer='ELG', tracer2=None, zmin=0, zmax=np.inf, rec_type=False, weight_type='default', bin_type='lin', njack=0, out_dir='.',option=None):
    if tracer2: tracer += '_' + tracer2
    if rec_type: tracer += '_' + rec_type
    if region: tracer += '_' + region
    if option:
        zmax = str(zmax) + option
    root = '{}_{}_{}_{}_{}_njack{:d}'.format(tracer, zmin, zmax, weight_type, bin_type, njack)
    if file_type == 'npy':
        return os.path.join(out_dir, 'allcounts_{}.npy'.format(root))
    return os.path.join(out_dir, '{}_{}.txt'.format(file_type, root))


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--tracer', help='tracer(s) to be selected - 2 for cross-correlation', type=str, nargs='+', default=['ELG'])
    parser.add_argument('--basedir', help='where to find catalogs', type=str, default='/global/cfs/cdirs/desi/survey/catalogs')
    parser.add_argument('--survey', help='e.g., SV3 or main', type=str, choices=['SV3', 'DA02', 'main'], default='SV3')
    parser.add_argument('--verspec', help='version for redshifts', type=str, default='guadalupe')
    parser.add_argument('--version', help='catalog version', type=str, default='test')
    parser.add_argument('--region', help='regions; by default, run on all regions', type=str, nargs='*', choices=['N', 'S'], default=None)
    parser.add_argument('--zlim', help='z-limits, or options for z-limits, e.g. "highz", "lowz", "fullonly"', type=str, nargs='*', default=None)
    parser.add_argument('--corr_type', help='correlation type', type=str, nargs='*', choices=['smu', 'rppi', 'theta'], default=['smu', 'rppi'])
    parser.add_argument('--weight_type', help='types of weights to use; use "default_angular_bitwise" for PIP with angular upweighting; "default" just uses WEIGHT column', type=str, default='default')
    parser.add_argument('--bin_type', help='binning type', type=str, choices=['log', 'lin'], default='lin')
    parser.add_argument('--nran', help='number of random files to combine together (1-18 available)', type=int, default=4)
    parser.add_argument('--split_ran_above', help='separation scale above which RR are summed over each random file;\
                                                   typically, most efficient for xi < 1, i.e. sep > 10 Mpc/h;\
                                                   see https://arxiv.org/pdf/1905.01133.pdf',
                        type=float, default=np.inf)
    parser.add_argument('--njack', help='number of jack-knife subsamples; 0 for no jack-knife error estimates', type=int, default=60)
    parser.add_argument('--nthreads', help='number of threads', type=int, default=64)
    parser.add_argument('--outdir', help='base directory for output', type=str, default=None)
    #parser.add_argument('--mpi', help='whether to use MPI', action='store_true', default=False)
    parser.add_argument('--vis', help='show plot of each xi?', action='store_true', default=False)

    #only relevant for reconstruction
    parser.add_argument('--rec_type', help='reconstruction algorithm + reconstruction convention', choices=['IFTrecsym', 'IFTreciso', 'MGrecsym', 'MGreciso'], type=str, default=None)

    setup_logging()
    args = parser.parse_args()

    mpicomm, mpiroot = None, None
    if True:#args.mpi:
        from pycorr import mpi
        mpicomm = mpi.COMM_WORLD
        mpiroot = 0

    if args.basedir == '/global/project/projectdirs/desi/users/acarnero/mtl_mock000_univ1/':
        cat_dir = args.basedir
        args.region = ['']
    else:
        cat_dir = catalog_dir(base_dir=args.basedir, survey=args.survey, verspec=args.verspec, version=args.version)
    out_dir = os.path.join(os.environ['CSCRATCH'], args.survey)
    if args.outdir is not None: out_dir = args.outdir
    tracer, tracer2 = args.tracer[0], None
    if len(args.tracer) > 1:
        tracer2 = args.tracer[1]
        if len(args.tracer) > 2:
            raise ValueError('Provide <= 2 tracers!')
    if tracer2 == tracer:
        tracer2 = None # otherwise counting of self-pairs
    catalog_kwargs = dict(tracer=tracer, tracer2=tracer2, survey=args.survey, cat_dir=cat_dir, rec_type=args.rec_type) # survey required for zdone
    distance = TabulatedDESI().comoving_radial_distance

    regions = args.region
    if regions is None:
        regions = get_regions(args.survey, rec=bool(args.rec_type))

    option = None
    if args.zlim is None:
        zlims = get_zlims(tracer, tracer2=tracer2)
    elif not args.zlim[0].replace('.', '').isdigit():
        option=args.zlim[0]
        zlims = get_zlims(tracer, tracer2=tracer2,option=option )
    else:
        zlims = [float(zlim) for zlim in args.zlim]
    zlims = list(zip(zlims[:-1], zlims[1:])) + ([(zlims[0], zlims[-1])] if len(zlims) > 2 else []) # len(zlims) == 2 == single redshift range

    rebinning_factors = [1, 4, 5, 10] if 'lin' in args.bin_type else [1,2,4]
    pi_rebinning_factors = [1, 4, 5, 10] if 'log' in args.bin_type else [1]
    if mpicomm is None or mpicomm.rank == mpiroot:
        logger.info('Computing correlation functions {} in regions {} in redshift ranges {}.'.format(args.corr_type, regions, zlims))

    for zmin, zmax in zlims:
        base_file_kwargs = dict(tracer=tracer, tracer2=tracer2, zmin=zmin, zmax=zmax, rec_type=args.rec_type, weight_type=args.weight_type, bin_type=args.bin_type, njack=args.njack, option=option)
        for region in regions:
            wang = None
            for corr_type in args.corr_type:
                if mpicomm is None or mpicomm.rank == mpiroot:
                    logger.info('Computing correlation function {} in region {} in redshift range {}.'.format(corr_type, region, (zmin, zmax)))
                edges = get_edges(corr_type=corr_type, bin_type=args.bin_type)
                result, wang = compute_correlation_function(corr_type, edges=edges, distance=distance, nrandoms=args.nran, split_randoms_above=args.split_ran_above, nthreads=args.nthreads, region=region, zlim=(zmin, zmax), weight_type=args.weight_type, njack=args.njack, wang=wang, mpicomm=mpicomm, mpiroot=mpiroot,option=option, **catalog_kwargs)
                #save pair counts
                result.save(corr_fn(file_type='npy', region=region, out_dir=os.path.join(out_dir, corr_type), **base_file_kwargs))

        # Save combination and .txt files
        for corr_type in args.corr_type:
            all_regions = regions.copy()
            if 'N' in regions and 'S' in regions:  # let's combine
                corr = sum([TwoPointCorrelationFunction.load(
                            corr_fn(file_type='npy', region=region, out_dir=os.path.join(out_dir, corr_type), **base_file_kwargs)).normalize() for region in 'NS'])
                corr.save(corr_fn(file_type='npy', region='NScomb', out_dir=os.path.join(out_dir, corr_type), **base_file_kwargs))
                all_regions.append('NScomb')
            for region in all_regions:
                txt_kwargs = base_file_kwargs.copy()
                txt_kwargs.update(region=region, out_dir=os.path.join(out_dir, corr_type))
                result = TwoPointCorrelationFunction.load(corr_fn(file_type='npy', **txt_kwargs))
                for factor in rebinning_factors:
                    #result = TwoPointEstimator.load(fn)
                    rebinned = result[:(result.shape[0]//factor)*factor:factor]
                    txt_kwargs.update(bin_type=args.bin_type+str(factor))
                    if corr_type == 'smu':
                        fn_txt = corr_fn(file_type='xismu', **txt_kwargs)
                        rebinned.save_txt(fn_txt)
                        fn_txt = corr_fn(file_type='xipoles', **txt_kwargs)
                        rebinned.save_txt(fn_txt, ells=(0, 2, 4))
                        fn_txt = corr_fn(file_type='xiwedges', **txt_kwargs)
                        rebinned.save_txt(fn_txt, wedges=(-1., -2./3, -1./3, 0., 1./3, 2./3, 1.))
                    elif corr_type == 'rppi':
                        fn_txt = corr_fn(file_type='wp', **txt_kwargs)
                        rebinned.save_txt(fn_txt, pimax=40.)
                        for pifac in pi_rebinning_factors:
                            rebinned = result[:(result.shape[0]//factor)*factor:factor,:(result.shape[1]//pifac)*pifac:pifac]
                            txt_kwargs.update(bin_type=args.bin_type+str(factor)+'_'+str(pifac))
                            fn_txt = corr_fn(file_type='xirppi', **txt_kwargs)
                            rebinned.save_txt(fn_txt)
                    elif corr_type == 'theta':
                        fn_txt = corr_fn(file_type='theta', **txt_kwargs)
                        rebinned.save_txt(fn_txt)

                    if args.vis and (mpicomm is None or mpicomm.rank == mpiroot):
                        if corr_type == 'smu':
                            sep, xis = rebinned(ells=(0, 2, 4), return_sep=True, return_std=False)
                        elif corr_type == 'rppi':
                            sep, xis = rebinned(pimax=40, return_sep=True, return_std=False)
                        else:
                            sep, xis = rebinned(return_sep=True, return_std=False)
                        if args.bin_type == 'log':
                            for xi in xis: plt.loglog(sep, xi)
                        if args.bin_type == 'lin':
                            for xi in xis: plt.plot(sep, sep**2 * xi)
                        tracers = tracer
                        if tracer2 is not None: tracers += ' x ' + tracer2
                        plt.title('{} {:.2f} < z {:.2f} in {}'.format(tracers, zmin, zmax, region))
                        plt.show()