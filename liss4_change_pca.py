#!/usr/bin/env python3
"""Build the SOP-ordered LISS-IV stack and calculate six covariance PCA components."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

LOG = logging.getLogger("liss4_pca")
LABELS = (
    "Pre-monsoon B2", "Pre-monsoon B3", "Pre-monsoon B4",
    "Post-monsoon B2", "Post-monsoon B3", "Post-monsoon B4",
)
PC_NODATA = float(np.finfo(np.float32).min)
HFA_REAL_TYPES = {"Byte", "Int8", "UInt16", "Int16", "UInt32", "Int32", "Float32", "Float64"}


def _open_inputs(paths, band_indices):
    datasets, bands, sources = [], [], []
    reference = None

    for label, raw_path, band_index in zip(LABELS, paths, band_indices):
        path = Path(raw_path).expanduser().resolve()
        ds = gdal.Open(str(path), gdal.GA_ReadOnly)
        if ds is None:
            raise FileNotFoundError(f"GDAL could not open input: {path}")
        if not 1 <= band_index <= ds.RasterCount:
            raise ValueError(f"{path} has {ds.RasterCount} band(s), not band {band_index}.")
        band = ds.GetRasterBand(band_index)
        if gdal.GetDataTypeName(band.DataType).startswith("C"):
            raise ValueError(f"{label} is complex-valued; real multispectral bands are required.")

        transform = ds.GetGeoTransform(can_return_null=True)
        projection = ds.GetProjectionRef()
        if transform is None:
            raise ValueError(f"{path} has no affine geotransform.")
        if not projection:
            raise ValueError(f"{path} has no coordinate reference system.")
        srs = osr.SpatialReference()
        if srs.ImportFromWkt(projection) != 0:
            raise ValueError(f"Could not read the CRS for {path}.")
        grid = (ds.RasterXSize, ds.RasterYSize, tuple(transform), srs)

        if reference is None:
            reference = grid
        else:
            rw, rh, rgt, rsrs = reference
            w, h, gt, candidate_srs = grid
            if (w, h) != (rw, rh):
                raise ValueError(f"{label} dimensions differ. Align/resample all images before PCA.")
            if not rsrs.IsSame(candidate_srs):
                raise ValueError(f"{label} uses a different CRS. Reproject before PCA.")
            if any(not math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9) for a, b in zip(rgt, gt)):
                raise ValueError(
                    f"{label} has a different pixel grid, extent, or resolution. "
                    "Co-register and align before PCA."
                )

        datasets.append(ds)
        bands.append(band)
        sources.append({
            "label": label,
            "path": str(path),
            "source_band": int(band_index),
        })

    source_types = {band.DataType for band in bands}
    if len(source_types) == 1 and gdal.GetDataTypeName(next(iter(source_types))) in HFA_REAL_TYPES:
        stack_type = next(iter(source_types))
    else:
        stack_type = gdal.GDT_Float64
    return datasets, bands, reference, sources, stack_type


def _chunk_rows(width):
    # Bound a six-band float64 working block to roughly 96 MiB.
    return max(1, min(512, 2_000_000 // max(width, 1)))


def _read_block(bands, yoff, width, height):
    arrays = []
    valid = np.ones((height, width), dtype=bool)
    for band in bands:
        data = band.ReadAsArray(0, yoff, width, height)
        if data is None:
            raise RuntimeError("GDAL failed while reading an input raster block.")
        arrays.append(data)
        valid &= np.isfinite(data)

        nodata = band.GetNoDataValue()
        if nodata is not None:
            try:
                nan_nodata = math.isnan(float(nodata))
            except (TypeError, ValueError):
                nan_nodata = False
            valid &= ~np.isnan(data) if nan_nodata else data != nodata

        mask = band.GetMaskBand().ReadAsArray(0, yoff, width, height)
        if mask is None:
            raise RuntimeError("GDAL failed while reading an input validity mask.")
        valid &= mask != 0

    values = np.stack([a.astype(np.float64, copy=False) for a in arrays], axis=-1)
    valid &= np.isfinite(values).all(axis=-1)
    return values, valid


def _hfa_driver():
    driver = gdal.GetDriverByName("HFA")
    if driver is None:
        raise RuntimeError("This GDAL installation does not include the HFA (.img) driver.")
    return driver


def _create_hfa(path, width, height, count, data_type, grid, overwrite):
    driver = _hfa_driver()
    spill = path.with_suffix(".ige")
    if path.exists() or spill.exists():
        if not overwrite:
            raise FileExistsError(f"Output exists: {path}. Choose another --outdir or use --overwrite.")
        driver.Delete(str(path))
        if spill.exists():
            spill.unlink()

    ds = driver.Create(str(path), width, height, count, data_type)
    if ds is None:
        raise RuntimeError(f"Could not create HFA output: {path}")
    _, _, transform, srs = grid
    ds.SetGeoTransform(transform)
    ds.SetProjection(srs.ExportToWkt())
    return ds


def _preflight_outputs(paths, overwrite):
    if overwrite:
        return
    existing = [str(p) for p in paths if p.exists() or p.with_suffix(".ige").exists()]
    if existing:
        raise FileExistsError(
            "Output(s) already exist: " + ", ".join(existing) +
            ". Choose another --outdir or pass --overwrite."
        )


def _write_stack(bands, sources, stack_ds, width, height):
    rows = _chunk_rows(width)
    for number, (src, source) in enumerate(zip(bands, sources), start=1):
        dst = stack_ds.GetRasterBand(number)
        dst.SetDescription(source["label"])
        nodata = src.GetNoDataValue()
        if nodata is not None:
            dst.SetNoDataValue(float(nodata))
        dst.SetMetadataItem("SOURCE_FILE", source["path"])
        dst.SetMetadataItem("SOURCE_BAND", str(source["source_band"]))
        for yoff in range(0, height, rows):
            ysize = min(rows, height - yoff)
            data = src.ReadAsArray(0, yoff, width, ysize)
            if data is None:
                raise RuntimeError(f"Could not read source pixels for {source['label']}.")
            dst.WriteArray(data, 0, yoff)

    stack_ds.SetMetadataItem(
        "STACK_ORDER", "Pre B2, Pre B3, Pre B4, Post B2, Post B3, Post B4"
    )
    for i, source in enumerate(sources, start=1):
        stack_ds.SetMetadataItem(
            f"STACK_BAND_{i}",
            f"{source['label']} | {source['path']} | source band {source['source_band']}",
        )
    stack_ds.FlushCache()


def _covariance(bands, width, height):
    # Merge per-block means and centered sums of squares (Chan/Welford method).
    count = 0
    mean = np.zeros(6, dtype=np.float64)
    m2 = np.zeros((6, 6), dtype=np.float64)
    rows = _chunk_rows(width)

    for yoff in range(0, height, rows):
        values, valid = _read_block(bands, yoff, width, min(rows, height - yoff))
        samples = values[valid]
        if not samples.size:
            continue
        n = samples.shape[0]
        batch_mean = samples.mean(axis=0)
        centered = samples - batch_mean
        batch_m2 = centered.T @ centered
        if count == 0:
            count, mean, m2 = n, batch_mean, batch_m2
        else:
            combined = count + n
            delta = batch_mean - mean
            m2 += batch_m2 + np.outer(delta, delta) * (count * n / combined)
            mean += delta * (n / combined)
            count = combined

    if count < 2:
        raise ValueError(f"Only {count} common valid pixel(s); at least two are required.")
    cov = m2 / (count - 1)  # Sample covariance (same ddof as numpy.cov).
    cov = (cov + cov.T) / 2.0
    return count, mean, cov


def run(args):
    output_dir = Path(args.outdir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    input_paths = [str(Path(p).expanduser().resolve()) for p in args.inputs]

    stack_path = output_dir / "Pre_Post_Stack.img"
    all_pc_path = output_dir / "PCA_6Components.img"
    pc_paths = [output_dir / f"PC{i}.img" for i in range(1, 7)]
    metadata_path = output_dir / "PCA_metadata.json"
    outputs = [stack_path, all_pc_path, *pc_paths, metadata_path]
    input_set = {Path(p).resolve() for p in input_paths}
    if any(p.resolve() in input_set for p in outputs):
        raise ValueError("An output path would overwrite an input raster; select another --outdir.")
    _preflight_outputs(outputs, args.overwrite)

    datasets, bands, grid, sources, stack_type = _open_inputs(input_paths, args.band_indices)
    width, height, _, _ = grid
    LOG.info("Validated six bands on a matching grid (%d x %d).", width, height)

    stack_ds = _create_hfa(stack_path, width, height, 6, stack_type, grid, args.overwrite)
    try:
        _write_stack(bands, sources, stack_ds, width, height)
    finally:
        stack_ds = None
    LOG.info("Wrote six-band stack: %s", stack_path)

    LOG.info("Calculating covariance from pixels valid in all six bands...")
    sample_count, mean, cov = _covariance(bands, width, height)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    eigenvectors = eigenvectors[:, order]
    total_variance = float(eigenvalues.sum())
    if not np.isfinite(total_variance) or total_variance <= 0:
        raise ValueError("All valid pixels have zero variance; PCA cannot produce meaningful components.")
    explained = eigenvalues / total_variance

    all_ds = _create_hfa(
        all_pc_path, width, height, 6, gdal.GDT_Float32, grid, args.overwrite
    )
    single_ds = []
    try:
        for i in range(6):
            band = all_ds.GetRasterBand(i + 1)
            band.SetDescription(f"PC{i + 1}")
            band.SetNoDataValue(PC_NODATA)
            band.SetMetadataItem("EIGENVALUE", repr(float(eigenvalues[i])))
            band.SetMetadataItem("EXPLAINED_VARIANCE", repr(float(explained[i])))

            one = _create_hfa(
                pc_paths[i], width, height, 1, gdal.GDT_Float32, grid, args.overwrite
            )
            one_band = one.GetRasterBand(1)
            one_band.SetDescription(f"PC{i + 1}")
            one_band.SetNoDataValue(PC_NODATA)
            one_band.SetMetadataItem("EIGENVALUE", repr(float(eigenvalues[i])))
            one_band.SetMetadataItem("EXPLAINED_VARIANCE", repr(float(explained[i])))
            single_ds.append(one)

        rows = _chunk_rows(width)
        for yoff in range(0, height, rows):
            ysize = min(rows, height - yoff)
            values, valid = _read_block(bands, yoff, width, ysize)
            if not valid.any():
                continue
            scores = (values[valid] - mean) @ eigenvectors
            flat_valid = valid.ravel()
            for i in range(6):
                plane = np.full((ysize, width), PC_NODATA, dtype=np.float32)
                plane.ravel()[flat_valid] = scores[:, i].astype(np.float32)
                all_ds.GetRasterBand(i + 1).WriteArray(plane, 0, yoff)
                single_ds[i].GetRasterBand(1).WriteArray(plane, 0, yoff)

        all_ds.FlushCache()
        for ds in single_ds:
            ds.FlushCache()
    finally:
        all_ds = None
        single_ds.clear()

    metadata = {
        "method": "centered covariance-matrix PCA",
        "sample_covariance_ddof": 1,
        "valid_pixel_rule": "finite and valid in all six selected bands",
        "valid_pixel_count": int(sample_count),
        "band_order": list(LABELS),
        "sources": sources,
        "means": mean.tolist(),
        "covariance_matrix": cov.tolist(),
        "eigenvalues_descending": eigenvalues.tolist(),
        "explained_variance_fraction": explained.tolist(),
        "eigenvectors_by_component_column": eigenvectors.tolist(),
        "pc_sign_note": "Eigenvector signs are arbitrary; a component may be inverted versus another software package.",
        "coregistration_note": "CRS and pixel-grid checks cannot prove tie-point/coregistration accuracy.",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    LOG.info("Used %d common valid pixels.", sample_count)
    LOG.info("Created %s and PC1.img through PC6.img.", all_pc_path)
    LOG.info("PC4 explains %.3f%% of covariance variance.", 100.0 * explained[3])
    LOG.info("Review PC4 patterns against both dates and terrain before digitizing any scar.")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Stack pre B2/B3/B4 then post B2/B3/B4, run six-component covariance PCA, "
            "and write separate PC1-PC6 HFA files."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "inputs", nargs=6,
        metavar=("PRE_B2", "PRE_B3", "PRE_B4", "POST_B2", "POST_B3", "POST_B4"),
        help="six band sources in SOP order; repeat a multiband scene path if needed",
    )
    parser.add_argument(
        "--band-indices", nargs=6, type=int, default=[1, 1, 1, 1, 1, 1],
        metavar=("PRE_B2", "PRE_B3", "PRE_B4", "POST_B2", "POST_B3", "POST_B4"),
        help="one-based source band numbers for each input",
    )
    parser.add_argument("--outdir", default="results", help="directory for output files")
    parser.add_argument(
        "--i-confirm-coregistered", action="store_true",
        help="confirm accurate co-registration was completed before PCA",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing outputs in the output directory"
    )
    return parser


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.i_confirm_coregistered:
        parser.error(
            "accurate co-registration is required; after confirming it, rerun with "
            "--i-confirm-coregistered"
        )
    gdal.UseExceptions()
    try:
        return run(args)
    except Exception as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())


