#!/usr/bin/env python3
"""Inventory, pair, and grid-align Resourcesat LISS-IV pre/post scene folders.

The module reads NRSC BAND_META.txt / .meta key-value metadata, groups
BAND2.tif, BAND3.tif, and BAND4.tif into a scene, matches 2026 seasons by
path/row, and warps valid georeferenced pairs to one exact shared grid.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

from osgeo import gdal, osr

gdal.UseExceptions()

RASTER_EXTENSIONS = {
    ".tif", ".tiff", ".img", ".jp2", ".vrt", ".h5", ".hdf", ".nc", ".dat", ".bil"
}
PRE_START = dt.date(2026, 1, 1)
PRE_END = dt.date(2026, 5, 31)
POST_START = dt.date(2026, 10, 1)
POST_END = dt.date(2026, 12, 31)
MIN_COVERAGE_DEFAULT = 0.80
OUTPUT_NODATA = 0


def _canonical(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value))[:28].strip("_") or "scene"


def _set_axis_order(srs: osr.SpatialReference) -> osr.SpatialReference:
    if hasattr(srs, "SetAxisMappingStrategy"):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def _parse_date(value) -> dt.date | None:
    text = str(value or "").strip()
    token = text.split()[0] if text else ""
    for pattern in (
        "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%Y/%m/%d",
        "%d/%m/%Y", "%Y%m%d", "%d-%m-%Y",
    ):
        try:
            return dt.datetime.strptime(token, pattern).date()
        except ValueError:
            continue
    match = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", text)
    if match:
        try:
            return dt.date(*(int(value) for value in match.groups()))
        except ValueError:
            return None
    return None


def _read_key_value(path: Path) -> dict[str, str]:
    """Read NRSC BAND_META / .meta and common text sidecars."""
    values = {}
    try:
        raw = path.read_bytes()
    except OSError:
        return values
    if len(raw) > 4 * 1024 * 1024:
        return values
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return values

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        match = re.match(r"^\s*([^:=]+?)\s*[:=]\s*(.*?)\s*$", line)
        if match and match.group(2):
            key, value = match.groups()
            values.setdefault(_canonical(key), value.strip().strip('"'))
    return values


def _metadata_for_folder(folder: Path) -> tuple[dict[str, str], list[str]]:
    preferred = []
    other = []
    for item in folder.iterdir():
        if not item.is_file():
            continue
        name = item.name.lower()
        if name == "band_meta.txt" or item.suffix.lower() == ".meta":
            preferred.append(item)
        elif item.suffix.lower() in {".xml", ".json", ".txt", ".geom"} and (
            "meta" in name or "rpc" in name or "band" in name
        ):
            other.append(item)
    values = {}
    used = []
    for path in preferred + other:
        parsed = _read_key_value(path)
        for key, value in parsed.items():
            values.setdefault(key, value)
        if parsed:
            used.append(str(path))
    return values, used


def _metadata_date(values: dict[str, str]) -> dt.date | None:
    # Prefer acquisition/pass dates to dump or product-generation dates.
    for key in ("dateofpass", "acquisitiondate", "dateacquired",
                "imagingdate", "scenedate", "passdate", "date"):
        parsed = _parse_date(values.get(key))
        if parsed:
            return parsed
    for key in ("acquisitiondatetime", "imagingdatetime", "starttime"):
        parsed = _parse_date(values.get(key))
        if parsed:
            return parsed
    return None


def _path_row(values: dict[str, str], folder_name: str) -> tuple[int | None, int | None]:
    path_value = next(
        (values[key] for key in ("path", "pathno", "pathnumber", "orbitpath")
         if values.get(key)), None
    )
    row_value = next(
        (values[key] for key in ("row", "rowno", "rownumber", "orbitrow")
         if values.get(key)), None
    )
    if path_value is None or row_value is None:
        combined = f"{folder_name} " + " ".join(values.values())
        patterns = (
            r"\bpath\s*[_-]?\s*(\d{1,4}).{0,30}?\brow\s*[_-]?\s*(\d{1,4})\b",
            r"\bP(\d{1,4})\s*[_-]?\s*R(\d{1,4})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, combined, re.IGNORECASE)
            if match:
                path_value, row_value = match.groups()
                break
    try:
        path_number = int(re.search(r"\d+", str(path_value)).group())
        row_number = int(re.search(r"\d+", str(row_value)).group())
        return path_number, row_number
    except (AttributeError, TypeError, ValueError):
        return None, None


def _srs_from_metadata(values: dict[str, str]) -> str | None:
    projection = (values.get("mapprojection") or "").upper()
    zone = values.get("zoneno") or values.get("utmzone")
    datum = (values.get("datum") or values.get("ellipsoid") or "").upper()
    if "UTM" not in projection or not zone or "WGS" not in datum:
        return None
    try:
        zone_number = int(re.search(r"\d+", zone).group())
    except (AttributeError, ValueError):
        return None
    if not 1 <= zone_number <= 60:
        return None
    try:
        latitude = float(values.get("scenecenterlat") or values.get("produllat") or 0)
    except ValueError:
        latitude = 0
    epsg = (32600 if latitude >= 0 else 32700) + zone_number
    srs = osr.SpatialReference()
    if srs.ImportFromEPSG(epsg) != 0:
        return None
    _set_axis_order(srs)
    return srs.ExportToWkt()


def _metadata_geotransform(values: dict[str, str], width: int, height: int):
    """Rebuild a north-up transform from GEOREF product bounds when available."""
    if (values.get("prodtype") or "").strip().upper() not in {"GEOREF", "GEO-REF", "GEOREFERENCED"}:
        return None
    try:
        left = float(values["produlmapx"])
        top = float(values["produlmapy"])
        right = float(values["prodlrmapx"])
        bottom = float(values["prodlrmapy"])
        if width <= 0 or height <= 0 or right <= left or top <= bottom:
            return None
        return (left, (right - left) / width, 0.0,
                top, 0.0, -(top - bottom) / height)
    except (KeyError, ValueError):
        return None


def _band_file_map(folder: Path) -> dict[int, str]:
    mapping = {}
    preferred = {}
    for item in sorted(folder.iterdir(), key=lambda path: path.name.casefold()):
        if not item.is_file() or item.suffix.lower() not in RASTER_EXTENSIONS:
            continue
        match = re.fullmatch(r"band[_-]?([234])(?:[_-].*)?", item.stem, re.IGNORECASE)
        if match:
            number = int(match.group(1))
            is_preferred = re.fullmatch(rf"band[_-]?{number}", item.stem, re.IGNORECASE) is not None
            if number not in mapping or (is_preferred and not preferred[number]):
                mapping[number] = str(item)
                preferred[number] = is_preferred
    return mapping


def _candidate_rasters(folder: Path, band_map: dict[int, str]) -> list[str]:
    if set(band_map) == {2, 3, 4}:
        return [band_map[2], band_map[3], band_map[4]]
    candidates = [
        item for item in folder.iterdir()
        if item.is_file() and item.suffix.lower() in RASTER_EXTENSIONS
        and not item.stem.lower().endswith(("_rpc", "_browse", "_thumb"))
    ]
    if len(candidates) == 1:
        return [str(candidates[0])]
    return [str(path) for path in candidates]


def _open_details(path: str) -> dict:
    try:
        ds = gdal.Open(path, gdal.GA_ReadOnly)
    except Exception as exc:
        return {"open_error": f"GDAL could not open this raster: {exc}"}
    if ds is None:
        return {"open_error": "GDAL could not open this raster."}
    gt = ds.GetGeoTransform(can_return_null=True)
    projection = ds.GetProjectionRef() or ""
    result = {
        "width": int(ds.RasterXSize),
        "height": int(ds.RasterYSize),
        "bands": int(ds.RasterCount),
        "geotransform": list(gt) if gt else None,
        "crs_wkt": projection or None,
        "has_rpc": bool(ds.GetMetadata("RPC")),
        "gcp_count": int(ds.GetGCPCount()),
        "gcp_projection": ds.GetGCPProjection() or None,
        "driver": ds.GetDriver().ShortName,
    }
    ds = None
    return result


def scan_root(root: str | Path, *, ignore_root: str | Path | None = None) -> list[dict]:
    """Inspect product folders recursively and return one record per scene folder."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Input folder does not exist: {root}")
    ignore = Path(ignore_root).expanduser().resolve() if ignore_root else None
    scenes = []

    folders = [root]
    folders.extend(path for path in root.rglob("*") if path.is_dir())
    for folder in sorted(folders):
        if ignore and (folder == ignore or ignore in folder.parents):
            continue
        try:
            metadata, metadata_files = _metadata_for_folder(folder)
            band_map = _band_file_map(folder)
            rasters = _candidate_rasters(folder, band_map)
        except OSError:
            continue
        if not rasters:
            continue

        path_number, row_number = _path_row(metadata, folder.name)
        acquired = _metadata_date(metadata)
        detail = _open_details(rasters[0])
        warnings = []
        if len(rasters) > 1 and set(band_map) != {2, 3, 4}:
            warnings.append("Multiple rasters found but BAND2/BAND3/BAND4 grouping is ambiguous.")
        if detail.get("open_error"):
            warnings.append(detail["open_error"])
        elif len(rasters) > 1:
            extra = [_open_details(item) for item in rasters[1:]]
            if any(item.get("open_error") for item in extra):
                warnings.append("One or more separate band rasters could not be opened.")
            elif any(
                (item["width"], item["height"]) != (detail["width"], detail["height"])
                for item in extra
            ):
                warnings.append("Separate band rasters have different dimensions.")
            else:
                reference_gt = detail.get("geotransform")
                reference_srs = detail.get("crs_wkt")
                for item in extra:
                    candidate_gt = item.get("geotransform")
                    candidate_srs = item.get("crs_wkt")
                    if reference_gt and candidate_gt and any(
                        not math.isclose(a, b, rel_tol=0.0, abs_tol=1e-8)
                        for a, b in zip(reference_gt, candidate_gt)
                    ):
                        warnings.append("Separate band rasters have different pixel grids.")
                        break
                    if bool(reference_srs) != bool(candidate_srs):
                        warnings.append("Separate band rasters have inconsistent CRS metadata.")
                        break
                    if reference_srs and candidate_srs:
                        a, b = _set_axis_order(osr.SpatialReference()), _set_axis_order(osr.SpatialReference())
                        if a.ImportFromWkt(reference_srs) != 0 or b.ImportFromWkt(candidate_srs) != 0 or not a.IsSame(b):
                            warnings.append("Separate band rasters use different coordinate systems.")
                            break

        # If GDAL lacks a CRS/transform, use the NRSC product metadata only when
        # it explicitly says GEOREF and provides UTM zone and map-corner bounds.
        if not detail.get("open_error"):
            if not detail.get("crs_wkt"):
                detail["crs_wkt"] = _srs_from_metadata(metadata)
            if not detail.get("geotransform"):
                detail["geotransform"] = _metadata_geotransform(
                    metadata, detail["width"], detail["height"]
                )
            if detail.get("geotransform") and detail.get("crs_wkt"):
                detail["georeferencing_source"] = (
                    "GeoTIFF" if _open_details(rasters[0]).get("geotransform")
                    and _open_details(rasters[0]).get("crs_wkt")
                    else "NRSC GEOREF metadata reconstruction"
                )
                if not _is_north_up(tuple(detail["geotransform"])):
                    warnings.append("Rotated pixel grids need a separately defined north-up target grid.")
            else:
                detail["georeferencing_source"] = "missing"
                warnings.append(
                    "No complete CRS and affine transform found. RPC/GCP-only products "
                    "need a suitable DEM/control-point workflow before grid alignment."
                )

        if acquired is None:
            warnings.append("Acquisition date was not found in metadata.")
        if path_number is None or row_number is None:
            warnings.append("Path or row was not found in metadata.")

        sensor = metadata.get("sensor", "")
        scene_id = metadata.get("otsproductid") or folder.name
        scene = {
            "scene_id": scene_id,
            "folder": str(folder),
            "metadata_files": metadata_files,
            "raster_files": rasters,
            "band_files": {str(k): v for k, v in sorted(band_map.items())},
            "path": path_number,
            "row": row_number,
            "date": acquired.isoformat() if acquired else None,
            "season": season_for_date(acquired),
            "sensor": sensor,
            "product_type": metadata.get("prodtype", ""),
            "dem_correction": metadata.get("demcorrection", ""),
            "band_numbers": metadata.get("bandnumbers", ""),
            "metadata": metadata,
            "raster": {
                **detail,
                "bands": 3 if set(band_map) == {2, 3, 4} else detail.get("bands"),
            },
            "warnings": warnings,
        }
        scenes.append(scene)
    return scenes


def season_for_date(value: dt.date | str | None) -> str:
    if isinstance(value, str):
        value = _parse_date(value)
    if value is None or value.year != 2026:
        return "outside selected 2026 seasons"
    if PRE_START <= value <= PRE_END:
        return "pre"
    if POST_START <= value <= POST_END:
        return "post"
    return "outside selected 2026 seasons"


def product_identity(folder: str | Path) -> dict:
    """Read the path, row, and acquisition date from a product folder's metadata."""
    folder = Path(folder).expanduser().resolve()
    values, metadata_files = _metadata_for_folder(folder)
    path_number, row_number = _path_row(values, "")
    acquired = _metadata_date(values)
    return {
        "path": path_number,
        "row": row_number,
        "date": acquired.isoformat() if acquired else None,
        "metadata_files": metadata_files,
    }


def group_pathrow_products(scenes: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Group complete 2026 products by metadata Path/Row and pre/post season.

    This metadata-only grouping deliberately does not require georeferencing;
    georeferencing and common-grid checks happen before PCA.
    """
    candidates = []
    skipped = []
    by_pathrow = {}
    for scene in scenes:
        folder = Path(scene["folder"])
        metadata = scene.get("metadata", {})
        metadata_files = scene.get("metadata_files", [])
        path_number, row_number = _path_row(metadata, "")
        acquired = _metadata_date(metadata)
        season = season_for_date(acquired)
        if not metadata_files:
            skipped.append({"folder": str(folder), "reason": "No readable product metadata file."})
            continue
        if path_number is None or row_number is None:
            skipped.append({"folder": str(folder), "reason": "Path and/or Row is missing from metadata."})
            continue
        if acquired is None:
            skipped.append({"folder": str(folder), "reason": "DateOfPass/acquisition date is missing from metadata."})
            continue
        if season not in {"pre", "post"}:
            continue

        band_files = {int(key) for key in scene.get("band_files", {})}
        if band_files == {2, 3, 4}:
            band_layout = "Separate BAND2 / BAND3 / BAND4"
        elif len(scene.get("raster_files", [])) == 1 and int(scene.get("raster", {}).get("bands") or 0) >= 3:
            band_layout = "One multi-band raster (3+ bands)"
        else:
            skipped.append({
                "folder": str(folder),
                "reason": "Could not identify all three B2/B3/B4 bands unambiguously.",
            })
            continue

        candidate = {
            **scene,
            "path": path_number,
            "row": row_number,
            "date": acquired.isoformat(),
            "season": season,
            "band_layout": band_layout,
        }
        candidates.append(candidate)
        key = (path_number, row_number)
        group = by_pathrow.setdefault(key, {"path": path_number, "row": row_number, "pre": [], "post": []})
        group[season].append(candidate)

    groups = [
        group for group in by_pathrow.values()
        if group["pre"] and group["post"]
    ]
    groups.sort(key=lambda group: (group["path"], group["row"]))
    candidates.sort(key=lambda scene: (scene["path"], scene["row"], scene["date"], scene["season"], scene["folder"]))
    return groups, candidates, skipped


def move_pathrow_products(
    groups: list[dict],
    selected_keys: set[tuple[int, int]],
    output_root: str | Path,
    source_roots: list[str | Path],
) -> list[dict]:
    """Move selected original product folders under Path/Row and season folders.

    All moves must stay on the same volume. Existing target folders are never
    overwritten. If a move or index write fails, already moved folders are
    rolled back to their original locations.
    """
    destination_root = Path(output_root).expanduser().resolve()
    roots = [Path(root).expanduser().resolve() for root in source_roots]
    if not roots or any(not root.is_dir() for root in roots):
        raise NotADirectoryError("Every source root must be an existing folder.")
    if not destination_root.drive or any(root.drive.casefold() != destination_root.drive.casefold() for root in roots):
        raise ValueError("Choose an output folder on the same drive as every scanned source folder.")

    plan = []
    for group in groups:
        key = (group["path"], group["row"])
        if key not in selected_keys:
            continue
        for season in ("pre", "post"):
            for scene in group[season]:
                source = Path(scene["folder"]).expanduser().resolve()
                if not source.is_dir():
                    raise FileNotFoundError(f"Source product folder no longer exists: {source}")
                if source.drive.casefold() != destination_root.drive.casefold():
                    raise ValueError(f"Source and output are on different drives: {source}")
                if not any(source == root or root in source.parents for root in roots):
                    raise ValueError(f"Scanned product is outside the selected source roots: {source}")
                if source == destination_root or source in destination_root.parents:
                    raise ValueError(f"Output folder cannot be inside a product folder being moved: {destination_root}")
                season_folder = "Pre_Monsoon" if season == "pre" else "Post_Monsoon"
                suffix = hashlib.sha1(str(source).casefold().encode("utf-8")).hexdigest()[:8]
                product_name = f"{scene['date']}_{_safe_token(source.name)}_{suffix}"
                target = (
                    destination_root
                    / f"Path_{int(group['path']):04d}_Row_{int(group['row']):04d}"
                    / season_folder
                    / product_name
                )
                plan.append({
                    "path": int(group["path"]),
                    "row": int(group["row"]),
                    "season": season,
                    "date": scene["date"],
                    "band_layout": scene["band_layout"],
                    "source": str(source),
                    "destination": str(target),
                })

    if not plan:
        raise ValueError("Select at least one matched Path/Row group to move.")
    destinations = [Path(item["destination"]) for item in plan]
    if len(set(destinations)) != len(destinations):
        raise FileExistsError("Two product folders would use the same destination. Choose a different output folder.")
    existing = [str(path) for path in destinations if path.exists()]
    index_path = destination_root / "path_row_folder_index.csv"
    if existing:
        raise FileExistsError("A destination product folder already exists; nothing was moved: " + ", ".join(existing))
    if index_path.exists():
        raise FileExistsError(f"An index already exists at {index_path}. Choose a new output folder.")

    moved = []
    try:
        destination_root.mkdir(parents=True, exist_ok=True)
        for item in plan:
            source = Path(item["source"])
            target = Path(item["destination"])
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            moved.append(item)
        write_csv(index_path, plan)
    except Exception as exc:
        rollback_errors = []
        for item in reversed(moved):
            source = Path(item["source"])
            target = Path(item["destination"])
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                target.rename(source)
            except Exception as rollback_exc:
                rollback_errors.append(f"{target} -> {source}: {rollback_exc}")
        if index_path.exists():
            index_path.unlink()
        detail = f"Folder move failed and moved products were rolled back: {exc}"
        if rollback_errors:
            detail += " Rollback also failed for: " + "; ".join(rollback_errors)
        raise RuntimeError(detail) from exc
    return plan


def _bounds(scene: dict, target_wkt: str) -> tuple[float, float, float, float] | None:
    raster = scene["raster"]
    gt = raster.get("geotransform")
    projection = raster.get("crs_wkt")
    if not gt or not projection or not raster.get("width") or not raster.get("height"):
        return None
    source_srs = _set_axis_order(osr.SpatialReference())
    target_srs = _set_axis_order(osr.SpatialReference())
    if source_srs.ImportFromWkt(projection) != 0 or target_srs.ImportFromWkt(target_wkt) != 0:
        return None
    transform = None if source_srs.IsSame(target_srs) else osr.CoordinateTransformation(source_srs, target_srs)

    width, height = raster["width"], raster["height"]
    points = []
    # Densify each edge so a projected footprint is less likely to be
    # underestimated when transforming between coordinate systems.
    n = 20
    pixels = []
    pixels.extend((width * i / n, 0) for i in range(n + 1))
    pixels.extend((width, height * i / n) for i in range(1, n + 1))
    pixels.extend((width * (n - i) / n, height) for i in range(1, n + 1))
    pixels.extend((0, height * (n - i) / n) for i in range(1, n))
    for pixel, line in pixels:
        x, y = gdal.ApplyGeoTransform(tuple(gt), pixel, line)
        if transform:
            x, y, *_ = transform.TransformPoint(x, y)
        points.append((x, y))
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _intersection(a, b):
    left, bottom = max(a[0], b[0]), max(a[1], b[1])
    right, top = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or top <= bottom:
        return None
    return (left, bottom, right, top)


def _area(bounds):
    return max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])


def _coverage(bounds, intersection):
    area = _area(bounds)
    return _area(intersection) / area if area > 0 else 0.0


def pair_scenes(scenes: list[dict], min_coverage: float = MIN_COVERAGE_DEFAULT) -> list[dict]:
    """Pair scenes by metadata path/row, then score their spatial overlap."""
    pre = [scene for scene in scenes if scene.get("season") == "pre" and scene.get("path") is not None and scene.get("row") is not None]
    post = [scene for scene in scenes if scene.get("season") == "post" and scene.get("path") is not None and scene.get("row") is not None]
    pairs = []
    for first in pre:
        for second in post:
            if (first["path"], first["row"]) != (second["path"], second["row"]):
                continue
            record = {
                "path": first["path"],
                "row": first["row"],
                "pre_scene_id": first["scene_id"],
                "post_scene_id": second["scene_id"],
                "pre_folder": first["folder"],
                "post_folder": second["folder"],
                "pre_files": first["raster_files"],
                "post_files": second["raster_files"],
                "pre_date": first["date"],
                "post_date": second["date"],
                "pre_sensor": first.get("sensor"),
                "post_sensor": second.get("sensor"),
                "pre_warnings": list(first.get("warnings", [])),
                "post_warnings": list(second.get("warnings", [])),
                "min_coverage_required_pct": round(min_coverage * 100, 2),
            }
            if (
                first.get("sensor") and second.get("sensor")
                and first["sensor"].strip().upper() != second["sensor"].strip().upper()
            ):
                record.update({
                    "status": "SENSOR_MISMATCH",
                    "reason": "The products have different sensor codes; review the pair before combining them.",
                    "pre_coverage_pct": None,
                    "post_coverage_pct": None,
                    "overlap_pct_of_union": None,
                })
                pairs.append(record)
                continue
            pre_wkt = first["raster"].get("crs_wkt")
            post_wkt = second["raster"].get("crs_wkt")
            if (
                not pre_wkt or not post_wkt
                or not first["raster"].get("geotransform")
                or not second["raster"].get("geotransform")
            ):
                record.update({
                    "status": "NEEDS_GEOREFERENCE",
                    "reason": "At least one scene has no usable CRS/geotransform. Review RPC/GCP/metadata and add ground control or a supported sensor model.",
                    "pre_coverage_pct": None,
                    "post_coverage_pct": None,
                    "overlap_pct_of_union": None,
                })
            else:
                pre_bounds = _bounds(first, pre_wkt)
                post_bounds = _bounds(second, pre_wkt)
                overlap = _intersection(pre_bounds, post_bounds) if pre_bounds and post_bounds else None
                if not overlap:
                    record.update({
                        "status": "NO_OVERLAP",
                        "reason": "The georeferenced footprints do not overlap.",
                        "pre_coverage_pct": 0.0,
                        "post_coverage_pct": 0.0,
                        "overlap_pct_of_union": 0.0,
                    })
                else:
                    pre_cov = _coverage(pre_bounds, overlap)
                    post_cov = _coverage(post_bounds, overlap)
                    union = _area(pre_bounds) + _area(post_bounds) - _area(overlap)
                    iou = _area(overlap) / union if union > 0 else 0
                    status = "READY" if min(pre_cov, post_cov) >= min_coverage else "LOW_OVERLAP_REVIEW"
                    record.update({
                        "status": status,
                        "reason": (
                            "Common footprint meets the coverage threshold; both outputs can be warped to the same grid."
                            if status == "READY"
                            else "Same path/row but the common footprint is below threshold; inspect before preparing."
                        ),
                        "pre_coverage_pct": round(pre_cov * 100, 3),
                        "post_coverage_pct": round(post_cov * 100, 3),
                        "overlap_pct_of_union": round(iou * 100, 3),
                        "_intersection_bounds": list(overlap),
                        "_target_wkt": pre_wkt,
                    })
            pairs.append(record)
    pairs = sorted(
        pairs,
        key=lambda item: (
            item["path"], item["row"], item.get("pre_date") or "",
            item.get("post_date") or "",
        ),
    )
    grouped = {}
    for pair in pairs:
        grouped.setdefault((pair["path"], pair["row"]), []).append(pair)
    for candidates in grouped.values():
        candidates.sort(
            key=lambda item: (
                -min(item["pre_coverage_pct"], item["post_coverage_pct"])
                if item.get("pre_coverage_pct") is not None
                and item.get("post_coverage_pct") is not None
                else 1,
                -(item.get("overlap_pct_of_union") or 0),
                item.get("pre_date") or "",
                item.get("post_date") or "",
            )
        )
        for rank, pair in enumerate(candidates, start=1):
            pair["pair_rank"] = rank
            pair["recommended"] = rank == 1
    return sorted(
        pairs,
        key=lambda item: (
            item["path"], item["row"], item.get("pair_rank", 1),
            item.get("pre_date") or "", item.get("post_date") or "",
        ),
    )


def scene_report_rows(scenes: list[dict]) -> list[dict]:
    rows = []
    for scene in scenes:
        raster = scene.get("raster", {})
        rows.append({
            "scene_id": scene.get("scene_id"),
            "folder": scene.get("folder"),
            "date": scene.get("date"),
            "season": scene.get("season"),
            "path": scene.get("path"),
            "row": scene.get("row"),
            "sensor": scene.get("sensor"),
            "product_type": scene.get("product_type"),
            "dem_correction": scene.get("dem_correction"),
            "raster_files": " | ".join(scene.get("raster_files", [])),
            "width": raster.get("width"),
            "height": raster.get("height"),
            "bands": raster.get("bands"),
            "crs_present": bool(raster.get("crs_wkt")),
            "geotransform_present": bool(raster.get("geotransform")),
            "georeferencing_source": raster.get("georeferencing_source"),
            "warnings": " | ".join(scene.get("warnings", [])),
        })
    return rows


def pair_report_rows(pairs: list[dict]) -> list[dict]:
    keys = (
        "path", "row", "pre_scene_id", "post_scene_id", "pre_date", "post_date",
        "status", "pair_rank", "recommended", "pre_coverage_pct",
        "post_coverage_pct", "overlap_pct_of_union",
        "reason",
    )
    return [{key: pair.get(key) for key in keys} for pair in pairs]


def _source_dataset(scene: dict, temp_dir: Path):
    """Open a scene or compose NRSC band-separated TIFFs as B2/B3/B4."""
    files = scene["raster_files"]
    band_map = {int(k): v for k, v in scene.get("band_files", {}).items()}
    use_separate = set(band_map) == {2, 3, 4}
    source_paths = [band_map[index] for index in (2, 3, 4)] if use_separate else files[:1]
    first = gdal.Open(source_paths[0], gdal.GA_ReadOnly)
    if first is None:
        raise RuntimeError(f"GDAL could not open {source_paths[0]}")
    expected_gt = scene["raster"].get("geotransform")
    expected_wkt = scene["raster"].get("crs_wkt")
    needs_assignment = bool(expected_gt and expected_wkt and (
        first.GetGeoTransform(can_return_null=True) is None or not first.GetProjectionRef()
    ))

    working_paths = source_paths
    if needs_assignment:
        working_paths = []
        for index, path in enumerate(source_paths, start=1):
            vrt_path = temp_dir / f"{Path(path).stem}_georef_{index}.vrt"
            layer = gdal.Translate(str(vrt_path), path, format="VRT")
            if layer is None:
                raise RuntimeError(f"Could not create temporary georeferenced VRT for {path}")
            layer.SetGeoTransform(tuple(expected_gt))
            layer.SetProjection(expected_wkt)
            layer.FlushCache()
            layer = None
            working_paths.append(str(vrt_path))
    first = None

    if use_separate:
        vrt_path = temp_dir / f"{_safe_token(scene['scene_id'])}_stack.vrt"
        source = gdal.BuildVRT(str(vrt_path), working_paths, separate=True)
        if source is None:
            raise RuntimeError(f"Could not stack B2/B3/B4 for {scene['scene_id']}")
        source.FlushCache()
        source = None
        source = gdal.Open(str(vrt_path), gdal.GA_ReadOnly)
    else:
        source = gdal.Open(working_paths[0], gdal.GA_ReadOnly)
    if source is None:
        raise RuntimeError(f"Could not open scene dataset for {scene['scene_id']}")
    if not source.GetProjectionRef() or source.GetGeoTransform(can_return_null=True) is None:
        source = None
        raise ValueError(
            f"{scene['scene_id']} has no complete georeferencing. A path/row alone "
            "does not provide image-to-ground coordinates."
        )
    return source


def _is_north_up(gt) -> bool:
    return abs(gt[2]) < 1e-10 and abs(gt[4]) < 1e-10 and gt[1] > 0 and gt[5] < 0


def _grid_for_pair(pre: dict, post: dict, target_wkt: str, resolution: float | None):
    pre_gt = tuple(pre["raster"]["geotransform"])
    if not _is_north_up(pre_gt):
        raise ValueError("Rotated source grids are not supported for automatic shared-grid creation.")
    pre_srs, target_srs = _set_axis_order(osr.SpatialReference()), _set_axis_order(osr.SpatialReference())
    pre_srs.ImportFromWkt(pre["raster"]["crs_wkt"])
    target_srs.ImportFromWkt(target_wkt)
    same_crs = bool(pre_srs.IsSame(target_srs))

    if same_crs:
        default_rx, default_ry = abs(pre_gt[1]), abs(pre_gt[5])
    else:
        coordinate_transform = osr.CoordinateTransformation(pre_srs, target_srs)
        width, height = pre["raster"]["width"], pre["raster"]["height"]
        center_x, center_y = gdal.ApplyGeoTransform(pre_gt, width / 2, height / 2)
        x2, y2 = gdal.ApplyGeoTransform(pre_gt, width / 2 + 1, height / 2)
        x3, y3 = gdal.ApplyGeoTransform(pre_gt, width / 2, height / 2 + 1)
        p0 = coordinate_transform.TransformPoint(center_x, center_y)
        p1 = coordinate_transform.TransformPoint(x2, y2)
        p2 = coordinate_transform.TransformPoint(x3, y3)
        default_rx = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        default_ry = math.hypot(p2[0] - p0[0], p2[1] - p0[1])

    rx = float(resolution or default_rx)
    ry = float(resolution or default_ry)
    if rx <= 0 or ry <= 0:
        raise ValueError("Target pixel resolution must be positive.")
    if same_crs:
        anchor_x, anchor_y = pre_gt[0], pre_gt[3]
    else:
        # A common zero-origin lattice is used after reprojection.
        anchor_x = anchor_y = 0.0

    pre_bounds = _bounds(pre, target_wkt)
    post_bounds = _bounds(post, target_wkt)
    intersection = _intersection(pre_bounds, post_bounds) if pre_bounds and post_bounds else None
    if not intersection:
        raise ValueError("Pre and post footprints have no usable overlap in the target CRS.")
    left, bottom, right, top = intersection
    col0 = math.ceil((left - anchor_x) / rx - 1e-8)
    col1 = math.floor((right - anchor_x) / rx + 1e-8)
    row0 = math.ceil((anchor_y - top) / ry - 1e-8)
    row1 = math.floor((anchor_y - bottom) / ry + 1e-8)
    width, height = col1 - col0, row1 - row0
    if width <= 0 or height <= 0:
        raise ValueError("The intersection is smaller than one output pixel.")
    snapped = (
        anchor_x + col0 * rx,
        anchor_y - row1 * ry,
        anchor_x + col1 * rx,
        anchor_y - row0 * ry,
    )
    return snapped, width, height, rx, ry


def prepare_pair(
    pre: dict,
    post: dict,
    output_root: str | Path,
    *,
    target_crs: str | None = None,
    target_resolution: float | None = None,
    resampling: str = "bilinear",
    overwrite: bool = False,
) -> dict:
    """Write two rasters in a pair directory with identical CRS, extent, and grid.

    This performs georeferenced reprojection/resampling and common-footprint
    cropping. It does not estimate image tie points or prove sub-pixel
    co-registration.
    """
    if not pre.get("raster", {}).get("geotransform") or not post.get("raster", {}).get("geotransform"):
        raise ValueError("Both scenes need usable affine georeferencing before alignment.")
    if pre.get("warnings") or post.get("warnings"):
        blocking = [
            warning for warning in (pre.get("warnings", []) + post.get("warnings", []))
            if "ambiguous" in warning.lower() or "different dimensions" in warning.lower()
            or "could not be opened" in warning.lower()
        ]
        if blocking:
            raise ValueError("; ".join(blocking))

    target_wkt = target_crs or pre["raster"].get("crs_wkt")
    if not target_wkt:
        raise ValueError("Choose a target CRS (for example EPSG:32643).")
    if target_wkt.upper().startswith("EPSG:"):
        target = _set_axis_order(osr.SpatialReference())
        if target.ImportFromEPSG(int(target_wkt.split(":", 1)[1])) != 0:
            raise ValueError(f"Could not read target CRS {target_wkt}.")
        target_wkt = target.ExportToWkt()

    bounds, width, height, rx, ry = _grid_for_pair(pre, post, target_wkt, target_resolution)
    out_root = Path(output_root).expanduser().resolve()
    pair_key = (
        f"Pre_{pre['date'].replace('-', '')}_Post_{post['date'].replace('-', '')}_"
        f"{_safe_token(pre['scene_id'])}_{_safe_token(post['scene_id'])}"
    )
    pair_dir = out_root / f"Path_{int(pre['path']):04d}_Row_{int(pre['row']):04d}" / pair_key
    pre_output = pair_dir / f"Pre_{pre['date'].replace('-', '')}.tif"
    post_output = pair_dir / f"Post_{post['date'].replace('-', '')}.tif"
    if (pre_output.exists() or post_output.exists()) and not overwrite:
        raise FileExistsError(f"Output already exists: {pair_dir}. Enable overwrite or select another output folder.")
    pair_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="liss4_bulk_") as temp_name:
        temp_dir = Path(temp_name)
        for scene, destination in ((pre, pre_output), (post, post_output)):
            source = _source_dataset(scene, temp_dir)
            options = gdal.WarpOptions(
                format="GTiff",
                dstSRS=target_wkt,
                outputBounds=bounds,
                width=width,
                height=height,
                resampleAlg=resampling,
                dstNodata=OUTPUT_NODATA,
                multithread=True,
                creationOptions=[
                    "TILED=YES", "COMPRESS=DEFLATE",
                    "PREDICTOR=2", "BIGTIFF=IF_SAFER",
                ],
            )
            result = gdal.Warp(str(destination), source, options=options)
            source = None
            if result is None:
                raise RuntimeError(f"GDAL failed to align {scene['scene_id']}")
            result.FlushCache()
            result = None

    check_pre = gdal.Open(str(pre_output), gdal.GA_ReadOnly)
    check_post = gdal.Open(str(post_output), gdal.GA_ReadOnly)
    if check_pre is None or check_post is None:
        raise RuntimeError("Could not reopen one or both aligned outputs.")
    same_dimensions = (
        check_pre.RasterXSize == check_post.RasterXSize
        and check_pre.RasterYSize == check_post.RasterYSize
        and check_pre.RasterCount == check_post.RasterCount
    )
    pre_transform = check_pre.GetGeoTransform()
    post_transform = check_post.GetGeoTransform()
    same_grid = same_dimensions and all(
        math.isclose(a, b, rel_tol=0.0, abs_tol=1e-8)
        for a, b in zip(pre_transform, post_transform)
    )
    pre_srs, post_srs = _set_axis_order(osr.SpatialReference()), _set_axis_order(osr.SpatialReference())
    pre_srs.ImportFromWkt(check_pre.GetProjectionRef())
    post_srs.ImportFromWkt(check_post.GetProjectionRef())
    same_grid = same_grid and pre_srs.IsSame(post_srs)
    check_pre = check_post = None
    if not same_grid:
        raise RuntimeError("Aligned outputs failed the same-grid verification.")

    manifest = {
        "path": pre["path"],
        "row": pre["row"],
        "pre_scene": pre["scene_id"],
        "post_scene": post["scene_id"],
        "pre_date": pre["date"],
        "post_date": post["date"],
        "pre_source_folder": pre["folder"],
        "post_source_folder": post["folder"],
        "pre_source_files": pre["raster_files"],
        "post_source_files": post["raster_files"],
        "target_crs_wkt": target_wkt,
        "extent": list(bounds),
        "width": width,
        "height": height,
        "pixel_size": [rx, ry],
        "resampling": resampling,
        "outputs": [pre_output.name, post_output.name],
        "interpretation": (
            "Both files have a common output grid and common overlap extent. "
            "Grid equality does not establish sub-pixel image co-registration."
        ),
    }
    manifest_path = pair_dir / "pair_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "folder": str(pair_dir),
        "pre_output": str(pre_output),
        "post_output": str(post_output),
        "manifest": str(manifest_path),
        "width": width,
        "height": height,
        "pixel_size": [rx, ry],
    }


def write_csv(path: str | Path, rows: list[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def csv_bytes(rows: list[dict]) -> bytes:
    """Render report rows for a browser download without writing to disk."""
    import io

    stream = io.StringIO(newline="")
    if rows:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")

