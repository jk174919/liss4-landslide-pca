# LISS-IV Pre/Post Monsoon PCA for Landslide Interpretation

This command-line tool creates the six-band ERDAS Imagine stack in the SOP order, runs covariance-based six-component PCA, and writes each component as its own .img file.

## Checks and prerequisites

All six selected bands must share exactly the same dimensions, CRS, affine geotransform, pixel size, and extent. The PCA uses only pixels that are finite and valid in all six bands, respecting each band's NoData value and GDAL mask.

Grid metadata cannot prove accurate image co-registration at ground control points. Co-register the dates before running the script. The script requires the explicit --i-confirm-coregistered acknowledgement and stops if the grid checks fail. It does not reproject or resample inputs.

## Outputs

- Pre_Post_Stack.img: six bands in SOP order.
- PCA_6Components.img: all six component score bands.
- PC1.img through PC6.img: one component per file.
- PCA_metadata.json: valid-pixel count, means, covariance matrix, eigenvalues, explained variance, eigenvectors, and source-band mapping.

The stack preserves the input type when all six bands share an HFA-compatible real type. Mixed or unsupported types are promoted to Float64. PCA score rasters use Float32 and mark invalid pixels as NoData.

## Install

A conda-forge environment is the simplest way to install GDAL's Python bindings with matching native libraries:

    conda create -n liss4-pca -c conda-forge python=3.12 numpy gdal
    conda activate liss4-pca

An existing OSGeo4W installation also works when its Python environment can import both osgeo.gdal and NumPy.

## Run with six single-band files

    python liss4_change_pca.py pre_B2.tif pre_B3.tif pre_B4.tif post_B2.tif post_B3.tif post_B4.tif --outdir results --i-confirm-coregistered

## Run with two three-band images

If each source image stores B2, B3, and B4 as bands 1, 2, and 3, pass each image three times and select the band numbers:

    python liss4_change_pca.py pre.img pre.img pre.img post.img post.img post.img --band-indices 1 2 3 1 2 3 --outdir results --i-confirm-coregistered

Set the six --band-indices values to the actual one-based positions when the inputs use different band positions.

Use --overwrite to replace existing outputs. Without it, the program refuses to replace existing files.

## PCA method and interpretation

PCA is centered but not standardized: the script eigendecomposes the sample covariance matrix (denominator n - 1) of the six raw input bands. Components are ordered by descending eigenvalue. Use consistent radiometric scaling across dates and bands.

Eigenvector signs are mathematically arbitrary, so PC4 may be inverted relative to ERDAS Imagine. Compare spatial patterns rather than whether a patch is bright or dark. PC4 is an exploratory indicator from the SOP; it does not automatically classify landslides. Inspect contrasting patches on slopes and compare them with both dates and terrain/context data. Confirm scars visually and digitize only after review.

## Requirements

- Python 3.10 or newer
- GDAL Python bindings with the HFA driver
- NumPy 1.24 or newer

See requirements.txt. Use conda-forge or OSGeo4W to install GDAL so the Python bindings match the native GDAL libraries.


