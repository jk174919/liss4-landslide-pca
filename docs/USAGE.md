# User guide

## Input requirements

Use one pre-monsoon and one post-monsoon LISS-IV scene. Each scene must contain corresponding B2, B3, and B4 bands. The app defaults to source indices 1, 2, and 3; change them in the band mapping controls if needed. If a raster depends on sidecars, upload it as a ZIP containing one primary raster and its associated files.

Both dates must be accurately co-registered before PCA. The app checks the CRS, image dimensions, affine geotransform, pixel size, and extent. Those metadata checks do not establish image tie-point accuracy. The app does not reproject or resample inputs.

## Run and save

Double-click run_windows.bat. On first use, the launcher creates the local Conda environment from environment.yml; Miniforge or Miniconda must be installed. The local Streamlit server opens the interface in your browser.

Select the two scenes, map the three source band indices for each date, check the co-registration confirmation, and click Run six-component PCA. The app previews PC4 and shows explained variance. Download the ZIP to save the complete set of outputs. The browser uses its configured Downloads folder; enable its “Ask where to save each file” setting to choose a location each time.

## Bulk scene matching and common-grid preparation

Open Bulk Scene Preparation in the app sidebar. Select the local pre- and post-monsoon parent folders and an output folder. The scanner recursively reads NRSC BAND_META.txt or .meta files and looks for Path, Row, DateOfPass, ProdType, ZoneNo, and raster dimensions/CRS. The 2026 pre window is January through May; the post window is October through December. Separate BAND2.tif, BAND3.tif, and BAND4.tif files are treated as one scene in that order.

The match report pairs only the same path/row. It scores footprint overlap, recommends the best-coverage date pair for each path/row, and marks pairs below the configured coverage threshold for review. Selected pairs are written together under a path/row output folder. Each pair contains a pre TIFF, a post TIFF, and pair_manifest.json. The two rasters are warped to the same CRS, pixel size, dimensions, and common overlap extent. The default target CRS and pixel size come from the pre scene.

For separate BAND2/BAND3/BAND4 inputs, each aligned output is a three-band GeoTIFF in the band order expected by the PCA page.

To run the notebook, double-click run_notebook_windows.bat. It opens bulk_scene_preparation.ipynb in JupyterLab from the installed environment. Edit PRE_ROOT, POST_ROOT, and OUTPUT_ROOT in the first code cell.

The program can reconstruct a missing GeoTIFF transform only when metadata explicitly says GEOREF and includes UTM zone and map bounds. It does not auto-georeference a scene from path/row alone. RPC-only or missing-georeference products that lack supported map bounds need the supplier's sensor model, a suitable DEM, or ground control points before alignment.

Grid alignment is not image-to-image co-registration. Verify tie-point alignment separately before running PCA or interpreting landslide change. The pair report and manifest record the common-grid operation and coverage.

## Command line

For six separate single-band rasters:

    python liss4_change_pca.py pre_B2.tif pre_B3.tif pre_B4.tif post_B2.tif post_B3.tif post_B4.tif --outdir results --i-confirm-coregistered

For two three-band scenes with B2/B3/B4 in positions 1/2/3:

    python liss4_change_pca.py pre.img pre.img pre.img post.img post.img post.img --band-indices 1 2 3 1 2 3 --outdir results --i-confirm-coregistered


