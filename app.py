#!/usr/bin/env python3
"""Local, path-based Streamlit GUI for the LISS-IV six-band PCA workflow."""

from __future__ import annotations

import json
import hashlib
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import streamlit as st
from osgeo import gdal

import liss4_change_pca as pca
import bulk_scene_prep as bulk

gdal.UseExceptions()
LOG = logging.getLogger("liss4_pca_app")
RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".jp2", ".vrt", ".dat", ".bil", ".nc"}


def _inspect_raster(path: Path) -> dict:
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise ValueError(f"GDAL could not open {path}. Check the raster and its sidecar files.")
    details = {
        "path": str(path.resolve()),
        "width": int(dataset.RasterXSize),
        "height": int(dataset.RasterYSize),
        "bands": int(dataset.RasterCount),
        "crs": bool(dataset.GetProjectionRef()),
    }
    dataset = None
    return details


def _scene_identity(scene_directory: Path) -> dict:
    try:
        return bulk.product_identity(scene_directory)
    except (OSError, ValueError):
        return {"path": None, "row": None, "date": None, "metadata_files": []}


def _default_output_dir(scene_directory: Path) -> Path:
    season_directory = scene_directory.parent
    pathrow_directory = season_directory.parent
    if (
        season_directory.name.casefold() == "pre_monsoon"
        and re.fullmatch(r"path_\d+_row_\d+", pathrow_directory.name, re.IGNORECASE)
    ):
        return pathrow_directory / "LISS4_PCA_Results"
    return season_directory / "LISS4_PCA_Results"


def _resolve_scene_path(raw_path: str, scene_label: str) -> dict | None:
    """Accept either one multi-band raster or a folder of BAND2/3/4 rasters."""
    raw_path = (raw_path or "").strip().strip('"')
    if not raw_path:
        return None
    requested = Path(raw_path).expanduser()
    if not requested.exists():
        raise FileNotFoundError(f"{scene_label} path does not exist: {requested}")

    if requested.is_dir():
        band_files = {}
        preferred = {}
        for item in sorted(requested.iterdir(), key=lambda path: path.name.casefold()):
            if not item.is_file():
                continue
            match = re.fullmatch(r"BAND[_-]?([234])(?:_RPC)?\.(?:tif|tiff)", item.name, re.IGNORECASE)
            if match:
                number = int(match.group(1))
                is_preferred = re.fullmatch(rf"BAND[_-]?{number}\.(?:tif|tiff)", item.name, re.IGNORECASE) is not None
                if number not in band_files or (is_preferred and not preferred[number]):
                    band_files[number] = item
                    preferred[number] = is_preferred
        if set(band_files) == {2, 3, 4}:
            ordered = [band_files[index] for index in (2, 3, 4)]
            details = [_inspect_raster(path) for path in ordered]
            if any(item["bands"] < 1 for item in details):
                raise ValueError(f"{scene_label} BAND2/BAND3/BAND4 files must each contain raster data.")
            first = details[0]
            identity = _scene_identity(requested)
            return {
                "label": scene_label,
                "scene_directory": requested.resolve(),
                "inputs": [str(path.resolve()) for path in ordered],
                "band_indices": [1, 1, 1],
                "band_count": 3,
                "separate_bands": True,
                "width": first["width"],
                "height": first["height"],
                "crs": first["crs"],
                "description": "BAND2, BAND3, and BAND4 folder",
                "metadata_path": identity["path"],
                "metadata_row": identity["row"],
                "metadata_date": identity["date"],
                "metadata_files": identity["metadata_files"],
            }

        rasters = sorted(
            item for item in requested.iterdir()
            if item.is_file() and item.suffix.lower() in RASTER_EXTENSIONS
        )
        if len(rasters) != 1:
            raise ValueError(
                f"{scene_label}: folder must contain one multi-band raster, or the three separate "
                "files BAND2.tif, BAND3.tif, and BAND4.tif."
            )
        raster_path = rasters[0]
        scene_directory = requested.resolve()
    elif requested.is_file() and requested.suffix.lower() in RASTER_EXTENSIONS:
        raster_path = requested
        scene_directory = requested.parent.resolve()
    else:
        raise ValueError(f"{scene_label}: enter a raster file or a scene folder containing its raster bands.")

    details = _inspect_raster(raster_path)
    if details["bands"] < 3:
        raise ValueError(
            f"{scene_label} has {details['bands']} band(s). Use a three-band raster or a folder "
            "with BAND2.tif, BAND3.tif, and BAND4.tif."
        )
    identity = _scene_identity(scene_directory)
    return {
        "label": scene_label,
        "scene_directory": scene_directory,
        "inputs": [details["path"]] * 3,
        "band_indices": [1, 2, 3],
        "band_count": details["bands"],
        "separate_bands": False,
        "width": details["width"],
        "height": details["height"],
        "crs": details["crs"],
        "description": raster_path.name,
        "metadata_path": identity["path"],
        "metadata_row": identity["row"],
        "metadata_date": identity["date"],
        "metadata_files": identity["metadata_files"],
    }


def _band_mapping(scene: dict, key_prefix: str) -> list[int]:
    if scene["separate_bands"]:
        st.caption(f"{scene['label']}: using separate BAND2, BAND3, and BAND4 files in that order.")
        return [1, 1, 1]

    with st.expander(f"{scene['label']}: source band mapping", expanded=False):
        options = list(range(1, scene["band_count"] + 1))
        cols = st.columns(3)
        selected = []
        for col, label, default in zip(cols, ("B2", "B3", "B4"), (1, 2, 3)):
            with col:
                selected.append(
                    st.selectbox(
                        f"{label} source band",
                        options,
                        index=min(default - 1, len(options) - 1),
                        key=f"{key_prefix}_{label}",
                    )
                )
    if len(set(selected)) != 3:
        st.error(f"{scene['label']}: B2, B3, and B4 must use three distinct source bands.")
        return []
    return selected


def _grid_check(pre_scene, post_scene, pre_indices, post_indices):
    inputs = pre_scene["inputs"] + post_scene["inputs"]
    indices = pre_indices + post_indices
    datasets, bands, grid, sources, _ = pca._open_inputs(inputs, indices)
    width, height, _, _ = grid
    del bands, datasets, sources
    return width, height


def _pc4_preview(path: Path):
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError("GDAL could not reopen the PC4 output for preview.")
    band = dataset.GetRasterBand(1)
    width, height = dataset.RasterXSize, dataset.RasterYSize
    out_width, out_height = min(width, 1400), min(height, 1400)
    array = band.ReadAsArray(buf_xsize=out_width, buf_ysize=out_height).astype(np.float32)
    mask = band.GetMaskBand().ReadAsArray(buf_xsize=out_width, buf_ysize=out_height) != 0
    mask &= np.isfinite(array)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        mask &= array != nodata
    dataset = None
    if not mask.any():
        raise ValueError("PC4 contains no valid pixels to preview.")
    low, high = np.percentile(array[mask], (2, 98))
    if high <= low:
        high = low + 1.0
    image = np.clip((array - low) * (255.0 / (high - low)), 0, 255).astype(np.uint8)
    image[~mask] = 255
    return image


def _render_results(result_dir: Path):
    metadata_path = result_dir / "PCA_metadata.json"
    if not metadata_path.exists():
        return
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    explained = metadata["explained_variance_fraction"]
    st.divider()
    st.subheader("PCA results saved on this PC")
    st.caption(str(result_dir))
    metrics = st.columns(3)
    metrics[0].metric("Common valid pixels", f"{metadata['valid_pixel_count']:,}")
    metrics[1].metric("PC4 explained variance", f"{100.0 * explained[3]:.3f}%")
    metrics[2].metric("Components", "6")
    left, right = st.columns([1.5, 1])
    with left:
        st.markdown("#### PC4 preview")
        st.image(
            _pc4_preview(result_dir / "PC4.img"),
            caption="PC4 contrast stretch (2nd–98th percentile)",
            use_container_width=True,
        )
    with right:
        st.markdown("#### Explained variance")
        table = [
            {"Component": f"PC{i + 1}", "Variance (%)": round(100.0 * value, 4)}
            for i, value in enumerate(explained)
        ]
        st.dataframe(table, hide_index=True, use_container_width=True)
        st.info(
            "PC4 is an exploratory change indicator. Review contrasting slope patches "
            "against both dates and terrain before digitizing a landslide scar."
        )
    st.success("All PCA rasters and metadata are already saved in the output folder above.")


st.set_page_config(
    page_title="LISS-IV Landslide PCA",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {max-width: 1220px; padding-top: 1.5rem; padding-bottom: 3rem;}
      .hero {padding: 1.3rem 1.6rem; border-radius: 16px;
             background: linear-gradient(120deg, #e8f4f1 0%, #f5f8f7 60%, #e9f0f8 100%);
             border: 1px solid #d7e6e3; margin-bottom: 1.1rem;}
      .hero-kicker {font-size: .78rem; letter-spacing: .12em; text-transform: uppercase;
                    color: #26766f; font-weight: 700;}
      .hero-title {font-size: 2rem; font-weight: 750; color: #173c3a; margin: .2rem 0;}
      .hero-copy {color: #526b69; margin: 0;}
      div[data-testid="stMetricValue"] {color: #126b64;}
    </style>
    <div class="hero">
      <div class="hero-kicker">Resourcesat-2 / LISS-IV</div>
      <div class="hero-title">Pre- and Post-Monsoon Landslide PCA</div>
      <p class="hero-copy">Enter local scene paths, build the SOP stack, calculate six components, and inspect PC4.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Workflow")
    st.markdown("1. Paste the pre- and post-monsoon scene paths.")
    st.markdown("2. Confirm source band mapping and grid checks.")
    st.markdown("3. Confirm co-registration and run PCA.")
    st.markdown("4. Choose the output folder or use the same-disk default.")
    st.divider()
    st.caption(
        "Paths are read by the local app on this PC. Nothing is uploaded. "
        "Grid checks cannot prove tie-point co-registration."
    )

st.markdown("### 1. Enter local scene paths")
st.caption(
    "For NRSC products, paste the product-folder path containing BAND_META.txt and BAND2.tif, "
    "BAND3.tif, and BAND4.tif. The app reads all three files automatically in that order. "
    "A single multi-band raster is also supported."
)
pre_col, post_col = st.columns(2, gap="large")
with pre_col:
    st.markdown("#### Pre-monsoon")
    pre_text = st.text_input(
        "Pre-monsoon raster or folder path",
        placeholder=r"F:\SILAS\Pre_Monsoon_2026\R2...",
        key="pre_scene_path_text",
    )
with post_col:
    st.markdown("#### Post-monsoon")
    post_text = st.text_input(
        "Post-monsoon raster or folder path",
        placeholder=r"F:\SILAS\Post_Monsoon_2026\R2...",
        key="post_scene_path_text",
    )
output_text = st.text_input(
    "Output folder on this PC (leave blank for the same-drive default)",
    placeholder=r"F:\SILAS\PCA_Results_2026",
    key="pca_output_path_text",
)
pre_scene = post_scene = None
pre_error = post_error = None
try:
    pre_scene = _resolve_scene_path(pre_text, "Pre-monsoon")
except Exception as exc:
    pre_error = str(exc)
try:
    post_scene = _resolve_scene_path(post_text, "Post-monsoon")
except Exception as exc:
    post_error = str(exc)

if pre_error:
    st.error(pre_error)
if post_error:
    st.error(post_error)
for scene in (pre_scene, post_scene):
    if scene:
        st.caption(
            f"{scene['label']}: {scene['description']} | {scene['width']:,} × {scene['height']:,} pixels | "
            f"{scene['band_count']} PCA bands | CRS {'present' if scene['crs'] else 'missing'}"
        )
        if scene.get("metadata_path") is not None and scene.get("metadata_row") is not None:
            identity_text = (
                f"metadata Path {scene['metadata_path']:04d} / Row {scene['metadata_row']:04d}"
            )
            if scene.get("metadata_date"):
                identity_text += f" | Date {scene['metadata_date']}"
            st.caption(f"{scene['label']} product identity: {identity_text}")
        else:
            st.caption(f"{scene['label']}: Path/Row could not be confirmed from product metadata.")

metadata_pair_ready = False
if pre_scene and post_scene:
    pre_identity = (pre_scene.get("metadata_path"), pre_scene.get("metadata_row"))
    post_identity = (post_scene.get("metadata_path"), post_scene.get("metadata_row"))
    if all(value is not None for value in pre_identity + post_identity):
        if pre_identity == post_identity:
            metadata_pair_ready = True
            st.success(
                f"Metadata confirms both inputs are Path {pre_identity[0]:04d} / Row {pre_identity[1]:04d}."
            )
        else:
            st.error(
                "The product metadata Path/Row values do not match. Select pre- and post-monsoon "
                "product folders from the same Path/Row before running PCA."
            )
    else:
        st.warning("Path/Row could not be read from both product metadata files.")
        metadata_pair_ready = st.checkbox(
            "I manually verified that these pre- and post-monsoon scenes have the same Path/Row.",
            key="confirm_pathrow_without_metadata",
        )

pre_indices = _band_mapping(pre_scene, "pre") if pre_scene else []
post_indices = _band_mapping(post_scene, "post") if post_scene else []
signature_parts = [pre_text.strip(), post_text.strip(), output_text.strip()]
signature_parts.extend(str(index) for index in pre_indices + post_indices)
current_signature = hashlib.sha256("|".join(signature_parts).encode("utf-8")).hexdigest()
if (
    st.session_state.get("pca_input_signature") is not None
    and st.session_state.get("pca_input_signature") != current_signature
):
    st.session_state.pop("pca_result_dir", None)

if pre_scene:
    default_output_dir = _default_output_dir(pre_scene["scene_directory"])
    st.caption(f"Default save location on this PC: {default_output_dir}")
st.caption(
    "To sort many product folders by metadata Path/Row, open **Sort Products by Path/Row** in the sidebar. "
    "If selected rasters need a common grid, use **Bulk Scene Preparation** to make aligned outputs first."
)

grid_ready = False
if pre_scene and post_scene and pre_indices and post_indices:
    try:
        grid_width, grid_height = _grid_check(pre_scene, post_scene, pre_indices, post_indices)
        grid_ready = True
        st.success(
            f"Selected bands share the same CRS and pixel grid ({grid_width:,} × {grid_height:,}). "
            "This is a metadata check only."
        )
    except Exception as exc:
        st.error(f"Grid check failed: {exc}")

st.markdown("### 2. Confirm the prerequisite")
coregistered = st.checkbox(
    "I confirm that the pre- and post-monsoon scenes have been accurately co-registered.",
    key="confirm_coregistered",
)
if not coregistered:
    st.warning("Complete and verify image co-registration before running PCA.")

overwrite = st.checkbox("Replace existing PCA outputs in the output folder", value=False)
can_run = bool(
    grid_ready and metadata_pair_ready and coregistered and pre_indices and post_indices
)
run_clicked = st.button(
    "Run six-component PCA and save to my PC",
    type="primary",
    disabled=not can_run,
    use_container_width=True,
)

if run_clicked:
    try:
        output_dir = (
            Path(output_text.strip()).expanduser()
            if output_text.strip()
            else _default_output_dir(pre_scene["scene_directory"])
        )
        args = SimpleNamespace(
            inputs=pre_scene["inputs"] + post_scene["inputs"],
            band_indices=pre_indices + post_indices,
            outdir=str(output_dir),
            overwrite=overwrite,
        )
        with st.spinner("Stacking the scenes and calculating covariance PCA..."):
            status = pca.run(args)
            if status != 0:
                raise RuntimeError(f"PCA returned status {status}.")
        result_dir = Path(args.outdir).expanduser().resolve()
        st.session_state["pca_result_dir"] = str(result_dir)
        st.session_state["pca_input_signature"] = current_signature
        st.success(f"PCA completed. Results were saved to {result_dir}")
    except Exception as exc:
        LOG.exception("PCA failed")
        st.error(f"PCA failed: {exc}")

result_dir_text = st.session_state.get("pca_result_dir")
if result_dir_text and st.session_state.get("pca_input_signature") == current_signature:
    _render_results(Path(result_dir_text))

