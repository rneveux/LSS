#!/usr/bin/env python
# coding: utf-8

import os
import argparse
import logging

import numpy as np
from astropy.table import Table, vstack

from pyrecon import MultiGridReconstruction, IterativeFFTReconstruction, IterativeFFTParticleReconstruction, utils, setup_logging
from LSS.tabulated_cosmo import TabulatedDESI

from xirunpc import get_clustering_positions_weights, catalog_dir, catalog_fn, get_regions, get_zlims


logger = logging.getLogger('recon')


def run_reconstruction(Reconstruction, distance, data_fn, randoms_fn, data_rec_fn, randoms_rec_fn, f=0.8, bias=1.2, boxsize=None, nmesh=None, cellsize=7, smoothing_radius=15, nthreads=8, convention='reciso', dtype='f4', **kwargs):

    if np.ndim(randoms_fn) == 0: randoms_fn = [randoms_fn]
    if np.ndim(randoms_rec_fn) == 0: randoms_rec_fn = [randoms_rec_fn]

    logger.info('Loading {}.'.format(data_fn))
    data = Table.read(data_fn)
    (ra, dec, dist), data_weights, mask = get_clustering_positions_weights(data, distance, name='data', return_mask=True, **kwargs)
    data = data[mask]
    data_positions = utils.sky_to_cartesian(dist, ra, dec, dtype=dtype)
    recon = Reconstruction(f=f, bias=bias, boxsize=boxsize, nmesh=nmesh, cellsize=cellsize, los='local', positions=data_positions, nthreads=nthreads, fft_engine='fftw', dtype=dtype)

    recon.assign_data(data_positions, data_weights)
    for fn in randoms_fn:
        logger.info('Loading {}.'.format(fn))
        (ra, dec, dist), randoms_weights = get_clustering_positions_weights(Table.read(fn), distance, name='randoms', **kwargs)
        randoms_positions = utils.sky_to_cartesian(dist, ra, dec, dtype=dtype)
        recon.assign_randoms(randoms_positions, randoms_weights)

    recon.set_density_contrast(smoothing_radius=smoothing_radius)
    recon.run()

    field = 'disp+rsd'
    if type(recon) is IterativeFFTParticleReconstruction:
        data_positions_rec = recon.read_shifted_positions('data', field=field)
    else:
        data_positions_rec = recon.read_shifted_positions(data_positions, field=field)

    distance_to_redshift = utils.DistanceToRedshift(distance)
    catalog = Table(data)
    dist, ra, dec = utils.cartesian_to_sky(data_positions_rec)
    catalog['RA'], catalog['DEC'], catalog['Z'] = ra, dec, distance_to_redshift(dist)
    logger.info('Saving {}.'.format(data_rec_fn))
    catalog.write(data_rec_fn, format='fits', overwrite=True)

    field = 'disp+rsd' if convention == 'recsym' else 'disp'
    for fn, rec_fn in zip(randoms_fn, randoms_rec_fn):
        catalog = Table.read(fn)
        (ra, dec, dist), randoms_weights, mask = get_clustering_positions_weights(catalog, distance, name='randoms', return_mask=True, **kwargs)
        catalog = catalog[mask]
        randoms_positions = utils.sky_to_cartesian(dist, ra, dec, dtype=dtype)
        dist, ra, dec = utils.cartesian_to_sky(recon.read_shifted_positions(randoms_positions, field=field))
        catalog['RA'], catalog['DEC'], catalog['Z'] = ra, dec, distance_to_redshift(dist)
        logger.info('Saving {}.'.format(rec_fn))
        catalog.write(rec_fn, format='fits', overwrite=True)


def get_f_bias(tracer='ELG'):
    if tracer.startswith('ELG') or tracer.startswith('QSO'):
        return 0.8, 1.
    if tracer.startswith('LRG'):
        return 0.7, 1.8
    return 0.8, 1.2


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--tracer', help='tracer to be selected', type=str, default='ELG')
    parser.add_argument('--basedir', help='where to find catalogs', type=str, default='/global/cfs/cdirs/desi/survey/catalogs')
    parser.add_argument('--survey', help='e.g., SV3 or main', type=str, choices=['SV3', 'DA02', 'main'], default='SV3')
    parser.add_argument('--verspec', help='version for redshifts', type=str, default='everest')
    parser.add_argument('--version', help='catalog version', type=str, default='test')
    parser.add_argument('--region', help='regions; by default, run on all regions', type=str, nargs='*', choices=['N', 'S', 'DN', 'DS', ''], default=None)
    parser.add_argument('--zlim', help='z-limits, or options for z-limits, e.g. "highz", "lowz"', type=str, nargs='*', default=None)
    parser.add_argument('--weight_type', help='types of weights to use; "default" just uses WEIGHT column', type=str, default='default')
    parser.add_argument('--nran', help='number of random files to combine together (1-18 available)', type=int, default=5)
    parser.add_argument('--nthreads', help='number of threads', type=int, default=64)
    parser.add_argument('--outdir',  help='base directory for output', type=str, default=None)
    parser.add_argument('--algorithm', help='reconstruction algorithm', type=str, choices=['MG', 'IFT', 'IFTP'], default='MG')
    parser.add_argument('--convention', help='reconstruction convention', type=str, choices=['reciso', 'recsym'], default='reciso')
    parser.add_argument('--f', help='growth rate', type=float, default=None)
    parser.add_argument('--bias', help='bias', type=float, default=None)
    parser.add_argument('--boxsize', help='box size', type=float, default=None)
    parser.add_argument('--nmesh', help='mesh size', type=int, default=None)
    parser.add_argument('--cellsize', help='cell size', type=float, default=7)
    parser.add_argument('--smoothing_radius', help='smoothing radius', type=float, default=15)

    setup_logging()
    args = parser.parse_args()

    Reconstruction = {'MG': MultiGridReconstruction, 'IFT': IterativeFFTReconstruction, 'IFTP': IterativeFFTParticleReconstruction}[args.algorithm]

    cat_dir = catalog_dir(base_dir=args.basedir, survey=args.survey, verspec=args.verspec, version=args.version)
    out_dir = os.path.join(os.environ['CSCRATCH'], args.survey)
    if args.outdir is not None: out_dir = args.outdir

    distance = TabulatedDESI().comoving_radial_distance

    f, bias = get_f_bias(args.tracer)
    if args.f is not None: f = args.f
    if args.bias is not None: bias = args.bias

    regions = args.region
    if regions is None:
        regions = get_regions(args.survey, rec=True)

    if args.zlim is None:
        zlims = get_zlims(args.tracer)
    elif not args.zlim[0].replace('.', '').isdigit():
        zlims = get_zlims(args.tracer, option=args.zlim[0])
    else:
        zlims = [float(zlim) for zlim in args.zlim]
    zlims = [(zlims[0], zlims[-1])]

    for zmin, zmax in zlims:
        for region in regions:
            logger.info('Running reconstruction in region {} in redshift range {} with f, bias = {}.'.format(region, (zmin, zmax), (f, bias)))
            catalog_kwargs = dict(tracer=args.tracer, region=region, ctype='clustering', nrandoms=args.nran, cat_dir=cat_dir, survey=args.survey)
            data_fn = catalog_fn(**catalog_kwargs, name='data')
            randoms_fn = catalog_fn(**catalog_kwargs, name='randoms')
            data_rec_fn = catalog_fn(**catalog_kwargs, rec_type=args.algorithm+args.convention, name='data')
            randoms_rec_fn = catalog_fn(**catalog_kwargs, rec_type=args.algorithm+args.convention, name='randoms')
            run_reconstruction(Reconstruction, distance, data_fn, randoms_fn, data_rec_fn, randoms_rec_fn, f=f, bias=bias, boxsize=args.boxsize, nmesh=args.nmesh, cellsize=args.cellsize, smoothing_radius=args.smoothing_radius, nthreads=args.nthreads, convention=args.convention, dtype='f4', zlim=(zmin, zmax), weight_type=args.weight_type)
