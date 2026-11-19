# LISS-IV Landslide PCA

A local browser GUI for six-component PCA using pre- and post-monsoon LISS-IV scenes. It builds the ERDAS Imagine stack in the SOP order and highlights PC4 for visual landslide interpretation.

## Start on Windows

1. Install Miniforge or Miniconda for Windows if it is not already installed.
2. Double-click install_windows.bat once to create the GDAL and Streamlit environment.
3. Double-click run_windows.bat. It starts the local app and opens it in your browser.

The app runs locally on your PC; the uploaded rasters are processed locally and are not sent to a hosted service.

## Use the app

1. Upload one pre-monsoon and one post-monsoon raster. Each should contain the three corresponding LISS-IV bands. Supported inputs include GeoTIFF, ERDAS Imagine .img, and ZIP files containing one raster plus sidecars.
2. Map source bands to B2, B3, and B4. The default indices are 1, 2, and 3.
3. Confirm that both scenes are accurately co-registered, then click Run six-component PCA.
4. Review the PC4 preview and explained-variance table.
5. Click Save all outputs as a ZIP. Your browser saves it to its configured Downloads folder; enable its “Ask where to save each file” setting to choose a location each time.

The app stops if CRS, raster dimensions, affine grid, extent, or resolution do not match. Matching metadata cannot prove accurate tie-point co-registration, so complete that step before PCA.

## Outputs in the ZIP

- Pre_Post_Stack.img: pre B2, pre B3, pre B4, post B2, post B3, post B4.
- PCA_6Components.img: all six PC score bands.
- PC1.img through PC6.img: each component as a separate one-band raster.
- PCA_metadata.json: source mapping, valid-pixel count, band means, covariance matrix, eigenvalues, explained variance, and eigenvectors.
- Any HFA sidecar files created for large rasters.

PCA is centered but not standardized: it uses the sample covariance matrix (denominator n - 1) of the raw input bands. Components are ordered by descending eigenvalue. PC4 is an exploratory indicator, not an automatic landslide classification. Inspect candidate patches on slopes against both dates and terrain, then confirm and digitize scars manually. Eigenvector signs are arbitrary, so PC4 may appear inverted relative to ERDAS Imagine.

## Bulk 2026 scene preparation

The browser app also has a **Bulk Scene Preparation** page in the sidebar. Enter the local pre- and post-monsoon parent folders and an output folder, then click **Analyze folders and match path/row**. It recursively reads scene metadata, inventories raster files, and pairs acquisitions by matching path and row:

- Pre-monsoon window: January 1 through May 31, 2026.
- Post-monsoon window: October 1 through December 31, 2026.
- NRSC product metadata fields Path, Row, and DateOfPass are read from BAND_META.txt or .meta.
- BAND2.tif, BAND3.tif, and BAND4.tif are grouped in that order.
- Candidate pairs show how much of each scene overlaps. Low-overlap pairs require review.
- Selected pairs are reprojected/resampled to one CRS and pixel grid, cropped to the common overlap, and saved together under Path_####_Row_#### folders.

The bulk workflow is also available in bulk_scene_preparation.ipynb. Open it in JupyterLab from the same Conda environment, edit the three folder paths at the top, and run the cells in order.

The supplied NRSC example is already a GEOREF product. If a GeoTIFF is missing its georeferencing tags, the tool can rebuild a transform only when metadata explicitly identifies a GEOREF product and contains a UTM zone and product map bounds. RPC-only or unreferenced scenes without usable control information are reported for review; path/row alone cannot georeference pixels.

Reprojection, resampling, and cropping make a common grid; they do not prove or fix sub-pixel image-to-image co-registration. Confirm co-registration before PCA or landslide change interpretation.

## Command-line alternative

The PCA calculation remains available in liss4_change_pca.py. Bulk metadata matching and common-grid preparation are provided by bulk_scene_prep.py.


