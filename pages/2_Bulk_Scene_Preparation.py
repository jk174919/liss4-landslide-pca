#!/usr/bin/env python3
"""Bulk LISS-IV folder audit and pre/post common-grid preparation page."""

from __future__ import annotations

import streamlit as st

import bulk_scene_prep as bulk


st.title("Bulk LISS-IV Scene Preparation")
st.write(
    "Scan large local folders, read each product's metadata, pair 2026 pre/post "
    "scenes by path and row, and place matching outputs together."
)
st.caption(
    "This page runs on your PC. Copy each folder path from Windows Explorer "
    "and paste it into the matching field below."
)
st.info(
    "The supplied NRSC product layout uses BAND_META.txt / .meta and separate "
    "BAND2.tif, BAND3.tif, and BAND4.tif files. Those files are grouped in the "
    "correct spectral order automatically."
)

with st.form("bulk_scan_form"):
    left, right = st.columns(2)
    with left:
        pre_root = st.text_input(
            "Pre-monsoon parent folder (local path)",
            placeholder=r"F:\SILAS\Pre_Monsoon_2026",
        )
    with right:
        post_root = st.text_input(
            "Post-monsoon parent folder (local path)",
            placeholder=r"F:\SILAS\Post_Monsoon_2026",
        )
    output_root = st.text_input(
        "Output folder for paired scenes",
        placeholder=r"F:\SILAS\Aligned_Pairs_2026",
    )
    min_coverage = st.slider(
        "Minimum coverage of each scene by the shared area",
        min_value=10,
        max_value=100,
        value=80,
        step=5,
        help="Pairs below this threshold are shown for review and are not selected automatically.",
    )
    analyze = st.form_submit_button("Analyze folders and match path/row", type="primary")

if analyze:
    for key in ("bulk_scenes", "bulk_pairs", "bulk_prepared"):
        st.session_state.pop(key, None)
    try:
        if not pre_root.strip() or not post_root.strip():
            raise ValueError("Choose both the pre- and post-monsoon folders.")
        pre_scenes = bulk.scan_root(pre_root, ignore_root=output_root or None)
        post_scenes = bulk.scan_root(post_root, ignore_root=output_root or None)
        # Do not count the same product twice if the two selected roots overlap.
        by_folder = {}
        for scene in pre_scenes + post_scenes:
            by_folder[(scene["folder"], scene.get("date"))] = scene
        scenes = list(by_folder.values())
        pairs = bulk.pair_scenes(scenes, min_coverage / 100.0)
        st.session_state["bulk_scenes"] = scenes
        st.session_state["bulk_pairs"] = pairs
        st.session_state["bulk_output_root"] = output_root.strip()
        if not scenes:
            st.warning(
                "No readable raster product folders were found. Check the selected paths "
                "and confirm that each scene folder contains a supported raster file."
            )
        elif not pairs:
            st.warning(
                "No 2026 pre/post scenes with the same metadata path and row were found. "
                "Review the inventory CSV for missing or unparsed date/path/row fields."
            )
    except Exception as exc:
        st.error(f"Folder analysis failed: {exc}")

scenes = st.session_state.get("bulk_scenes", [])
pairs = st.session_state.get("bulk_pairs", [])

if scenes:
    st.divider()
    st.subheader("Scene inventory")
    counts = st.columns(4)
    counts[0].metric("Scene folders", len(scenes))
    counts[1].metric("Pre scenes in date window", sum(s.get("season") == "pre" for s in scenes))
    counts[2].metric("Post scenes in date window", sum(s.get("season") == "post" for s in scenes))
    counts[3].metric("Same path/row candidates", len(pairs))
    inventory_rows = bulk.scene_report_rows(scenes)
    st.dataframe(inventory_rows, hide_index=True, use_container_width=True)
    st.download_button(
        "Download scene inventory CSV",
        data=bulk.csv_bytes(inventory_rows),
        file_name="scene_inventory.csv",
        mime="text/csv",
    )

if pairs:
    st.subheader("Pre/post matches")
    pair_rows = bulk.pair_report_rows(pairs)
    st.dataframe(pair_rows, hide_index=True, use_container_width=True)
    st.download_button(
        "Download pair matching report CSV",
        data=bulk.csv_bytes(pair_rows),
        file_name="pre_post_pair_matches.csv",
        mime="text/csv",
        key="bulk_pair_report_download",
    )
    st.caption(
        "The shared output extent is cropped to the georeferenced overlap. "
        "Matching CRS, pixel size, and extent gives a common grid; it does not "
        "measure or remove residual image-to-image shifts."
    )

    eligible = [
        pair for pair in pairs
        if pair["status"] in {"READY", "LOW_OVERLAP_REVIEW"}
    ]
    label_to_pair = {}
    for pair in eligible:
        label = (
            f"Path {pair['path']:03d} / Row {pair['row']:03d} | "
            f"{pair['pre_date']} → {pair['post_date']} | {pair['status']} | "
            f"{pair.get('pre_scene_id')} → {pair.get('post_scene_id')}"
        )
        label_to_pair[label] = pair
    default_labels = [
        label for label, pair in label_to_pair.items() if pair["status"] == "READY"
    ]

    with st.expander("Alignment options", expanded=True):
        allow_low = st.checkbox(
            "Allow pairs below the coverage threshold after I review their overlap percentages",
            value=False,
        )
        selectable_labels = [
            label for label, pair in label_to_pair.items()
            if pair["status"] == "READY" or allow_low
        ]
        selected = st.multiselect(
            "Pairs to prepare",
            options=selectable_labels,
            default=[label for label in default_labels if label in selectable_labels],
        )
        target_crs = st.text_input(
            "Target CRS override (leave blank to use each pre-scene CRS)",
            placeholder="For example: EPSG:32643",
        )
        resolution = st.number_input(
            "Target pixel size in target CRS units (0 = use pre-scene pixel size)",
            min_value=0.0,
            value=0.0,
            step=0.5,
        )
        resampling = st.selectbox(
            "Resampling for continuous image data",
            options=["bilinear", "near", "cubic"],
            index=0,
        )
        overwrite = st.checkbox(
            "Overwrite outputs when the same pair folders already exist",
            value=False,
        )

    scenes_by_folder = {scene["folder"]: scene for scene in scenes}
    if st.button("Prepare selected pairs", type="primary", disabled=not selected):
        destination = st.session_state.get("bulk_output_root", "")
        if not destination.strip():
            st.error("Enter an output folder above, then analyze the folders again.")
        else:
            results = []
            progress = st.progress(0.0)
            status = st.empty()
            for index, label in enumerate(selected):
                pair = label_to_pair[label]
                status.write(f"Aligning path {pair['path']} row {pair['row']} ({index + 1}/{len(selected)})…")
                try:
                    pre = scenes_by_folder[pair["pre_folder"]]
                    post = scenes_by_folder[pair["post_folder"]]
                    result = bulk.prepare_pair(
                        pre,
                        post,
                        destination,
                        target_crs=target_crs.strip() or None,
                        target_resolution=resolution or None,
                        resampling=resampling,
                        overwrite=overwrite,
                    )
                    result["status"] = "SUCCESS"
                    result["path"] = pair["path"]
                    result["row"] = pair["row"]
                    result["pre_date"] = pair["pre_date"]
                    result["post_date"] = pair["post_date"]
                except Exception as exc:
                    result = {
                        "status": "FAILED",
                        "path": pair["path"],
                        "row": pair["row"],
                        "pre_date": pair["pre_date"],
                        "post_date": pair["post_date"],
                        "error": str(exc),
                    }
                results.append(result)
                progress.progress((index + 1) / len(selected))
            st.session_state["bulk_prepared"] = results
            status.empty()

if st.session_state.get("bulk_prepared"):
    st.divider()
    st.subheader("Preparation results")
    for result in st.session_state["bulk_prepared"]:
        if result["status"] == "SUCCESS":
            st.success(
                f"Path {result['path']} / Row {result['row']}: "
                f"{result['width']:,} × {result['height']:,} pixels. "
                f"Saved in {result['folder']}"
            )
        else:
            st.error(
                f"Path {result['path']} / Row {result['row']}: {result['error']}"
            )

st.warning(
    "Automatic preparation only works when each scene has a usable georeference "
    "or a GEOREF metadata record with UTM zone and product map bounds. For RPC-only "
    "or ungeoreferenced imagery, a DEM, ground control, or the supplier's sensor "
    "model may be required. Do not treat a common output grid as proof of accurate "
    "co-registration for landslide change analysis."
)

