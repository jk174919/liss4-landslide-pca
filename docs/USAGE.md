# User guide

## Input requirements

Use one pre-monsoon and one post-monsoon LISS-IV scene. Paste local paths into the browser app; nothing is uploaded. Each scene can be a multi-band raster file or a folder containing one multi-band raster. For NRSC band-separated products, use the product folder containing BAND2.tif, BAND3.tif, and BAND4.tif. The app groups those files in B2/B3/B4 order.

Both dates must be accurately co-registered before PCA. The app checks the CRS, image dimensions, affine geotransform, pixel size, and extent. Those metadata checks do not establish image tie-point accuracy. The app does not reproject or resample inputs.

## Run and save

Double-click run_windows.bat. On first use, install_windows.bat creates the local Conda environment from environment.yml; Miniforge or Miniconda must be installed. The local Streamlit server opens the interface in your browser.

Paste the pre and post raster or product-folder paths. Map source band indices when the source is a multi-band raster. Confirm co-registration, set an output folder, and click Run six-component PCA. The stack, six-component raster, PC1-PC6 rasters, and PCA_metadata.json are written directly to disk. If the output folder is blank, the app creates LISS4_PCA_Results beside the pre-scene folder.

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


