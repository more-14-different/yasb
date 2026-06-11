import math
from typing import TypedDict, Literal

class RectDict(TypedDict):
    left: int
    top: int
    right: int
    bottom: int

class OutputRectDict(TypedDict):
    left: int
    top: int
    width: int
    height: int

Axis = Literal["Horizontal", "Vertical", "HorizontalAndVertical", "None"]

MIN_RATIO = 0.1
MAX_RATIO = 0.9
DEFAULT_RATIO = 0.5
DEFAULT_SECONDARY_RATIO = 0.25
MAX_RATIOS = 5

def calculate_layout(
    layout_type: str,
    work_area: RectDict,
    num_windows: int,
    layout_flip: Axis = "None",
    layout_options: dict | None = None
) -> list[OutputRectDict]:
    if num_windows <= 0:
        return []
    
    if layout_options is None:
        layout_options = {}

    if layout_type == "BSP":
        layouts = _calculate_bsp(work_area, num_windows, layout_flip, layout_options)
    elif layout_type == "Columns":
        layouts = _calculate_columns(work_area, num_windows, layout_flip, layout_options)
    elif layout_type == "Rows":
        layouts = _calculate_rows(work_area, num_windows, layout_flip, layout_options)
    elif layout_type == "VerticalStack":
        layouts = _calculate_vertical_stack(work_area, num_windows, layout_flip, layout_options)
    elif layout_type == "HorizontalStack":
        layouts = _calculate_horizontal_stack(work_area, num_windows, layout_flip, layout_options)
    elif layout_type == "UltrawideVerticalStack":
        layouts = _calculate_ultrawide_vertical_stack(work_area, num_windows, layout_flip, layout_options)
    elif layout_type == "RightMainVerticalStack":
        layouts = _calculate_right_main_vertical_stack(work_area, num_windows, layout_flip, layout_options)
    elif layout_type == "Grid":
        layouts = _calculate_grid(work_area, num_windows, layout_flip, layout_options)
    elif layout_type == "Scrolling":
        layouts = _calculate_scrolling(work_area, num_windows, layout_flip, layout_options)
    else:
        layouts = [{"left": work_area["left"], "top": work_area["top"], "right": work_area["right"], "bottom": work_area["bottom"]} for _ in range(num_windows)]

    # Convert Komorebi's right/bottom meaning width/height to actual python keys
    out = []
    for l in layouts:
        out.append({
            "left": l["left"],
            "top": l["top"],
            "width": l["right"],
            "height": l["bottom"]
        })
    return out

def _get_column_ratios(layout_options: dict) -> list[float]:
    ratios = layout_options.get("column_ratios", [])
    if not ratios:
        return []
    valid_ratios = []
    cumulative = 0.0
    for r in ratios[:MAX_RATIOS]:
        if r is not None:
            clamped = max(MIN_RATIO, min(MAX_RATIO, r))
            if cumulative + clamped < 1.0:
                valid_ratios.append(clamped)
                cumulative += clamped
            else:
                break
    return valid_ratios

def _get_row_ratios(layout_options: dict) -> list[float]:
    ratios = layout_options.get("row_ratios", [])
    if not ratios:
        return []
    valid_ratios = []
    cumulative = 0.0
    for r in ratios[:MAX_RATIOS]:
        if r is not None:
            clamped = max(MIN_RATIO, min(MAX_RATIO, r))
            if cumulative + clamped < 1.0:
                valid_ratios.append(clamped)
                cumulative += clamped
            else:
                break
    return valid_ratios

def _recursive_fibonacci(
    idx: int,
    count: int,
    area: RectDict,
    layout_flip: Axis,
    column_split_ratio: float,
    row_split_ratio: float,
) -> list[RectDict]:
    primary_resized_width = int(area["right"] * column_split_ratio)
    primary_resized_height = int(area["bottom"] * row_split_ratio)

    if layout_flip == "Horizontal":
        main_x = area["left"] + (area["right"] - primary_resized_width)
        alt_x = area["left"]
        alt_y = area["top"] + primary_resized_height
        main_y = area["top"]
    elif layout_flip == "Vertical":
        main_y = area["top"] + (area["bottom"] - primary_resized_height)
        alt_y = area["top"]
        main_x = area["left"]
        alt_x = area["left"] + primary_resized_width
    elif layout_flip == "HorizontalAndVertical":
        main_x = area["left"] + (area["right"] - primary_resized_width)
        alt_x = area["left"]
        main_y = area["top"] + (area["bottom"] - primary_resized_height)
        alt_y = area["top"]
    else: # None
        main_x = area["left"]
        alt_x = area["left"] + primary_resized_width
        main_y = area["top"]
        alt_y = area["top"] + primary_resized_height

    if count == 0:
        return []
    elif count == 1:
        return [{"left": area["left"], "top": area["top"], "right": area["right"], "bottom": area["bottom"]}]
    elif idx % 2 != 0:
        res = [{"left": area["left"], "top": main_y, "right": area["right"], "bottom": primary_resized_height}]
        res.extend(_recursive_fibonacci(
            idx + 1, count - 1,
            {"left": area["left"], "top": alt_y, "right": area["right"], "bottom": area["bottom"] - primary_resized_height},
            layout_flip, column_split_ratio, row_split_ratio
        ))
        return res
    else:
        res = [{"left": main_x, "top": area["top"], "right": primary_resized_width, "bottom": area["bottom"]}]
        res.extend(_recursive_fibonacci(
            idx + 1, count - 1,
            {"left": alt_x, "top": area["top"], "right": area["right"] - primary_resized_width, "bottom": area["bottom"]},
            layout_flip, column_split_ratio, row_split_ratio
        ))
        return res

def _columns_with_ratios(area: RectDict, length: int, ratios: list[float]) -> list[RectDict]:
    layouts = []
    left_offset = 0
    defined_ratios = len(ratios)

    for i in range(length):
        should_apply_ratio = (i < MAX_RATIOS) and (i < defined_ratios) and (i < length - 1)

        if should_apply_ratio:
            ratio = ratios[i]
            right_width = int(area["right"] * ratio)
        elif ratios:
            ratios_applied = min(i, defined_ratios, max(0, length - 1))
            used = sum(ratios[:ratios_applied])
            remaining_space = area["right"] - int(area["right"] * used)
            remaining_columns = length - ratios_applied
            right_width = remaining_space // remaining_columns if remaining_columns > 0 else remaining_space
        else:
            right_width = area["right"] // length

        layouts.append({
            "left": area["left"] + left_offset,
            "top": area["top"],
            "right": right_width,
            "bottom": area["bottom"],
        })
        left_offset += right_width

    total_width = sum(l["right"] for l in layouts)
    remainder = area["right"] - total_width
    if remainder > 0 and layouts:
        layouts[-1]["right"] += remainder

    return layouts

def _rows_with_ratios(area: RectDict, length: int, ratios: list[float]) -> list[RectDict]:
    layouts = []
    top_offset = 0
    defined_ratios = len(ratios)

    for i in range(length):
        should_apply_ratio = (i < MAX_RATIOS) and (i < defined_ratios) and (i < length - 1)

        if should_apply_ratio:
            ratio = ratios[i]
            bottom_height = int(area["bottom"] * ratio)
        elif ratios:
            ratios_applied = min(i, defined_ratios, max(0, length - 1))
            used = sum(ratios[:ratios_applied])
            remaining_space = area["bottom"] - int(area["bottom"] * used)
            remaining_rows = length - ratios_applied
            bottom_height = remaining_space // remaining_rows if remaining_rows > 0 else remaining_space
        else:
            bottom_height = area["bottom"] // length

        layouts.append({
            "left": area["left"],
            "top": area["top"] + top_offset,
            "right": area["right"],
            "bottom": bottom_height,
        })
        top_offset += bottom_height

    total_height = sum(l["bottom"] for l in layouts)
    remainder = area["bottom"] - total_height
    if remainder > 0 and layouts:
        layouts[-1]["bottom"] += remainder

    return layouts

def _columns_reverse(layouts: list[RectDict]):
    if not layouts: return
    n = len(layouts)
    layouts[n - 1]["left"] = layouts[0]["left"]
    for i in range(n - 2, -1, -1):
        layouts[i]["left"] = layouts[i + 1]["left"] + layouts[i + 1]["right"]

def _rows_reverse(layouts: list[RectDict]):
    if not layouts: return
    n = len(layouts)
    layouts[n - 1]["top"] = layouts[0]["top"]
    for i in range(n - 2, -1, -1):
        layouts[i]["top"] = layouts[i + 1]["top"] + layouts[i + 1]["bottom"]

def _calculate_bsp(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    col_ratios = _get_column_ratios(layout_options)
    row_ratios = _get_row_ratios(layout_options)
    col_split = col_ratios[0] if col_ratios else DEFAULT_RATIO
    row_split = row_ratios[0] if row_ratios else DEFAULT_RATIO
    return _recursive_fibonacci(0, num_windows, work_area, layout_flip, col_split, row_split)

def _calculate_columns(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    ratios = _get_column_ratios(layout_options)
    layouts = _columns_with_ratios(work_area, num_windows, ratios)
    if layout_flip in ("Horizontal", "HorizontalAndVertical") and num_windows >= 2:
        _columns_reverse(layouts)
    return layouts

def _calculate_rows(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    ratios = _get_row_ratios(layout_options)
    layouts = _rows_with_ratios(work_area, num_windows, ratios)
    if layout_flip in ("Vertical", "HorizontalAndVertical") and num_windows >= 2:
        _rows_reverse(layouts)
    return layouts

def _calculate_vertical_stack(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    layouts = []
    col_ratios = _get_column_ratios(layout_options)
    primary_ratio = col_ratios[0] if col_ratios else DEFAULT_RATIO

    primary_width = work_area["right"] if num_windows == 1 else int(work_area["right"] * primary_ratio)
    
    if num_windows >= 1:
        layouts.append({
            "left": work_area["left"],
            "top": work_area["top"],
            "right": primary_width,
            "bottom": work_area["bottom"]
        })
        if num_windows > 1:
            row_ratios = _get_row_ratios(layout_options)
            layouts.extend(_rows_with_ratios({
                "left": work_area["left"] + primary_width,
                "top": work_area["top"],
                "right": work_area["right"] - primary_width,
                "bottom": work_area["bottom"]
            }, num_windows - 1, row_ratios))

    if layout_flip in ("Horizontal", "HorizontalAndVertical") and num_windows >= 2:
        primary = layouts[0]
        rest = layouts[1:]
        for rect in rest:
            rect["left"] = primary["left"]
        primary["left"] = rest[0]["left"] + rest[0]["right"]

    if layout_flip in ("Vertical", "HorizontalAndVertical") and num_windows >= 3:
        sub = layouts[1:]
        _rows_reverse(sub)
        layouts[1:] = sub

    return layouts

def _calculate_right_main_vertical_stack(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    layouts = []
    col_ratios = _get_column_ratios(layout_options)
    primary_ratio = col_ratios[0] if col_ratios else DEFAULT_RATIO

    primary_width = work_area["right"] if num_windows == 1 else int(work_area["right"] * primary_ratio)
    primary_left = 0 if num_windows == 1 else work_area["right"] - primary_width
    
    if num_windows >= 1:
        layouts.append({
            "left": work_area["left"] + primary_left,
            "top": work_area["top"],
            "right": primary_width,
            "bottom": work_area["bottom"]
        })
        if num_windows > 1:
            row_ratios = _get_row_ratios(layout_options)
            layouts.extend(_rows_with_ratios({
                "left": work_area["left"],
                "top": work_area["top"],
                "right": primary_left,
                "bottom": work_area["bottom"]
            }, num_windows - 1, row_ratios))

    if layout_flip in ("Horizontal", "HorizontalAndVertical") and num_windows >= 2:
        primary = layouts[0]
        rest = layouts[1:]
        primary["left"] = rest[0]["left"]
        for rect in rest:
            rect["left"] = primary["left"] + primary["right"]

    if layout_flip in ("Vertical", "HorizontalAndVertical") and num_windows >= 3:
        sub = layouts[1:]
        _rows_reverse(sub)
        layouts[1:] = sub

    return layouts

def _calculate_horizontal_stack(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    layouts = []
    row_ratios = _get_row_ratios(layout_options)
    primary_ratio = row_ratios[0] if row_ratios else DEFAULT_RATIO

    primary_height = work_area["bottom"] if num_windows == 1 else int(work_area["bottom"] * primary_ratio)
    
    if num_windows >= 1:
        layouts.append({
            "left": work_area["left"],
            "top": work_area["top"],
            "right": work_area["right"],
            "bottom": primary_height
        })
        if num_windows > 1:
            col_ratios = _get_column_ratios(layout_options)
            layouts.extend(_columns_with_ratios({
                "left": work_area["left"],
                "top": work_area["top"] + primary_height,
                "right": work_area["right"],
                "bottom": work_area["bottom"] - primary_height
            }, num_windows - 1, col_ratios))

    if layout_flip in ("Vertical", "HorizontalAndVertical") and num_windows >= 2:
        primary = layouts[0]
        rest = layouts[1:]
        for rect in rest:
            rect["top"] = primary["top"]
        primary["top"] = rest[0]["top"] + rest[0]["bottom"]

    if layout_flip in ("Horizontal", "HorizontalAndVertical") and num_windows >= 3:
        sub = layouts[1:]
        _columns_reverse(sub)
        layouts[1:] = sub

    return layouts

def _calculate_ultrawide_vertical_stack(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    layouts = []
    ratios = _get_column_ratios(layout_options)
    primary_ratio = ratios[0] if len(ratios) > 0 else DEFAULT_RATIO
    secondary_ratio = ratios[1] if len(ratios) > 1 else DEFAULT_SECONDARY_RATIO

    primary_width = work_area["right"] if num_windows == 1 else int(work_area["right"] * primary_ratio)
    
    if num_windows == 1:
        secondary_width = 0
    elif num_windows == 2:
        secondary_width = work_area["right"] - primary_width
    else:
        secondary_width = int(work_area["right"] * secondary_ratio)

    if num_windows == 1:
        primary_left, secondary_left, stack_left = work_area["left"], 0, 0
    elif num_windows == 2:
        primary_left = work_area["left"] + secondary_width
        secondary_left = work_area["left"]
        stack_left = 0
    else:
        primary_left = work_area["left"] + secondary_width
        secondary_left = work_area["left"]
        stack_left = work_area["left"] + primary_width + secondary_width

    if num_windows >= 1:
        layouts.append({
            "left": primary_left,
            "top": work_area["top"],
            "right": primary_width,
            "bottom": work_area["bottom"]
        })
        if num_windows >= 2:
            layouts.append({
                "left": secondary_left,
                "top": work_area["top"],
                "right": secondary_width,
                "bottom": work_area["bottom"]
            })
            if num_windows > 2:
                tertiary_width = work_area["right"] - primary_width - secondary_width
                row_ratios = _get_row_ratios(layout_options)
                layouts.extend(_rows_with_ratios({
                    "left": stack_left,
                    "top": work_area["top"],
                    "right": tertiary_width,
                    "bottom": work_area["bottom"]
                }, num_windows - 2, row_ratios))

    if layout_flip in ("Horizontal", "HorizontalAndVertical"):
        if num_windows == 2:
            primary = layouts[0]
            secondary = layouts[1]
            primary["left"] = secondary["left"]
            secondary["left"] = primary["left"] + primary["right"]
        elif num_windows >= 3:
            primary = layouts[0]
            secondary = layouts[1]
            tertiary = layouts[2:]
            for rect in tertiary:
                rect["left"] = secondary["left"]
            primary["left"] = tertiary[0]["left"] + tertiary[0]["right"]
            secondary["left"] = primary["left"] + primary["right"]

    if layout_flip in ("Vertical", "HorizontalAndVertical") and num_windows >= 4:
        sub = layouts[2:]
        _rows_reverse(sub)
        layouts[2:] = sub

    return layouts

def _calculate_grid(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    layouts = [{"left":0, "top":0, "right":0, "bottom":0} for _ in range(num_windows)]
    
    grid_opts = layout_options.get("grid", {})
    row_constraint = grid_opts.get("rows")
    col_ratios = _get_column_ratios(layout_options)
    defined_ratios = len(col_ratios)

    if row_constraint is not None and row_constraint > 0:
        num_cols = int(math.ceil(num_windows / row_constraint))
    else:
        num_cols = int(math.ceil(math.sqrt(num_windows)))

    if num_cols <= 0:
        return layouts

    col_widths = []
    col_lefts = []
    current_left = work_area["left"]

    for col in range(num_cols):
        if col_ratios:
            should_apply_ratio = col < MAX_RATIOS and col < defined_ratios and col < num_cols - 1
            if should_apply_ratio:
                width = int(work_area["right"] * col_ratios[col])
            else:
                ratios_applied = min(defined_ratios, max(0, num_cols - 1))
                used = sum(col_ratios[:ratios_applied])
                remaining_space = work_area["right"] - int(work_area["right"] * used)
                remaining_cols = num_cols - ratios_applied
                width = remaining_space // remaining_cols if remaining_cols > 0 else remaining_space
        else:
            width = work_area["right"] // num_cols

        col_lefts.append(current_left)
        col_widths.append(width)
        current_left += width

    total_width = sum(col_widths)
    width_remainder = work_area["right"] - total_width
    if width_remainder > 0 and col_widths:
        col_widths[-1] += width_remainder

    flipped_col_lefts = []
    if layout_flip in ("Horizontal", "HorizontalAndVertical"):
        flipped_col_lefts = [0] * num_cols
        fl = work_area["left"]
        for i in range(num_cols - 1, -1, -1):
            flipped_col_lefts[i] = fl
            fl += col_widths[i]

    win_idx = 0
    for col in range(num_cols):
        remaining_windows = num_windows - win_idx
        remaining_columns = num_cols - col

        if remaining_columns <= 0 or remaining_windows <= 0:
            break

        if row_constraint is not None and row_constraint > 0:
            num_rows_in_this_col = min(remaining_windows // remaining_columns, row_constraint)
        else:
            num_rows_in_this_col = remaining_windows // remaining_columns
        
        if num_rows_in_this_col <= 0:
            num_rows_in_this_col = 1
            
        base_height = work_area["bottom"] // num_rows_in_this_col
        height_remainder = work_area["bottom"] - (base_height * num_rows_in_this_col)

        win_width = col_widths[col]
        col_left = col_lefts[col]

        for row in range(num_rows_in_this_col):
            if win_idx < num_windows:
                is_last_row = (row == num_rows_in_this_col - 1)
                win_height = base_height + height_remainder if is_last_row else base_height

                left = col_left
                top = work_area["top"] + (base_height * row)

                if layout_flip == "Horizontal":
                    left = flipped_col_lefts[col]
                elif layout_flip == "Vertical":
                    top = work_area["top"] if is_last_row else work_area["top"] + work_area["bottom"] - (base_height * (row + 1))
                elif layout_flip == "HorizontalAndVertical":
                    left = flipped_col_lefts[col]
                    top = work_area["top"] if is_last_row else work_area["top"] + work_area["bottom"] - (base_height * (row + 1))

                layouts[win_idx] = {
                    "left": left,
                    "top": top,
                    "right": win_width,
                    "bottom": win_height
                }
                win_idx += 1

    return layouts

def _calculate_scrolling(work_area: RectDict, num_windows: int, layout_flip: Axis, layout_options: dict):
    layouts = []
    scrolling_opts = layout_options.get("scrolling", {})
    column_count = scrolling_opts.get("columns", 3)
    
    column_width = work_area["right"] // min(column_count, num_windows)
    visible_columns = work_area["right"] // column_width
    first_visible = 0
    
    for i in range(num_windows):
        position = i - first_visible
        left = work_area["left"] + (position * column_width)
        layouts.append({
            "left": left,
            "top": work_area["top"],
            "right": column_width,
            "bottom": work_area["bottom"]
        })
        
    width_remainder = work_area["right"] - (column_width * visible_columns)
    if width_remainder > 0:
        last_visible_idx = min(first_visible + visible_columns - 1, num_windows - 1)
        if 0 <= last_visible_idx < len(layouts):
            layouts[last_visible_idx]["right"] += width_remainder

    return layouts
