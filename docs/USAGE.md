# User guide

## Input requirements

Use one pre-monsoon and one post-monsoon LISS-IV scene. Each scene must contain corresponding B2, B3, and B4 bands. The app defaults to source indices 1, 2, and 3; change them in the band mapping controls if needed. If a raster depends on sidecars, upload it as a ZIP containing one primary raster and its associated files.

Both dates must be accurately co-registered before PCA. The app checks the CRS, image dimensions, affine geotransform, pixel size, and extent. Those metadata checks do not establish image tie-point accuracy. The app does not reproject or resample inputs.

## Run and save

Double-click run_windows.bat. On first use, the launcher creates the local Conda environment from environment.yml; Miniforge or Miniconda must be installed. The local Streamlit server opens the interface in your browser.

Select the two scenes, map the three source band indices for each date, check the co-registration confirmation, and click Run six-component PCA. The app previews PC4 and shows explained variance. Download the ZIP to save the complete set of outputs wherever you choose.

## Command line

For six separate single-band rasters:

    python liss4_change_pca.py pre_B2.tif pre_B3.tif pre_B4.tif post_B2.tif post_B3.tif post_B4.tif --outdir results --i-confirm-coregistered

For two three-band scenes with B2/B3/B4 in positions 1/2/3:

    python liss4_change_pca.py pre.img pre.img pre.img post.img post.img post.img --band-indices 1 2 3 1 2 3 --outdir results --i-confirm-coregistered


