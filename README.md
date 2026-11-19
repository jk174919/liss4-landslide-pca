# LISS-IV Landslide PCA

A local browser GUI for six-component PCA using pre- and post-monsoon LISS-IV scenes. It builds the ERDAS Imagine stack in the SOP order and highlights PC4 for visual landslide interpretation.

## Start on Windows

1. Install Miniforge or Miniconda for Windows if it is not already installed.
2. Double-click install_windows.bat once to create the GDAL and Streamlit environment.
3. Double-click run_windows.bat. It starts the local app and opens it in your browser.

The app runs locally on your PC. It reads the folder paths you enter, processes rasters on your disk, and writes results directly to your local output folder.

## Use the app

1. Paste the pre- and post-monsoon product-folder paths. For the NRSC layout, each folder can contain BAND2.tif, BAND3.tif, BAND4.tif, and its metadata file; the app reads all three bands automatically in B2/B3/B4 order. A single multi-band raster is supported too.
2. The app checks that the metadata Path/Row values match and that all six inputs have the same CRS, dimensions, and affine pixel grid. If metadata is missing, you must manually confirm the Path/Row.
3. Confirm that both scenes are accurately co-registered, then click Run six-component PCA.
4. Enter an output folder on your PC. If left blank, the app creates LISS4_PCA_Results inside the matching Path/Row folder for organized inputs, or beside the pre-scene folder otherwise. It stays on the same drive.
5. Review PC4 and the explained-variance table. All rasters and metadata are already saved in that output folder.

The app stops if CRS, raster dimensions, affine grid, extent, or resolution do not match. Matching metadata cannot prove accurate tie-point co-registration, so complete that step before PCA.

## PCA output files

- Pre_Post_Stack.img: pre B2, pre B3, pre B4, post B2, post B3, post B4.
- PCA_6Components.img: all six PC score bands.
- PC1.img through PC6.img: each component as a separate one-band raster.
- PCA_metadata.json: source mapping, valid-pixel count, band means, covariance matrix, eigenvalues, explained variance, and eigenvectors.
- Any HFA sidecar files created for large rasters.

PCA is centered but not standardized: it uses the sample covariance matrix (denominator n - 1) of the raw input bands. Components are ordered by descending eigenvalue. PC4 is an exploratory indicator, not an automatic landslide classification. Inspect candidate patches on slopes against both dates and terrain, then confirm and digitize scars manually. Eigenvector signs are arbitrary, so PC4 may appear inverted relative to ERDAS Imagine.

## Organize source folders by Path/Row

Open **Sort Products by Path/Row** in the browser app sidebar. Enter one or more local parent folder paths (one per line) and a new output folder on the same drive. The scanner reads product metadata recursively and groups complete 2026 pre/post products by metadata Path and Row. Pre dates are January through May; post dates are October through December. It requires either separate BAND2/BAND3/BAND4 files or one raster with at least three bands.

Review the match table, choose the Path/Row groups, and confirm **Move selected matched folders**. The app moves each complete original product folder, including all image bands, metadata, and sidecars, into `Path_####_Row_####/Pre_Monsoon/` or `Post_Monsoon/`. Unmatched folders remain where they are. It never replaces an existing product folder, requires the output and source folders to be on the same drive, and writes `path_row_folder_index.csv` in the output root.

After sorting, paste the exact pre- and post-monsoon product-folder paths into the PCA page. The PCA page automatically loads all three separate bands from each folder in SOP order, verifies matching metadata Path/Row, checks the six rasters' georeferencing and pixel grid, then writes the six-band stack and PCA outputs directly to the selected local folder.

The same sorting workflow is available in `bulk_pathrow_sort.ipynb`. Edit `SOURCE_ROOTS` and `OUTPUT_ROOT`, inspect the product and move-preview tables, then set `CONFIRM_MOVE = True` and run the final cell to move the selected folders.

## Bulk 2026 common-grid preparation

The browser app also has a **Bulk Scene Preparation** page in the sidebar. This page creates aligned raster outputs for pre/post scenes whose georeferenced extents or pixel grids need a common grid. Enter the local pre- and post-monsoon parent folders and an output folder, then click **Analyze folders and match path/row**. It recursively reads scene metadata and inventories raster files:

- Pre-monsoon window: January 1 through May 31, 2026.
- Post-monsoon window: October 1 through December 31, 2026.
- NRSC product metadata fields Path, Row, and DateOfPass are read from BAND_META.txt or .meta.
- BAND2.tif, BAND3.tif, and BAND4.tif are grouped in that order.
- Candidate pairs show how much of each scene overlaps. The best-coverage pair for each path/row is recommended; low-overlap pairs require review.
- Selected pairs are reprojected/resampled to one CRS and pixel grid, cropped to the common overlap, and saved together under Path_####_Row_#### folders.
- For separate BAND2/BAND3/BAND4 products, each output is a three-band GeoTIFF in the PCA GUI's expected order.

Common-grid preparation is also available in `bulk_scene_preparation.ipynb`. Double-click `run_notebook_windows.bat` to open the notebooks in JupyterLab. Edit the paths at the top of the notebook you want, then run its cells in order.

The supplied NRSC example is already a GEOREF product. If a GeoTIFF is missing its georeferencing tags, the tool can rebuild a transform only when metadata explicitly identifies a GEOREF product and contains a UTM zone and product map bounds. RPC-only or unreferenced scenes without usable control information are reported for review; path/row alone cannot georeference pixels.

Reprojection, resampling, and cropping make a common grid; they do not prove or fix sub-pixel image-to-image co-registration. Confirm co-registration before PCA or landslide change interpretation.

## Command-line alternative

The PCA calculation remains available in liss4_change_pca.py. Bulk metadata matching and common-grid preparation are provided by bulk_scene_prep.py.


