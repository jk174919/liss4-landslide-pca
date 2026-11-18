#!/usr/bin/env python3
"""Local Streamlit interface for the LISS-IV six-band PCA workflow."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import streamlit as st
from osgeo import gdal

import liss4_change_pca as pca

gdal.UseExceptions()
LOG = logging.getLogger("liss4_pca_app")
RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".jp2", ".vrt", ".dat", ".bil", ".nc"}


def _session_root() -> Path:
    if "_liss4_tempdir" not in st.session_state:
        tempdir = tempfile.TemporaryDirectory(prefix="liss4_pca_")
        st.session_state["_liss4_tempdir"] = tempdir
        st.session_state["_liss4_root"] = tempdir.name
    return Path(st.session_state["_liss4_root"])


def _extract_zip_safely(archive_bytes: bytes, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    extracted = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            target = (destination / member.filename).resolve()
            try:
                common = os.path.commonpath((str(root), str(target)))
            except ValueError as exc:
                raise ValueError("The ZIP contains a path outside its scene folder.") from exc
            if common != str(root):
                raise ValueError("The ZIP contains a path outside its scene folder.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
            extracted.append(target)
    return extracted


def _save_scene(upload, slot: str):
    if upload is None:
        return None, None

    raw = upload.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    path_key = f"_{slot}_path"
    digest_key = f"_{slot}_digest"
    if (
        st.session_state.get(digest_key) == digest
        and st.session_state.get(path_key)
        and Path(st.session_state[path_key]).exists()
    ):
        return Path(st.session_state[path_key]), digest

    folder = _session_root() / "inputs" / f"{slot}_{digest[:16]}"
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.name).suffix.lower()

    if suffix == ".zip":
        candidates = [
            path for path in _extract_zip_safely(raw, folder)
            if path.suffix.lower() in RASTER_EXTENSIONS
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"{upload.name}: ZIP must contain exactly one primary raster "
                "(.img, .tif, .jp2, .dat, or similar), plus any sidecar files."
            )
        raster_path = candidates[0]
    else:
        if suffix not in RASTER_EXTENSIONS:
            raise ValueError(
                f"{upload.name}: unsupported file type. Upload a GDAL-readable raster "
                "or a ZIP containing one raster and its sidecars."
            )
        raster_path = folder / ("scene" + suffix)
        raster_path.write_bytes(raw)

    st.session_state[path_key] = str(raster_path)
    st.session_state[digest_key] = digest
    return raster_path, digest


def _inspect_raster(path: Path):
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise ValueError(f"GDAL could not open {path.name}. Check the file and any sidecars.")
    summary = {
        "width": dataset.RasterXSize,
        "height": dataset.RasterYSize,
        "bands": dataset.RasterCount,
        "crs": bool(dataset.GetProjectionRef()),
        "geotransform": dataset.GetGeoTransform(can_return_null=True),
    }
    dataset = None
    return summary


def _band_pickers(scene_label: str, count: int, key_prefix: str):
    if count < 3:
        st.error(f"{scene_label} has {count} band(s); three corresponding bands are required.")
        return None

    with st.expander(f"{scene_label}: map source bands to B2, B3, and B4", expanded=False):
        options = list(range(1, count + 1))
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
        st.error(f"{scene_label}: B2, B3, and B4 must use three distinct source bands.")
        return None
    return selected


def _grid_check(pre_path, post_path, pre_indices, post_indices):
    paths = [str(pre_path)] * 3 + [str(post_path)] * 3
    indices = list(pre_indices) + list(post_indices)
    datasets, bands, grid, sources, _ = pca._open_inputs(paths, indices)
    width, height, _, _ = grid
    del bands, datasets, sources
    return width, height


def _pc4_preview(path: Path):
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError("GDAL could not reopen the PC4 output for preview.")
    band = dataset.GetRasterBand(1)
    width, height = dataset.RasterXSize, dataset.RasterYSize
    out_width = min(width, 1400)
    out_height = min(height, 1400)
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


def _build_download_zip(result_dir: Path) -> Path:
    zip_path = result_dir / "LISS4_PCA_Results.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=2) as archive:
        for item in sorted(result_dir.iterdir()):
            if item.is_file() and item != zip_path:
                archive.write(item, arcname=item.name)
    return zip_path


def _render_results(current_signature: str):
    if st.session_state.get("_result_signature") != current_signature:
        return
    result_dir = Path(st.session_state["_result_dir"])
    zip_path = Path(st.session_state["_result_zip"])
    metadata = json.loads((result_dir / "PCA_metadata.json").read_text(encoding="utf-8"))
    explained = metadata["explained_variance_fraction"]
    pc4_path = result_dir / "PC4.img"

    st.divider()
    st.subheader("PCA results")
    metrics = st.columns(3)
    metrics[0].metric("Common valid pixels", f"{metadata['valid_pixel_count']:,}")
    metrics[1].metric("PC4 explained variance", f"{100.0 * explained[3]:.3f}%")
    metrics[2].metric("Components", "6")

    left, right = st.columns([1.5, 1])
    with left:
        st.markdown("#### PC4 preview")
        st.image(_pc4_preview(pc4_path), caption="PC4 contrast stretch (2nd–98th percentile)", use_container_width=True)
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

    zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
    st.download_button(
        "Save all outputs as a ZIP",
        data=zip_path.read_bytes(),
        file_name="LISS4_PCA_Results.zip",
        mime="application/zip",
        type="primary",
        use_container_width=True,
        help="Includes the six-band stack, six PCA components, metadata, and any HFA sidecars.",
    )
    st.caption(
        f"Results are also staged locally at {result_dir}. The ZIP download lets your browser "
        f"save them wherever you choose ({zip_size_mb:.1f} MB)."
    )


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
      <p class="hero-copy">Build the six-band SOP stack, calculate six principal components, and inspect PC4.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Workflow")
    st.markdown("1. Upload the pre- and post-monsoon scenes.")
    st.markdown("2. Map each scene's bands to B2, B3, and B4.")
    st.markdown("3. Confirm co-registration and run PCA.")
    st.markdown("4. Preview PC4 and download the outputs.")
    st.divider()
    st.caption(
        "The app checks CRS, dimensions, extent, and pixel grid. Those checks cannot "
        "prove accurate tie-point co-registration."
    )

st.markdown("### 1. Add the two three-band scenes")
st.caption(
    "Upload a single-file raster per date. If an .img or other raster uses sidecars, "
    "ZIP the primary raster and its sidecars together before uploading."
)
pre_col, post_col = st.columns(2, gap="large")

with pre_col:
    st.markdown("#### Pre-monsoon")
    pre_upload = st.file_uploader(
        "Pre-monsoon scene",
        type=["tif", "tiff", "img", "jp2", "vrt", "dat", "bil", "nc", "zip"],
        key="pre_scene_upload",
    )
with post_col:
    st.markdown("#### Post-monsoon")
    post_upload = st.file_uploader(
        "Post-monsoon scene",
        type=["tif", "tiff", "img", "jp2", "vrt", "dat", "bil", "nc", "zip"],
        key="post_scene_upload",
    )

pre_path = post_path = None
pre_info = post_info = None
pre_error = post_error = None
try:
    pre_path, pre_digest = _save_scene(pre_upload, "pre")
    if pre_path:
        pre_info = _inspect_raster(pre_path)
except Exception as exc:
    pre_error = str(exc)
try:
    post_path, post_digest = _save_scene(post_upload, "post")
    if post_path:
        post_info = _inspect_raster(post_path)
except Exception as exc:
    post_error = str(exc)

if pre_error:
    st.error(f"Pre-monsoon input: {pre_error}")
if post_error:
    st.error(f"Post-monsoon input: {post_error}")

if pre_info:
    st.caption(
        f"Pre-monsoon: {pre_info['width']:,} × {pre_info['height']:,} pixels, "
        f"{pre_info['bands']} bands, CRS {'present' if pre_info['crs'] else 'missing'}."
    )
if post_info:
    st.caption(
        f"Post-monsoon: {post_info['width']:,} × {post_info['height']:,} pixels, "
        f"{post_info['bands']} bands, CRS {'present' if post_info['crs'] else 'missing'}."
    )

pre_indices = _band_pickers("Pre-monsoon scene", pre_info["bands"], "pre") if pre_info else None
post_indices = _band_pickers("Post-monsoon scene", post_info["bands"], "post") if post_info else None

grid_ready = False
grid_error = None
if pre_path and post_path and pre_indices and post_indices:
    try:
        grid_width, grid_height = _grid_check(pre_path, post_path, pre_indices, post_indices)
        grid_ready = True
        st.success(
            f"Selected bands share the same CRS and grid ({grid_width:,} × {grid_height:,}). "
            "This is a metadata check only."
        )
    except Exception as exc:
        grid_error = str(exc)
        st.error(f"Grid check failed: {grid_error}")

st.markdown("### 2. Confirm the prerequisite")
coregistered = st.checkbox(
    "I confirm that the pre- and post-monsoon scenes have been accurately co-registered.",
    key="confirm_coregistered",
)
if not coregistered:
    st.warning("Complete and verify image co-registration before running PCA.")

signature_parts = [
    st.session_state.get("_pre_digest", ""),
    st.session_state.get("_post_digest", ""),
    str(pre_indices),
    str(post_indices),
    str(coregistered),
]
signature = hashlib.sha256("|".join(signature_parts).encode("utf-8")).hexdigest()
can_run = bool(
    grid_ready and coregistered and pre_path and post_path
    and pre_indices and post_indices
)
run_clicked = st.button(
    "Run six-component PCA",
    type="primary",
    disabled=not can_run,
    use_container_width=True,
)

if run_clicked:
    try:
        run_dir = _session_root() / "results"
        run_dir.mkdir(parents=True, exist_ok=True)
        args = SimpleNamespace(
            inputs=[str(pre_path)] * 3 + [str(post_path)] * 3,
            band_indices=list(pre_indices) + list(post_indices),
            outdir=str(run_dir),
            overwrite=True,
        )
        with st.spinner("Stacking the scenes and calculating covariance PCA..."):
            status = pca.run(args)
            if status != 0:
                raise RuntimeError(f"PCA returned status {status}.")

            metadata_path = run_dir / "PCA_metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            for index, source in enumerate(metadata["sources"]):
                uploaded_name = pre_upload.name if index < 3 else post_upload.name
                source["uploaded_filename"] = uploaded_name
                source["path"] = uploaded_name
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            zip_path = _build_download_zip(run_dir)

        st.session_state["_result_signature"] = signature
        st.session_state["_result_dir"] = str(run_dir)
        st.session_state["_result_zip"] = str(zip_path)
        st.success("PCA completed. Review PC4, then save the ZIP to a folder of your choice.")
    except Exception as exc:
        LOG.exception("PCA failed")
        st.error(f"PCA failed: {exc}")

_render_results(signature)


