---
mode: quick
quick_id: 260322-vre
description: "fix all listed bugs. I bug 5: bandownload_aria2d shouldn't exist. This plugin doesn't know about aria."
---

# Plan: Fix listed code bugs in `aer-search-aws-goes`

## Task 1: Fix Core Bugs in `core.py`
**Files**: `components/aer/search_aws_goes/core.py`
**Action**:
- Edit line 18 docstring: replace `bandownload_aria2d` with `band`.
- Edit `channels` population: If `file_channel_id` is found but `file_channel` resolves to `None`, log a warning with `structlog` (`logger.warning("Channel ID found in filename but missing from product channels", ...)`).
- Edit `except Exception as e:` block: catch `FileNotFoundError` instead of broad `Exception` to avoid swallowing auth/network errors.

## Task 2: Fix Test Bug in `test_core.py`
**Files**: `test/components/aer/search_aws_goes/test_core.py`
**Action**:
- Edit line 87: change `assert gdf.iloc[0]["cell_overlap_mode"] == "contains"` to dynamically check `assert gdf.iloc[0]["cell_overlap_mode"] == query.cell_overlap_mode`.

## Task 3: Verify the changes
**Action**:
- Run `pytest test/components/aer/search_aws_goes/test_core.py` to ensure all tests still pass with the new changes.
