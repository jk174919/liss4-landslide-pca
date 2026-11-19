#!/usr/bin/env python3
"""Sort original LISS-IV product folders into 2026 pre/post Path-Row groups."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

import bulk_scene_prep as bulk


st.set_page_config(page_title="Sort LISS-IV Products", page_icon="🛰️", layout="wide")
st.title("Sort Original Product Folders by Path/Row")
st.write(
    "Enter one or more parent folder paths. The app reads each product's metadata, "
    "finds 2026 pre/post products with the same Path and Row, and can move the full "
    "original product folders into a new organized folder on the same drive."
)
st.info(
    "Enter paths only; do not upload the imagery. Each complete product folder, "
    "including BAND2, BAND3, BAND4, metadata, and sidecars, is kept together. "
    "Georeferencing is checked later on the PCA page."
)

with st.form("sort_pathrow_scan_form"):
    source_text = st.text_area(
        "Parent folder paths to scan (one path per line; a parent can contain many product folders)",
        placeholder="F:\\SILAS\\LISS4_2026\nF:\\Other_LISS4_Products",
        height=100,
        help="The scanner searches nested folders and classifies products from metadata dates: pre = Jan–May 2026; post = Oct–Dec 2026.",
    )
    output_text = st.text_input(
        "New organized folder on the same drive",
        placeholder=r"F:\SILAS\LISS4_Sorted_By_Path_Row_2026",
    )
    analyze = st.form_submit_button("Scan metadata and find matching Path/Rows", type="primary")

if analyze:
    for key in (
        "sort_pathrow_groups", "sort_pathrow_candidates", "sort_pathrow_skipped",
        "sort_move_results", "sort_pathrow_selection", "confirm_pathrow_move",
    ):
        st.session_state.pop(key, None)
    try:
        roots = [Path(line.strip().strip('"').strip("'")).expanduser() for line in source_text.splitlines() if line.strip()]
        if not roots:
            raise ValueError("Enter at least one parent folder path.")
        if not output_text.strip():
            raise ValueError("Enter the new organized output folder path.")
        destination = Path(output_text.strip().strip('"')).expanduser()
        scenes_by_folder = {}
        for root in roots:
            if not root.is_dir():
                raise NotADirectoryError(f"Source folder does not exist: {root}")
            for scene in bulk.scan_root(root, ignore_root=destination):
                scenes_by_folder[str(Path(scene["folder"]).resolve()).casefold()] = scene
        scenes = list(scenes_by_folder.values())
        groups, candidates, skipped = bulk.group_pathrow_products(scenes)
        st.session_state["sort_pathrow_roots"] = [str(root.resolve()) for root in roots]
        st.session_state["sort_pathrow_output"] = str(destination.resolve())
        st.session_state["sort_pathrow_groups"] = groups
        st.session_state["sort_pathrow_candidates"] = candidates
        st.session_state["sort_pathrow_skipped"] = skipped
        if not groups:
            st.warning(
                "No matching 2026 pre/post Path/Row groups were found. Check that product metadata "
                "contains Path, Row, and DateOfPass and each product has B2, B3, and B4."
            )
    except Exception as exc:
        st.error(f"Folder scan failed: {exc}")

groups = st.session_state.get("sort_pathrow_groups", [])
candidates = st.session_state.get("sort_pathrow_candidates", [])
skipped = st.session_state.get("sort_pathrow_skipped", [])

if candidates:
    matched_keys = {(group["path"], group["row"]) for group in groups}
    unmatched = [scene for scene in candidates if (scene["path"], scene["row"]) not in matched_keys]
    metrics = st.columns(4)
    metrics[0].metric("Complete product folders", len(candidates))
    metrics[1].metric("Matched Path/Row groups", len(groups))
    metrics[2].metric("Pre products", sum(scene["season"] == "pre" for scene in candidates))
    metrics[3].metric("Post products", sum(scene["season"] == "post" for scene in candidates))

    group_rows = []
    for group in groups:
        group_rows.append({
            "Path": f"{group['path']:04d}",
            "Row": f"{group['row']:04d}",
            "Pre products": len(group["pre"]),
            "Pre dates": ", ".join(scene["date"] for scene in group["pre"]),
            "Post products": len(group["post"]),
            "Post dates": ", ".join(scene["date"] for scene in group["post"]),
        })
    st.subheader("Matching Path/Row groups")
    st.dataframe(group_rows, hide_index=True, use_container_width=True)

    with st.expander("Review every product folder and band layout"):
        candidate_rows = [
            {
                "Path": scene["path"],
                "Row": scene["row"],
                "Season": "Pre" if scene["season"] == "pre" else "Post",
                "Date": scene["date"],
                "Bands found": scene["band_layout"],
                "Product folder": scene["folder"],
            }
            for scene in candidates
        ]
        st.dataframe(candidate_rows, hide_index=True, use_container_width=True)
    if unmatched:
        with st.expander(f"Unmatched product folders left in place ({len(unmatched)})"):
            st.dataframe(
                [
                    {
                        "Path": scene["path"],
                        "Row": scene["row"],
                        "Season": scene["season"],
                        "Date": scene["date"],
                        "Product folder": scene["folder"],
                    }
                    for scene in unmatched
                ],
                hide_index=True,
                use_container_width=True,
            )
    if skipped:
        with st.expander(f"Folders skipped because metadata or band grouping was incomplete ({len(skipped)})"):
            st.dataframe(skipped, hide_index=True, use_container_width=True)

    label_to_key = {}
    for group in groups:
        label = (
            f"Path {group['path']:04d} / Row {group['row']:04d} — "
            f"{len(group['pre'])} pre product(s), {len(group['post'])} post product(s)"
        )
        label_to_key[label] = (group["path"], group["row"])
    selected_labels = st.multiselect(
        "Choose Path/Row groups to organize (all are selected initially)",
        options=list(label_to_key),
        default=list(label_to_key),
        key="sort_pathrow_selection",
    )
    st.caption(
        "The new structure will be Path_####_Row_#### / Pre_Monsoon or Post_Monsoon / "
        "product folder. Every selected matched product directory is moved intact. "
        "Unmatched folders remain where they are."
    )
    confirm_move = st.checkbox(
        "I reviewed the matches and want to move these original product folders to the output folder.",
        key="confirm_pathrow_move",
    )
    already_moved = bool(st.session_state.get("sort_move_results"))
    move_clicked = st.button(
        "Move selected matched folders",
        type="primary",
        disabled=not selected_labels or not confirm_move or already_moved,
    )
    if move_clicked:
        try:
            with st.spinner("Moving complete product folders on the same drive..."):
                moved = bulk.move_pathrow_products(
                    groups,
                    {label_to_key[label] for label in selected_labels},
                    st.session_state["sort_pathrow_output"],
                    st.session_state["sort_pathrow_roots"],
                )
            st.session_state["sort_move_results"] = moved
        except Exception as exc:
            st.error(f"Folder organization failed: {exc}")

move_results = st.session_state.get("sort_move_results", [])
if move_results:
    st.divider()
    st.success(
        f"Moved {len(move_results)} complete product folders. The original files, all bands, "
        "metadata, and sidecars are now together in the organized output folder."
    )
    st.caption(f"Index: {st.session_state['sort_pathrow_output']}\\path_row_folder_index.csv")
    st.dataframe(
        [
            {
                "Path": item["path"],
                "Row": item["row"],
                "Season": "Pre" if item["season"] == "pre" else "Post",
                "Date": item["date"],
                "Moved to": item["destination"],
            }
            for item in move_results
        ],
        hide_index=True,
        use_container_width=True,
    )
    st.info(
        "Next, open the Landslide PCA page. For each matched Path/Row folder, paste the exact "
        "pre-monsoon and post-monsoon product folder paths. The PCA page reads BAND2, BAND3, "
        "and BAND4 automatically, checks metadata Path/Row and the six rasters' georeferenced "
        "pixel grid, then saves the stack and PC rasters in the selected local output folder."
    )

